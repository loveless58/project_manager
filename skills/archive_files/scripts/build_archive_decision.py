"""build_archive_decision.py — 评估文件归档动作

提取自 tools/data_cleaning_tools.py(Phase 2 拆分)。

公开 API:
- build_archive_action(...)         构造 archive_action.v1 dict
- business_phase_from_path(...)     从路径识别业务阶段

依赖: business_rules.archive_decision.evaluate_archive_decision

三层 fallback(v0.2.0):
1. 业务知识库(business_rules/archive_files/*.md)— 优先
2. 硬编码规则(evaluate_archive_decision)— 次选
3. LLM 通用兜底(仅在知识库缺失时)— 兜底

边界约束: LLM 通用兜底仅在 reason ∈ {knowledge_base_missing, knowledge_base_no_match}
时触发; knowledge_base_low_confidence 不触发 LLM 兜底(避免幻觉)。
"""
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from business_rules.archive_decision import evaluate_archive_decision
from common.provider_config import resolve_llm_base_url


# 业务知识库物理路径(business_rules/archive_files/)
BUSINESS_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "business_rules" / "archive_files"

# 模型和超时可按归档能力覆盖；endpoint 由统一 provider 配置解析。
_LLM_MODEL = os.getenv("ARCHIVE_LLM_MODEL", "openai/minimax-m3")
_LLM_TIMEOUT = int(os.getenv("ARCHIVE_LLM_TIMEOUT", "10"))
_LLM_REQUEST_FAILED = "ARCHIVE.LLM.REQUEST_FAILED"
_LLM_REQUEST_FAILED_MESSAGE = "LLM request failed."


def _path_parts(source_path: str) -> List[str]:
    """把路径按 / 或 \\ 拆成段。"""
    return [part for part in re.split(r"[\\/]+", os.path.normpath(str(source_path))) if part]


def business_phase_from_path(source_path: str) -> str:
    """从路径里识别业务阶段。

    返回 4 阶段之一：项目投标 / 项目弃标 / 项目丢标 / 项目执行。
    路径里没有这 4 个 phase 时返回空字符串。
    """
    parts = _path_parts(source_path)
    for phase in ("项目投标", "项目弃标", "项目丢标", "项目执行"):
        if phase in parts:
            return phase
    return ""


