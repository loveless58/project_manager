from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Set


def evaluate_archive_execution_gate(
    *,
    run_id: str,
    confirmed: bool,
    audit_review: Dict[str, Any],
    review_queue: Dict[str, Any],
) -> Dict[str, Any]:
    blockers: List[str] = []
    pending_required = _pending_required_feedback_items(audit_review, review_queue)
    if not confirmed:
        blockers.append("confirmation_required")
    if pending_required:
        blockers.append("required_feedback_pending")

    return {
        "schema_version": "archive_execution_gate.v1",
        "run_id": run_id,
        "checked_at": datetime.now().isoformat(),
        "status": "blocked" if blockers else "passed",
        "confirmed": confirmed,
        "blockers": blockers,
        "audit_verdict": audit_review.get("audit_verdict", ""),
        "human_feedback_required": bool(audit_review.get("human_feedback_required")),
        "required_feedback_items": list(audit_review.get("required_feedback_items") or []),
        "pending_required_feedback_items": pending_required,
    }


def _pending_required_feedback_items(
    audit_review: Dict[str, Any],
    review_queue: Dict[str, Any],
) -> List[str]:
    required_items = audit_review.get("required_feedback_items") or []
    if not audit_review.get("human_feedback_required") and not required_items:
        return []

    required_ids: Set[str] = {str(item_id) for item_id in required_items if str(item_id)}
    item_status = {}
    for item in review_queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("item_id") or "")
        if item_id:
            item_status[item_id] = item.get("feedback_status", "pending")

    if required_ids:
        return sorted(item_id for item_id in required_ids if item_status.get(item_id) != "feedback_received")

    pending = []
    for item in review_queue.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or item.get("item_id") or "")
        if item_id and item.get("feedback_status", "pending") != "feedback_received":
            pending.append(item_id)
    return sorted(pending)
