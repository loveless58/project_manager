"""document_parse LLM 提取器(L3 fallback)。

设计:
- KB(L1) / 硬编码(L2) 都低置信度时降级到 LLM
- LLM endpoint 必须由统一 provider 配置显式提供
- 直接调 requests(不带 APIAdapter,因为它不支持 max_tokens,
  模型会花 token 在 thinking 上导致 content 被截断)
- prompt: 给 LLM 类别列表 + 文本 + 文件名,要求返回 JSON
- 解析 LLM 输出,失败兜底为 low

调用约定:
    extractor = make_llm_extractor()
    parse(path, llm_extractor=extractor)

环境变量覆盖:
    PROJECT_MANAGER_LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_MAX_TOKENS
"""

import json
import os
import re
from typing import Any, Dict, Optional

import requests

from common.provider_config import resolve_llm_base_url


# 模型参数仍允许按调用方覆盖；endpoint 由统一 provider 配置解析。
DEFAULT_API_KEY = os.getenv("LLM_API_KEY")
DEFAULT_MODEL = os.getenv("LLM_MODEL", "minimax-m3-mxfp8")
DEFAULT_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
DEFAULT_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))

# 业务类别清单(必须跟 KB 一致)
CATEGORIES = ["招标公告", "投标文件", "合同文件", "报名材料", "其他"]

PROMPT_TEMPLATE = """你是文档分类助手。请根据以下信息,选择最合适的类别。

可选类别: {categories}

文件名: {filename}

文件内容(前 {max_chars} 字):
\"\"\"
{text}
\"\"\"

要求:
1. 从列表里选一个最匹配的类别(必须是列表里的)
2. 置信度: high / medium / low
3. 只返回 JSON,不要其他文字
4. 格式: {{"category": "...", "confidence": "high|medium|low", "reasoning": "..."}}
"""


def _extract_json(text):
    """从 LLM 输出里提取 JSON(可能含 thinking 或 markdown 代码块)。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    return None


def _call_llm(base_url, api_key, model, messages, max_tokens, temperature, timeout=60):
    """直接调 GPUStack(OpenAI 兼容)API,带 max_tokens。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
    tokens = data.get("usage", {}).get("total_tokens", 0)
    finish_reason = (data.get("choices", [{}])[0].get("finish_reason") or "unknown")

    return {
        "text": text,
        "tokens": tokens,
        "finish_reason": finish_reason,
        "raw": data,
    }


def make_llm_extractor(
    base_url=None,
    api_key=None,
    model=DEFAULT_MODEL,
    max_text_chars=2000,
    max_tokens=DEFAULT_MAX_TOKENS,
    temperature=DEFAULT_TEMPERATURE,
):
    """构造一个 llm_extractor callable。"""
    if api_key is None:
        api_key = DEFAULT_API_KEY or os.getenv("LLM_API_KEY")
    if not api_key:
        raise ValueError(
            "LLM_API_KEY 未设置；请通过运行环境注入或显式传 api_key 参数。"
        )
    base_url = resolve_llm_base_url(base_url, required=True)

    def llm_extractor(raw_data):
        text = (raw_data.get("raw_text") or "")[:max_text_chars]
        filename = raw_data.get("filename", "") or "(未知)"

        prompt = PROMPT_TEMPLATE.format(
            categories=", ".join(CATEGORIES),
            filename=filename,
            text=text or "(无内容)",
            max_chars=max_text_chars,
        )

        messages = [
            {"role": "system", "content": "你是文档分类助手,只返回 JSON。"},
            {"role": "user", "content": prompt},
        ]

        try:
            r = _call_llm(base_url, api_key, model, messages, max_tokens, temperature)
            parsed = _extract_json(r["text"])

            if parsed is None:
                return {
                    "category": "其他",
                    "extracted_fields": {},
                    "confidence": "low",
                    "rule_source": "llm",
                    "llm_raw_response": (r["text"] or "")[:500],
                    "llm_finish_reason": r["finish_reason"],
                    "llm_tokens_used": r["tokens"],
                    "llm_error": "json_parse_failed",
                }

            category = parsed.get("category", "其他")
            if category not in CATEGORIES:
                category = "其他"

            confidence = parsed.get("confidence", "low")
            if confidence not in ("high", "medium", "low"):
                confidence = "low"

            return {
                "category": category,
                "extracted_fields": {},
                "confidence": confidence,
                "rule_source": "llm",
                "llm_reasoning": parsed.get("reasoning", ""),
                "llm_tokens_used": r["tokens"],
                "llm_finish_reason": r["finish_reason"],
            }
        except Exception as e:
            return {
                "category": "其他",
                "extracted_fields": {},
                "confidence": "low",
                "rule_source": "llm",
                "llm_error": str(e),
            }

    return llm_extractor


_default_extractor = None


def get_default_llm_extractor():
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = make_llm_extractor()
    return _default_extractor
