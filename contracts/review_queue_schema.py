from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from platform_core.document_refs import (
    DocumentRefValidationError,
    validate_document_ref,
)
from platform_core.sensitive_text import (
    contains_sensitive_text as review_value_contains_sensitive_text,
)

MAX_REVIEW_BYTES = 128 * 1024
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
    "parser_case_review": {
        "feedback_type": "parser_case",
        "allowed_decisions": ["add_parser_case", "defer"],
        "recommended_decision": "add_parser_case",
        "question": "解析样例需要复核，是否生成候选测试？",
    },
    "false_positive_review": {
        "feedback_type": "false_positive",
        "allowed_decisions": ["mark_false_positive", "defer"],
        "recommended_decision": "mark_false_positive",
        "question": "该发现是否应标记为误报？",
    },
    "business_relation_review": {
        "feedback_type": "field_correction",
        "allowed_decisions": ["approve", "reject", "correct_relation", "defer"],
        "recommended_decision": "defer",
        "question": "业务关系仅为候选，是否接受候选、拒绝或提交修正？",
    },
    "archive_target_review": {
        "feedback_type": "archive_decision",
        "allowed_decisions": ["approve", "reject", "edit_target", "defer"],
        "recommended_decision": "defer",
        "question": "归档目标尚未形成执行授权，是否接受候选、拒绝或提交目标修正？",
    },
    "adversarial_verification": {
        "feedback_type": "rule_exception",
        "allowed_decisions": ["retry_verification", "defer", "accept_risk"],
        "recommended_decision": "retry_verification",
        "question": "对抗性验证未通过，是否修复后重试、暂缓或记录风险接受意见？",
    },
    "adversarial_verification_error": {
        "feedback_type": "rule_exception",
        "allowed_decisions": ["retry_verification", "defer", "accept_risk"],
        "recommended_decision": "retry_verification",
        "question": "对抗性验证执行失败，是否重试验证、暂缓或记录风险接受意见？",
    },
}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
_BACKSLASH = chr(92)
_FORWARD_SLASH = chr(47)
_FILE_URI_PREFIX = "file:" + _FORWARD_SLASH * 3
_ABSOLUTE_PATH = re.compile(
    rf"^(?:[A-Za-z]:[{re.escape(_BACKSLASH + _FORWARD_SLASH)}]|"
    rf"{re.escape(_BACKSLASH * 2)}|{re.escape(_FORWARD_SLASH * 2)}|"
    rf"{re.escape(_FORWARD_SLASH)}|{re.escape(_FILE_URI_PREFIX)})",
    re.IGNORECASE,
)
_TRACE_TEXT_FIELDS = {
    "project_name", "reason", "recommended_action", "verdict", "block_reason",
    "finding_id", "dimension", "status", "destination_status", "content_hash",
    "artifact_schema_version", "interpreter", "model", "prompt_version",
    "policy_version", "field", "expected_field", "source_file", "target_path",
    "file", "project_overview_md",
}
_TRACE_LIST_FIELDS = {
    "blockers", "missing_fields", "risk_reasons", "recommended_actions",
    "low_confidence_items",
}
_CANDIDATE_LIST_FIELDS = {"candidate_ids", "candidate_target_binding_ids"}


