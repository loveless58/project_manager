"""Semantic document normalization and deterministic guardrails."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .field_quality import filter_business_facts


MIN_SEMANTIC_CONFIDENCE = 0.6


def normalize_semantic_response(raw: Any, fallback_document_type: str = "") -> Dict[str, Any]:
    """Normalize a semantic adapter response into semantic_document.v1 shape."""
    if not isinstance(raw, dict):
        return {
            "schema_version": "semantic_document.v1",
            "status": "blocked",
            "blocked_reason": "semantic_adapter_returned_non_object",
            "document_type": fallback_document_type,
            "business_fields": {},
            "uncertain_fields": [],
            "review_reasons": ["semantic_adapter_returned_non_object"],
        }

    business_fields = raw.get("business_fields") or raw.get("fields") or {}
    if not isinstance(business_fields, dict):
        business_fields = {}

    return {
        "schema_version": "semantic_document.v1",
        "status": raw.get("status", "success"),
        "document_type": raw.get("document_type") or fallback_document_type,
        "document_subtype": raw.get("document_subtype", ""),
        "business_fields": business_fields,
        "uncertain_fields": raw.get("uncertain_fields") or [],
        "review_reasons": raw.get("review_reasons") or [],
    }


def apply_semantic_guardrail(
    semantic_document: Dict[str, Any],
    source_type: str,
    source_path: str,
    min_confidence: float = MIN_SEMANTIC_CONFIDENCE,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Accept only evidence-backed semantic fields, then reuse field quality checks."""
    accepted_candidates: Dict[str, Any] = {}
    rejected: List[Dict[str, Any]] = []

    for field, payload in (semantic_document.get("business_fields") or {}).items():
        if isinstance(payload, dict):
            value = payload.get("value")
            confidence = payload.get("confidence", 0)
            evidence_refs = payload.get("evidence_refs") or []
        else:
            value = payload
            confidence = 0
            evidence_refs = []

        if value in (None, ""):
            rejected.append({"field": field, "reason": "empty_semantic_value"})
            continue
        if not isinstance(evidence_refs, list) or not evidence_refs:
            rejected.append({"field": field, "reason": "missing_evidence_refs", "value": value})
            continue
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        if confidence_value < min_confidence:
            rejected.append({
                "field": field,
                "reason": "semantic_confidence_too_low",
                "confidence": confidence,
                "value": value,
            })
            continue

        accepted_candidates[field] = value

    accepted, field_quality = filter_business_facts(
        accepted_candidates,
        source_type=source_type,
        source_path=source_path,
    )
    accepted_field_names = set(accepted.keys())
    for item in field_quality.get("rejected_fields", []):
        field = item.get("field")
        if field and field not in accepted_field_names:
            rejected.append({
                "field": field,
                "reason": item.get("reason", "field_quality_rejected"),
                "value": item.get("value"),
            })

    guardrail = {
        "schema_version": "semantic_guardrail.v1",
        "status": "ready" if not rejected else "partial",
        "min_confidence": min_confidence,
        "accepted_fields": sorted(accepted.keys()),
        "rejected_fields": rejected,
        "field_quality": field_quality,
    }
    return accepted, guardrail
