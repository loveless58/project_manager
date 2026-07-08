from __future__ import annotations

from typing import Any, Dict, List


SEVERITY_TO_RISK = {
    "critical": "P0",
    "high": "P1",
    "medium": "P2",
    "low": "P3",
    "unknown": "P2",
}

TYPE_DEFAULTS = {
    "extraction_failure": {
        "feedback_type": "false_negative",
        "allowed_decisions": ["retry_after_fix", "mark_unreadable", "ignore", "defer"],
        "recommended_decision": "retry_after_fix",
        "question": "文件提取失败，是否修复来源后重试或标记为不可读？",
    },
    "extraction_quality_review": {
        "feedback_type": "field_correction",
        "allowed_decisions": ["accept", "correct", "needs_manual_ocr", "defer"],
        "recommended_decision": "correct",
        "question": "提取质量不足，是否接受、修正字段或进入人工 OCR？",
    },
    "business_judgement_review": {
        "feedback_type": "field_correction",
        "allowed_decisions": ["accept", "correct", "rule_exception", "defer"],
        "recommended_decision": "correct",
        "question": "业务判断需要复核，是否接受、修正字段或提出规则例外？",
    },
    "archive_action_review": {
        "feedback_type": "archive_decision",
        "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
        "recommended_decision": "defer",
        "question": "归档计划需要确认，是否批准、拒绝或修改目标路径？",
    },
}


def normalize_review_queue(run_id: str, raw_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = [
        normalize_review_queue_item(run_id=run_id, raw_item=item, index=index)
        for index, item in enumerate(raw_items or [], 1)
    ]
    return {
        "schema_version": "review_queue.v2",
        "run_id": run_id,
        "status": "needs_review" if items else "clear",
        "items": items,
    }


def normalize_review_queue_item(run_id: str, raw_item: Dict[str, Any], index: int) -> Dict[str, Any]:
    item = dict(raw_item)
    item_type = str(item.get("type") or "review_item")
    defaults = TYPE_DEFAULTS.get(item_type, {
        "feedback_type": "rule_exception",
        "allowed_decisions": ["accept", "reject", "defer"],
        "recommended_decision": "defer",
        "question": "该复核项需要人工判断。",
    })
    severity = str(item.get("severity") or "unknown")
    normalized = {
        **item,
        "id": item.get("id") or f"R{index:03d}",
        "run_id": run_id,
        "risk": item.get("risk") or SEVERITY_TO_RISK.get(severity, "P2"),
        "question": item.get("question") or _question_for_item(item, defaults["question"]),
        "feedback_type": item.get("feedback_type") or defaults["feedback_type"],
        "allowed_decisions": item.get("allowed_decisions") or list(defaults["allowed_decisions"]),
        "recommended_decision": item.get("recommended_decision") or defaults["recommended_decision"],
        "evidence": item.get("evidence") or _evidence_for_item(item),
    }
    return normalized


def _question_for_item(item: Dict[str, Any], fallback: str) -> str:
    item_type = item.get("type")
    if item_type == "archive_action_review":
        project = item.get("project_name") or "未命名项目"
        target = item.get("target_path") or "未指定目标路径"
        return f"是否确认将 {item.get('source_file', '该文件')} 归档到 {project} 的 {target}？"
    if item_type == "extraction_failure":
        return f"文件 {item.get('file', '')} 提取失败，是否修复后重试或标记不可读？"
    if item_type == "extraction_quality_review":
        return f"文件 {item.get('file', '')} 的结构化提取质量不足，是否人工修正？"
    if item_type == "business_judgement_review":
        project = item.get("project_name") or "未命名项目"
        return f"项目 {project} 的业务判断需要复核，是否修正字段或规则？"
    return fallback


def _evidence_for_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = []
    for field in ("file", "source_file", "target_path", "project_overview_md"):
        value = item.get(field)
        if value:
            evidence.append({"kind": field, "value": value})
    if item.get("blockers"):
        evidence.append({"kind": "blockers", "value": item["blockers"]})
    if item.get("risk_reasons"):
        evidence.append({"kind": "risk_reasons", "value": item["risk_reasons"]})
    if item.get("missing_fields"):
        evidence.append({"kind": "missing_fields", "value": item["missing_fields"]})
    return evidence