def normalize_review_queue(run_id: str, raw_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not _safe_text(run_id, allow_path_like=True) or len(run_id) > 128:
        raise ValueError("invalid_review_run_id")
    if type(raw_items) is not list or len(raw_items) > 256:
        raise ValueError("invalid_review_items")
    items = []
    identifiers = set()
    for index, raw_item in enumerate(raw_items, 1):
        _validate_raw_item(raw_item)
        item = normalize_review_queue_item(run_id=run_id, raw_item=raw_item, index=index)
        if item["id"] in identifiers:
            raise ValueError("duplicate_review_item_id")
        identifiers.add(item["id"])
        items.append(item)
    return {
        "schema_version": "review_queue.v2",
        "run_id": run_id,
        "status": "needs_review" if items else "clear",
        "items": items,
    }


def review_policy_for_type(item_type: object) -> Dict[str, Any]:
    """Return a caller-isolated copy of the fixed decision policy."""
    defaults = TYPE_DEFAULTS.get(item_type)
    if defaults is None:
        return {
            "feedback_type": "rule_exception",
            "allowed_decisions": ["accept", "reject", "defer"],
            "recommended_decision": "defer",
        }
    return {
        key: list(defaults[key]) if key == "allowed_decisions" else defaults[key]
        for key in ("feedback_type", "allowed_decisions", "recommended_decision")
    }


def normalize_review_queue_item(run_id: str, raw_item: Dict[str, Any], index: int) -> Dict[str, Any]:
    item_type = raw_item.get("type") if _safe_text(raw_item.get("type")) else "review_item"
    defaults = TYPE_DEFAULTS.get(item_type, {
        "feedback_type": "rule_exception",
        "allowed_decisions": ["accept", "reject", "defer"],
        "recommended_decision": "defer",
        "question": "该复核项需要人工判断。",
    })
    severity = raw_item.get("severity") if _safe_text(raw_item.get("severity")) else "unknown"
    projected = review_trace_projection(raw_item)
    policy = review_policy_for_type(item_type)
    identifier = raw_item.get("id")
    if not _safe_text(identifier) or not _IDENTIFIER.fullmatch(identifier):
        identifier = f"R{index:03d}"
    allowed = policy["allowed_decisions"]
    recommended = policy["recommended_decision"]
    question = raw_item.get("question")
    if not _safe_text(question):
        question = _question_for_item(projected | {"type": item_type}, defaults["question"])
    feedback_type = policy["feedback_type"]
    risk = raw_item.get("risk")
    if not _safe_text(risk):
        risk = SEVERITY_TO_RISK.get(severity, "P2")
    normalized = {
        "type": item_type,
        "severity": severity,
        **projected,
        "id": identifier,
        "run_id": run_id,
        "risk": risk,
        "question": question,
        "feedback_type": feedback_type,
        "allowed_decisions": allowed,
        "recommended_decision": recommended,
    }
    normalized["evidence"] = projected.get("evidence") or _evidence_for_item(normalized)
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
    if item_type == "business_relation_review":
        return TYPE_DEFAULTS[item_type]["question"]
    if item_type == "archive_target_review":
        return TYPE_DEFAULTS[item_type]["question"]
    if item_type in {"adversarial_verification", "adversarial_verification_error"}:
        return TYPE_DEFAULTS[item_type]["question"]
    return fallback


def _evidence_for_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = []
    for field in ("file", "source_file", "target_path", "project_overview_md"):
        value = item.get(field)
        if _safe_text(value):
            evidence.append({"kind": field, "value": value})
    for field in ("blockers", "risk_reasons", "missing_fields"):
        value = item.get(field)
        if value:
            evidence.append({"kind": field, "value": value})
    return evidence


def review_trace_projection(item: Dict[str, Any]) -> Dict[str, Any]:
    """Project review provenance through a bounded allow-list.

    Physical paths, credentials and caller-controlled authority flags are never
    copied. Candidate review types are always explicitly non-authoritative.
    """
    if type(item) is not dict:
        return {}
    result: Dict[str, Any] = {}
    for key in _TRACE_TEXT_FIELDS:
        value = item.get(key)
        if _safe_text(value) and (key not in {"content_hash"} or _CONTENT_HASH.fullmatch(value)):
            result[key] = value
    for key in _TRACE_LIST_FIELDS:
        values = _safe_string_list(item.get(key))
        if values:
            result[key] = values
    for key in _CANDIDATE_LIST_FIELDS:
        values = _safe_string_list(item.get(key), identifiers=True)
        if values:
            result[key] = values
    source_ref = _source_ref(item.get("source_ref"))
    if source_ref:
        result["source_ref"] = source_ref
    evidence_refs = _record_list(
        item.get("evidence_refs"), {"kind", "candidate_id", "field", "ref", "code"},
    )
    if evidence_refs:
        result["evidence_refs"] = evidence_refs
    conflicts = _record_list(item.get("conflicts"), {"code"})
    if conflicts:
        result["conflicts"] = conflicts
    evidence = _record_list(item.get("evidence"), {"kind", "value", "candidate_id", "field", "ref", "code"})
    if evidence:
        result["evidence"] = evidence
    if item.get("type") in {"business_relation_review", "archive_target_review"} or "confirmed" in item:
        result["confirmed"] = False
    return result


def _record_list(value: Any, allowed_fields: set[str]) -> List[Dict[str, Any]]:
    if type(value) is not list and type(value) is not tuple:
        return []
    result = []
    for record in list(value)[:256]:
        if type(record) is not dict:
            continue
        projected: Dict[str, Any] = {}
        for key, raw in record.items():
            if key not in allowed_fields:
                continue
            if _safe_text(raw):
                projected[key] = raw
            elif type(raw) is list:
                values = _safe_string_list(raw)
                if values:
                    projected[key] = values
        if projected:
            result.append(projected)
    return result


def _source_ref(value: Any) -> Dict[str, str]:
    try:
        ref = validate_document_ref(value, require_binding=True)
    except DocumentRefValidationError:
        return {}
    return {
        "storage_provider": ref.storage_provider,
        "object_key": ref.object_key,
        "logical_uri": ref.logical_uri,
        "binding_id": ref.binding_id,
    }


def _safe_string_list(value: Any, *, identifiers: bool = False) -> List[str]:
    if type(value) is not list and type(value) is not tuple:
        return []
    result = []
    for raw in list(value)[:256]:
        if not _safe_text(raw):
            continue
        if identifiers and not _IDENTIFIER.fullmatch(raw):
            continue
        if raw not in result:
            result.append(raw)
    return result




def _safe_text(value: Any, *, allow_path_like: bool = False) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    if len(value.encode("utf-8")) > 2048 or review_value_contains_sensitive_text(value):
        return False
    return allow_path_like or not _ABSOLUTE_PATH.search(value)


def _validate_raw_item(value: Any) -> None:
    if type(value) is not dict:
        raise ValueError("invalid_review_item")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError("invalid_review_item") from None
    if len(encoded) > MAX_REVIEW_BYTES:
        raise ValueError("review_item_size_limit")


__all__ = [
    "MAX_REVIEW_BYTES", "TYPE_DEFAULTS", "normalize_review_queue",
    "normalize_review_queue_item", "review_trace_projection",
    "review_policy_for_type", "review_value_contains_sensitive_text",
]
