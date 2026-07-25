from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List

from contracts.review_queue_schema import review_trace_projection

_MUTABLE_REVIEW_ITEM_FIELDS = {
    "feedback_status", "feedback_ids", "feedback_decisions", "feedback_updated_at",
}



def build_feedback_form(
    *,
    run_id: str,
    review_queue: Dict[str, Any],
    audit_review: Dict[str, Any],
    adversarial_verification: Dict[str, Any],
) -> Dict[str, Any]:
    required_ids = {str(item_id) for item_id in audit_review.get("required_feedback_items") or []}
    items = []
    for raw_item in review_queue.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or raw_item.get("item_id") or "")
        trace = review_trace_projection(raw_item)
        items.append({
            "item_id": item_id,
            "run_id": run_id,
            "required": item_id in required_ids,
            "risk_level": raw_item.get("risk") or raw_item.get("risk_level", "P2"),
            "question": raw_item.get("question", ""),
            "feedback_type": raw_item.get("feedback_type", "rule_exception"),
            "allowed_decisions": raw_item.get("allowed_decisions") or ["accept", "reject", "defer"],
            "recommended_decision": raw_item.get("recommended_decision", "defer"),
            **trace,
            "source_file": trace.get("source_file", trace.get("file", "")),
            "target_path": trace.get("target_path", ""),
            "field": trace.get("field", ""),
            "expected_field": trace.get("expected_field", trace.get("field", "")),
            "evidence": trace.get("evidence", []),
            "confirmed": False,
            "review_item_hash": review_item_snapshot_hash(review_queue, raw_item),
            "response": {
                "decision": "",
                "new_value": "",
                "expected_value": "",
                "input_pattern": "",
                "reason": "",
            },
        })

    return {
        "schema_version": "feedback_form.v1",
        "run_id": run_id,
        "created_at": datetime.now().isoformat(),
        "audit_verdict": audit_review.get("audit_verdict", ""),
        "verification_verdict": (
            adversarial_verification.get("verification_verdict")
            or adversarial_verification.get("overall_verdict")
            or adversarial_verification.get("status")
            or ""
        ),
        "required_feedback_items": sorted(required_ids),
        "items": items,
        "instructions": [
            "Fill response.decision with one allowed decision before applying this form.",
            "Leave response.decision empty to keep an item pending.",
            "This form only produces feedback artifacts; it does not execute archive actions.",
        ],
    }


def render_feedback_form_markdown(form: Dict[str, Any]) -> str:
    lines = [
        f"# Feedback Form: {form.get('run_id', '')}",
        "",
        f"- schema: {form.get('schema_version', '')}",
        f"- audit_verdict: {form.get('audit_verdict', '')}",
        f"- verification_verdict: {form.get('verification_verdict', '')}",
        f"- required_feedback_items: {', '.join(form.get('required_feedback_items') or []) or '-'}",
        "",
        "Edit `feedback_form.json`, then run `scripts/review_file_organization_run.py --apply`.",
        "",
    ]
    for item in form.get("items") or []:
        lines.extend([
            f"## {item.get('item_id', '')} [{item.get('risk_level', '')}]",
            "",
            f"- required: {item.get('required', False)}",
            f"- feedback_type: {item.get('feedback_type', '')}",
            f"- question: {item.get('question', '')}",
            f"- allowed_decisions: {', '.join(item.get('allowed_decisions') or [])}",
            f"- recommended_decision: {item.get('recommended_decision', '')}",
            f"- source_file: {item.get('source_file', '')}",
            f"- target_path: {item.get('target_path', '')}",
            "- response.decision: ",
            "- response.reason: ",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def feedback_decisions_from_form(form: Dict[str, Any]) -> List[Dict[str, Any]]:
    run_id = str(form.get("run_id") or "")
    decisions = []
    for index, item in enumerate(form.get("items") or [], 1):
        if not isinstance(item, dict):
            continue
        response = item.get("response") if isinstance(item.get("response"), dict) else {}
        decision = str(response.get("decision") or item.get("decision") or "").strip()
        if not decision:
            continue
        feedback_id = str(item.get("feedback_id") or f"FB{index:03d}")
        decisions.append({
            "feedback_id": feedback_id,
            "run_id": run_id,
            "item_id": item.get("item_id", feedback_id),
            "feedback_type": item.get("feedback_type", "rule_exception"),
            "decision": decision,
            "risk_level": item.get("risk_level", "P2"),
            "field": item.get("field", ""),
            "expected_field": item.get("expected_field", item.get("field", "")),
            "old_value": item.get("old_value", ""),
            "new_value": response.get("new_value", item.get("new_value", "")),
            "expected_value": response.get("expected_value", item.get("expected_value", "")),
            "input_pattern": response.get("input_pattern", item.get("input_pattern", "")),
            "source_file": item.get("source_file", ""),
            "target_path": item.get("target_path", ""),
            "finding_id": item.get("finding_id", ""),
            "evidence_text": item.get("evidence_text", ""),
            "reason": response.get("reason", item.get("reason", "")),
            "review_item_hash": item.get("review_item_hash", ""),
            "actor": response.get("actor", item.get("actor", "")),
            "created_at": item.get("created_at", ""),
        })
    return decisions


def review_item_snapshot_hash(
    review_queue: Dict[str, Any], item: Dict[str, Any],
) -> str:
    if type(review_queue) is not dict or type(item) is not dict:
        raise ValueError("invalid review snapshot")
    immutable_item = {
        key: value for key, value in item.items()
        if key not in _MUTABLE_REVIEW_ITEM_FIELDS
    }
    snapshot = {
        "queue": {
            "schema_version": review_queue.get("schema_version"),
            "run_id": review_queue.get("run_id"),
            "status": review_queue.get("status"),
        },
        "item": immutable_item,
    }
    return _snapshot_hash(snapshot)


def _snapshot_hash(value: Dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
