from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


SUPPORTED_FEEDBACK_TYPES = {
    "field_correction",
    "archive_decision",
    "false_positive",
    "false_negative",
    "rule_exception",
    "parser_case",
    "human_confirmation",
}

DEFAULT_DECISIONS = {
    "field_correction": "correct",
    "archive_decision": "review",
    "false_positive": "mark_false_positive",
    "false_negative": "mark_false_negative",
    "rule_exception": "propose_rule_exception",
    "parser_case": "add_parser_case",
    "human_confirmation": "confirm",
}

DEFAULT_RISK_LEVELS = {
    "field_correction": "P2",
    "archive_decision": "P1",
    "false_positive": "P2",
    "false_negative": "P2",
    "rule_exception": "P1",
    "parser_case": "P3",
    "human_confirmation": "P1",
}


class FeedbackValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_feedback_decision(raw: Dict[str, Any], *, run_id: str, index: int) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise FeedbackValidationError("invalid_feedback_item", "feedback decision must be an object")
    feedback_type = str(raw.get("feedback_type") or "").strip()
    if feedback_type not in SUPPORTED_FEEDBACK_TYPES:
        raise FeedbackValidationError("unsupported_feedback_type", f"unsupported feedback_type: {feedback_type}")

    feedback_id = str(raw.get("feedback_id") or f"FB{index:03d}")
    decision = str(raw.get("decision") or DEFAULT_DECISIONS[feedback_type])
    normalized = {
        "schema_version": "human_feedback.decision.v1",
        "feedback_id": feedback_id,
        "run_id": run_id,
        "item_id": str(raw.get("item_id") or feedback_id),
        "feedback_type": feedback_type,
        "decision": decision,
        "risk_level": str(raw.get("risk_level") or DEFAULT_RISK_LEVELS[feedback_type]),
        "field": raw.get("field", ""),
        "old_value": raw.get("old_value", ""),
        "new_value": raw.get("new_value", raw.get("expected_value", "")),
        "source_file": raw.get("source_file", ""),
        "target_path": raw.get("target_path", ""),
        "finding_id": raw.get("finding_id", ""),
        "input_pattern": raw.get("input_pattern", ""),
        "expected_field": raw.get("expected_field", raw.get("field", "")),
        "expected_value": raw.get("expected_value", raw.get("new_value", "")),
        "evidence_text": raw.get("evidence_text", ""),
        "reason": raw.get("reason", ""),
        "created_at": raw.get("created_at") or datetime.now().isoformat(),
    }
    return normalized


def feedback_event(decision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "human_feedback.event.v1",
        "feedback_id": decision["feedback_id"],
        "run_id": decision["run_id"],
        "item_id": decision["item_id"],
        "feedback_type": decision["feedback_type"],
        "decision": decision["decision"],
        "risk_level": decision["risk_level"],
        "created_at": decision["created_at"],
    }


def build_rule_candidates(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = []
    for decision in decisions:
        if decision["feedback_type"] not in {"archive_decision", "rule_exception", "false_positive", "false_negative"}:
            continue
        candidates.append({
            "schema_version": "rule_candidate.v1",
            "feedback_id": decision["feedback_id"],
            "feedback_type": decision["feedback_type"],
            "run_id": decision["run_id"],
            "item_id": decision["item_id"],
            "decision": decision["decision"],
            "risk_level": decision["risk_level"],
            "source_file": decision.get("source_file", ""),
            "target_path": decision.get("target_path", ""),
            "finding_id": decision.get("finding_id", ""),
            "reason": decision.get("reason", ""),
            "status": "pending_rule_approval",
            "requires_test": True,
        })
    return candidates


def build_parser_test_candidates(decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates = []
    for decision in decisions:
        if decision["feedback_type"] not in {"field_correction", "parser_case"}:
            continue
        candidates.append({
            "schema_version": "parser_test_candidate.v1",
            "feedback_id": decision["feedback_id"],
            "run_id": decision["run_id"],
            "item_id": decision["item_id"],
            "field": decision.get("expected_field") or decision.get("field", ""),
            "input_pattern": decision.get("input_pattern") or decision.get("evidence_text", ""),
            "expected_value": decision.get("expected_value") or decision.get("new_value", ""),
            "reason": decision.get("reason", ""),
            "status": "pending_test_authoring",
        })
    return candidates