def _read_knowledge_base() -> str:
    """读取业务知识库的所有 markdown 内容。

    返回：合并后的 markdown 文本（用文件路径作为分隔符）。
    业务知识库不存在时返回空字符串。
    """
    if not BUSINESS_KNOWLEDGE_DIR.exists():
        return ""
    chunks = []
    for md_file in sorted(BUSINESS_KNOWLEDGE_DIR.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            chunks.append(f"--- {md_file.name} ---\n{content}")
        except Exception:
            continue
    return "\n\n".join(chunks)


def _search_knowledge_base(source_file: str, hard_phase: Optional[str]) -> Dict[str, Any]:
    """用 LLM 检索业务知识库，判断业务阶段。

    返回：
    - knowledge_base_used: bool
    - phase: 业务阶段（"unknown" 表示无法判断）
    - confidence: 置信度 (0.0 ~ 1.0)
    - reason: 决策原因
        - success: 知识库命中
        - knowledge_base_missing: 知识库不存在 / 读取失败
        - knowledge_base_no_match: 知识库存在但 LLM 无法匹配
        - knowledge_base_low_confidence: LLM 置信度低
    - reasoning: LLM 推理说明

    注意：仅当 LLM 高置信度（>= 0.7）时才返回 success；
    低置信度返回 knowledge_base_low_confidence（不触发 LLM 通用兜底）。
    """
    kb_content = _read_knowledge_base()
    if not kb_content:
        return {
            "knowledge_base_used": False,
            "phase": "unknown",
            "confidence": 0.0,
            "reason": "knowledge_base_missing",
            "reasoning": "业务知识库目录不存在或为空",
        }

    try:
        api_base = resolve_llm_base_url(required=True)
        import litellm

        prompt = f"""你是归档决策助手。根据文件名/路径和业务知识库判断归档阶段。

业务知识库：
{kb_content}

文件路径：{source_file}
硬约束判断的阶段：{hard_phase or "未确定"}

请判断归档阶段：项目投标 / 项目弃标 / 项目丢标 / 项目执行 / unknown。

输出格式（严格遵守）：
PHASE: <阶段>
CONFIDENCE: <0.0 ~ 1.0>
REASONING: <一句话理由>
"""

        response = litellm.completion(
            model=_LLM_MODEL,
            api_base=api_base,
            messages=[{"role": "user", "content": prompt}],
            timeout=_LLM_TIMEOUT,
        )
        answer = response.choices[0].message.content.strip()

        # 解析 LLM 输出
        phase = "unknown"
        confidence = 0.0
        reasoning = answer
        for line in answer.split("\n"):
            line = line.strip()
            if line.startswith("PHASE:"):
                p = line[len("PHASE:"):].strip()
                if p in ("项目投标", "项目弃标", "项目丢标", "项目执行"):
                    phase = p
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = float(line[len("CONFIDENCE:"):].strip())
                except ValueError:
                    confidence = 0.0
            elif line.startswith("REASONING:"):
                reasoning = line[len("REASONING:"):].strip()

        # 置信度分级
        if phase == "unknown" or confidence < 0.3:
            reason = "knowledge_base_no_match"
        elif confidence < 0.7:
            reason = "knowledge_base_low_confidence"
        else:
            reason = "success"

        return {
            "knowledge_base_used": True,
            "phase": phase,
            "confidence": confidence,
            "reason": reason,
            "reasoning": reasoning,
        }
    except ImportError:
        return {
            "knowledge_base_used": False,
            "phase": "unknown",
            "confidence": 0.0,
            "reason": "knowledge_base_missing",
            "reasoning": "litellm 未安装",
        }
    except Exception:
        return {
            "knowledge_base_used": False,
            "phase": "unknown",
            "confidence": 0.0,
            "reason": "knowledge_base_missing",
            "reasoning": _LLM_REQUEST_FAILED_MESSAGE,
            "llm_error": _LLM_REQUEST_FAILED,
        }


def _llm_general_fallback(source_file: str, hard_phase: Optional[str]) -> Dict[str, Any]:
    """LLM 通用兜底——直接调 LLM（不通过业务知识库）。

    注意：仅在 business_rules + 业务知识库都判断不出来时触发。
    这是 cold-start fallback，不是主路径。
    """
    try:
        api_base = resolve_llm_base_url(required=True)
        import litellm

        prompt = f"""你是归档决策助手。根据文件名/路径判断归档阶段（不需要业务知识库）。

文件路径：{source_file}
硬约束判断的阶段：{hard_phase or "未确定"}

请判断归档阶段：项目投标 / 项目弃标 / 项目丢标 / 项目执行 / unknown。

输出格式：
PHASE: <阶段>
CONFIDENCE: <0.0 ~ 1.0>
REASONING: <一句话理由>
"""

        response = litellm.completion(
            model=_LLM_MODEL,
            api_base=api_base,
            messages=[{"role": "user", "content": prompt}],
            timeout=_LLM_TIMEOUT,
        )
        answer = response.choices[0].message.content.strip()

        phase = "unknown"
        confidence = 0.0
        reasoning = answer
        for line in answer.split("\n"):
            line = line.strip()
            if line.startswith("PHASE:"):
                p = line[len("PHASE:"):].strip()
                if p in ("项目投标", "项目弃标", "项目丢标", "项目执行"):
                    phase = p
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = float(line[len("CONFIDENCE:"):].strip())
                except ValueError:
                    confidence = 0.0
            elif line.startswith("REASONING:"):
                reasoning = line[len("REASONING:"):].strip()

        return {
            "phase": phase,
            "confidence": confidence,
            "llm_fallback_used": True,
            "reasoning": reasoning,
        }
    except Exception:
        return {
            "phase": "unknown",
            "confidence": 0.0,
            "llm_fallback_used": False,
            "llm_error": _LLM_REQUEST_FAILED,
            "reasoning": _LLM_REQUEST_FAILED_MESSAGE,
        }


def _three_layer_fallback(
    source_file: str,
    hard_decision: Dict[str, Any],
) -> Dict[str, Any]:
    """三层 fallback 决议：
    1. 业务知识库高置信度 → 用知识库结果（reason=success）
    2. 业务知识库低置信度 → 用硬编码（reason=knowledge_base_low_confidence）
       注意：低置信度不触发 LLM 通用兜底
    3. 业务知识库缺失/无结果 + 硬编码置信度不够 → LLM 通用兜底
    """
    hard_phase = hard_decision.get("archive_phase")
    hard_confidence = hard_decision.get("confidence", 0.0)

    # 1. 查业务知识库
    kb_result = _search_knowledge_base(source_file, hard_phase)

    # 知识库高置信度 → 直接用
    if kb_result["reason"] == "success" and kb_result["phase"] != "unknown":
        return {
            "phase": kb_result["phase"],
            "confidence": kb_result["confidence"],
            "reason": "success",
            "knowledge_base_used": True,
            "kb_reasoning": kb_result.get("reasoning", ""),
            "llm_fallback_used": False,
        }

    # 知识库低置信度 → 走硬编码（不触发 LLM 兜底）
    if kb_result["reason"] == "knowledge_base_low_confidence":
        return {
            "phase": hard_phase,
            "confidence": hard_confidence,
            "reason": "knowledge_base_low_confidence",
            "knowledge_base_used": True,
            "kb_reasoning": kb_result.get("reasoning", ""),
            "llm_fallback_used": False,
        }

    # 知识库缺失 / 无结果（kb_missing / kb_no_match）
    # 2. 硬编码置信度足够 → 用硬编码
    if hard_phase and hard_confidence >= 0.5:
        return {
            "phase": hard_phase,
            "confidence": hard_confidence,
            "reason": "success",
            "knowledge_base_used": True,
            "kb_reasoning": kb_result.get("reasoning", ""),
            "llm_fallback_used": False,
        }

    # 3. LLM 通用兜底（仅在 kb_missing / kb_no_match 时）
    if kb_result["reason"] in ("knowledge_base_missing", "knowledge_base_no_match"):
        llm_result = _llm_general_fallback(source_file, hard_phase)
        return {
            "phase": llm_result.get("phase", hard_phase or "unknown"),
            "confidence": llm_result.get("confidence", hard_confidence),
            "reason": "success" if llm_result.get("phase") and llm_result["phase"] != "unknown" else "knowledge_base_low_confidence",
            "knowledge_base_used": False,
            "kb_reasoning": kb_result.get("reasoning", ""),
            "llm_fallback_used": llm_result.get("llm_fallback_used", False),
            "llm_error": llm_result.get("llm_error"),
        }

    # 所有路径都失败
    return {
        "phase": hard_phase or "unknown",
        "confidence": hard_confidence,
        "reason": "knowledge_base_low_confidence",
        "knowledge_base_used": True,
        "kb_reasoning": kb_result.get("reasoning", ""),
        "llm_fallback_used": False,
    }


def _resolve_final_reason(
    final: Dict[str, Any],
    blockers: List[str],
) -> str:
    """根据决策层结果 + blockers 确定最终 reason。

    决策层语义:
    - reason=success → 业务决策成功(可能文件本身有问题,但决策有效)
    - reason=knowledge_base_low_confidence → 知识库低置信度(决策层不确定)
    - reason=knowledge_base_missing / no_match + LLM 兜底也失败 → 所有路径都失败

    blockers 语义(纯文件系统层):
    - source_missing → 源文件不存在
    - target_exists → 目标路径已存在
    - human_review_recommended → 业务层建议人核

    优先级: 决策层成功 → 用决策层 reason;决策层失败 + 文件系统错 → 提升为文件系统 reason。
    """
    final_reason = final.get("reason", "success")
    final_phase = final.get("phase")
    llm_fallback_used = final.get("llm_fallback_used", False)

    # 决策层真成功 → reason=success
    if final_reason == "success":
        return "success"

    # 决策层知识库低置信度(LLM 兜底未触发)→ 保留
    if final_reason == "knowledge_base_low_confidence" and not llm_fallback_used:
        return "knowledge_base_low_confidence"

    # 决策层失败:LLM 兜底触发但未救回 / kb_missing / kb_no_match
    # → 提升为文件系统 reason(如果有 blocker)
    if "source_missing" in blockers:
        return "source_missing"
    if "target_exists" in blockers:
        return "target_exists"
    if blockers:
        return "human_review_required"
    return final_reason


def build_archive_action(
    run_id: str,
    source_file: str,
    project_name: str,
    extracted: Dict[str, Any],
    ledger_result: Dict[str, Any],
    project_files_dir: str,
) -> Dict[str, Any]:
    """构建单文件的 archive action 字典（archive_action.v1 schema）。

    返回字段（archive_action.v1）：
    - schema_version: archive_action.v1
    - run_id, status, source_file, project_name, document_type
    - proposed_name, target_dir, target_path, blockers
    - business_judgement, archive_decision
    - reason: success / knowledge_base_missing / knowledge_base_no_match /
              knowledge_base_low_confidence / human_review_required /
              target_exists / source_missing
    - knowledge_base_used: bool
    - pageindex_query_id: None（PageIndex 不直接用于 md，等 PDF 接入时填）
    """
    judgement = ledger_result.get("business_judgement", {})
    decision = evaluate_archive_decision(
        source_file=source_file,
        extracted=extracted,
        business_judgement=judgement,
        project_files_dir=project_files_dir,
    )

    # 三层 fallback 决议
    final = _three_layer_fallback(source_file, decision)

    # 应用最终阶段（仅在业务判断有意义时覆盖 hard decision 的 phase）
    final_phase = final.get("phase")
    if final_phase and final_phase != "unknown":
        decision["archive_phase"] = final_phase

    document_type = decision.get("document_type") or extracted.get("document_type") or "未分类"
    target_dir = decision.get("target_dir")
    target_path = decision.get("target_path")
    project_name = decision.get("subject_name") or project_name
    blockers = list(decision.get("blockers", []))
    fact_gate = extracted.get("business_fact_gate") or {}
    if fact_gate.get("blocked_reason"):
        decision.setdefault("reasons", []).append(fact_gate["blocked_reason"])

    if decision.get("archive_phase") is None or target_path is None:
        proposed_name = ""
        target_abs = ""
        already_archived = False
    else:
        proposed_name = os.path.basename(target_path)
        target_abs = os.path.normcase(os.path.abspath(target_path))

    source_abs = os.path.normcase(os.path.abspath(source_file))
    if target_path is not None:
        already_archived = source_abs == target_abs
        if os.path.exists(target_path) and not already_archived:
            blockers.append("target_exists")
    else:
        already_archived = False

    if not os.path.exists(source_file):
        blockers.append("source_missing")

    reason = _resolve_final_reason(final, blockers)

    return {
        "schema_version": "archive_action.v1",
        "run_id": run_id,
        "status": "already_archived" if already_archived and not blockers else ("ready" if not blockers else "needs_review"),
        "source_file": source_file,
        "project_name": project_name,
        "document_type": document_type,
        "proposed_name": proposed_name,
        "target_dir": target_dir,
        "target_path": target_path,
        "blockers": blockers,
        "business_judgement": judgement,
        "archive_decision": decision,
        "reason": reason,
        "knowledge_base_used": final.get("knowledge_base_used", False),
        "pageindex_query_id": None,
    }


__all__ = ["build_archive_action", "business_phase_from_path"]
