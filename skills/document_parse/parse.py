"""document_parse skill 主入口。

流程:
1. router 选 executor
2. executor.extract(source) → raw_data
3. 应用 3-layer 业务规则(KB → hard_code → llm)→ business_judgement
4. 组装 document_parse.v1
5. schema 校验
6. 返回 DocumentParseResult
"""

import os
import time
import uuid
from typing import Any, Dict, Optional

from skills.document_parse.router import (
    route,
    build_default_executors,
    NoExecutorError,
)
from skills.document_parse.validator import validate

SCHEMA_VERSION = "document_parse.v1"


def parse(
    source: str,
    parse_intent: str = "structured_business_fields",
    run_id: Optional[str] = None,
    knowledge_base=None,  # 后续 Step 5 接 business_rules/document_parse/*.md
    llm_extractor=None,  # 后续接 common.llm_adapter
) -> Dict[str, Any]:
    """主入口。

    Args:
        source: file_path 或 url
        parse_intent: "structured_business_fields" | "raw_content" | "metadata_only"
        run_id: 可选,自动生成 UUID
        knowledge_base: 知识库(后续接 business_rules/document_parse/*.md)
        llm_extractor: LLM fallback(后续接 common.llm_adapter)

    Returns:
        document_parse.v1
    """
    start = time.time()
    rid = run_id or f"run-{uuid.uuid4()}"
    is_url = bool(source) and source.startswith(("http://", "https://"))

    # Step 1: 路由
    try:
        executor, file_type = route(source)
    except NoExecutorError as e:
        return _build_blocked_result(
            rid=rid,
            source=source,
            source_type="url" if is_url else "file",
            file_type="unknown",
            executor_used="unknown",
            elapsed=time.time() - start,
            reason="parse_error",
            error=str(e),
            impl_status="implemented",
        )

    source_type = "url" if file_type == "url" else "file"

    # Step 2: executor 提取
    executor_result = executor.extract(source)

    # Step 3: 组装结果
    elapsed = time.time() - start

    if executor_result["status"] != "success":
        impl_status = executor_result.get("implementation_status", "implemented")
        return _build_blocked_result(
            rid=rid,
            source=source,
            source_type=source_type,
            file_type=file_type,
            executor_used=executor.name,
            elapsed=elapsed,
            reason="executor_not_implemented" if impl_status == "stub" else "parse_error",
            error=executor_result.get("error"),
            impl_status=impl_status,
        )

    # Step 4: 应用业务规则(3-layer fallback)
    business_judgement = _apply_business_rules(
        raw_data=executor_result["raw_data"],
        knowledge_base=knowledge_base,
        llm_extractor=llm_extractor,
    )

    # Step 5: 组装 document_parse.v1
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "status": "success" if business_judgement["confidence"] != "low" else "needs_review",
        "source_type": source_type,
        "source_path": source,
        "file_type": file_type,
        "executor_used": executor.name,
        "executor_implementation_status": executor_result["implementation_status"],
        "parse_intent": parse_intent,
        "extracted_data": executor_result["raw_data"],
        "business_judgement": business_judgement,
        "validation": {
            "schema_valid": True,  # 下面校验后会更新
            "required_fields_present": True,
            "missing_fields": [],
            "warnings": [],
        },
        "blockers": [],
        "reason": "success",
        "knowledge_base_used": business_judgement["rule_source"] == "knowledge_base",
        "elapsed_seconds": round(elapsed, 3),
    }

    # Step 6: schema 校验
    is_valid, errors = validate(result)
    result["validation"]["schema_valid"] = is_valid
    if not is_valid:
        result["validation"]["warnings"].extend(errors)
        result["status"] = "blocked"
        result["reason"] = "schema_invalid"

    return result


def _apply_business_rules(
    raw_data: Dict[str, Any],
    knowledge_base=None,
    llm_extractor=None,
) -> Dict[str, Any]:
    """3-layer fallback: 知识库(L1) → 硬编码(L2) → LLM(L3)。

    MVP 实现: 只用 L2 硬编码关键词分类。L1 和 L3 留接口,后续 Step 5 接入。
    """
    # L1: 知识库匹配
    if knowledge_base is not None:
        try:
            judgement = knowledge_base.match(raw_data)
            if judgement is not None:
                return judgement
        except Exception:
            pass  # 知识库失败,降级 L2

    # L2: 硬编码 fallback
    judgement = _hardcoded_classify(raw_data)
    if judgement["confidence"] == "high":
        return judgement

    # L3: LLM 提取(暂未实现,留接口)
    if llm_extractor is not None:
        try:
            return llm_extractor(raw_data)
        except Exception:
            pass

    # L3 也失败,返回 L2 结果(confidence=low → needs_review)
    return judgement


def _hardcoded_classify(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """硬编码 fallback 分类(MVP 用,后续迁到 business_rules/document_parse/classification.md)。"""
    text = raw_data.get("raw_text", "")

    category = "其他"
    confidence = "medium"
    rule_source = "hard_code"

    if "招标公告" in text or "采购公告" in text or "公开招标" in text:
        category = "招标公告"
        confidence = "high"
    elif "投标文件" in text or "投标书" in text or "投标响应" in text:
        category = "投标文件"
        confidence = "high"
    elif "合同" in text and ("签订" in text or "签署" in text or "签订合同" in text):
        category = "合同文件"
        confidence = "medium"
    elif "报名" in text or "报名表" in text or "登记" in text:
        category = "报名材料"
        confidence = "medium"

    return {
        "category": category,
        "extracted_fields": {},  # MVP 不做字段抽取(Step 5 加)
        "confidence": confidence,
        "rule_source": rule_source,
    }


def _build_blocked_result(
    rid: str,
    source: str,
    source_type: str,
    file_type: str,
    executor_used: str,
    elapsed: float,
    reason: str,
    error: Optional[str],
    impl_status: str,
) -> Dict[str, Any]:
    """构造 blocked 结果(确保 schema 有效)。"""
    warnings = [error] if error else []
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "status": "blocked",
        "source_type": source_type,
        "source_path": source,
        "file_type": file_type,
        "executor_used": executor_used,
        "executor_implementation_status": impl_status,
        "parse_intent": "structured_business_fields",
        "extracted_data": {
            "paragraphs": [],
            "tables": [],
            "metadata": {},
            "raw_text": "",
        },
        "business_judgement": {
            "category": "其他",
            "extracted_fields": {},
            "confidence": "low",
            "rule_source": "none",
        },
        "validation": {
            "schema_valid": True,
            "required_fields_present": True,
            "missing_fields": [],
            "warnings": warnings,
        },
        "blockers": warnings,
        "reason": reason,
        "knowledge_base_used": False,
        "elapsed_seconds": round(elapsed, 3),
    }
