"""Semantic document normalization and deterministic guardrails."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
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

@dataclass(frozen=True)
class DocumentClassification:
    """The single deterministic classification payload shared by consumers."""

    document_type: str
    business_domain: str
    project_phase: str | None
    archive_phase: str | None
    confidence: float
    evidence: List[str]
    requires_review: bool

    def payload(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_document_classification(value: Any, fallback_document_type: str = "未分类") -> Dict[str, Any]:
    """Return the stable classification payload without re-inspecting source text."""
    return _validated_document_classification(value, fallback_document_type)

_ARCHIVE_PHASES = frozenset({"项目投标", "项目执行", "项目丢标"})


def _validated_document_classification(value: Any, fallback_document_type: str) -> Dict[str, Any]:
    errors: List[str] = []
    if not isinstance(value, dict):
        errors.append("missing_document_classification")
        payload: Dict[str, Any] = {}
    else:
        payload = value
    document_type = payload.get("document_type")
    if not isinstance(document_type, str) or not document_type.strip():
        errors.append("invalid_document_type")
        document_type = fallback_document_type if isinstance(fallback_document_type, str) and fallback_document_type else "未分类"
    business_domain = payload.get("business_domain")
    if business_domain not in {"internal_project", "finance", "bid_project", "unknown"}:
        errors.append("invalid_business_domain")
        business_domain = "unknown"
    project_phase = payload.get("project_phase")
    archive_phase = payload.get("archive_phase")
    if project_phase is not None and (not isinstance(project_phase, str) or project_phase not in _ARCHIVE_PHASES):
        errors.append("invalid_project_phase")
        project_phase = None
    if archive_phase is not None and (not isinstance(archive_phase, str) or archive_phase not in _ARCHIVE_PHASES):
        errors.append("invalid_archive_phase")
        archive_phase = None
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
        errors.append("invalid_classification_confidence")
    if not isfinite(confidence):
        confidence = 0.0
        errors.append("invalid_classification_confidence")
    if confidence < 0.0 or confidence > 1.0:
        confidence = min(1.0, max(0.0, confidence))
        errors.append("classification_confidence_out_of_range")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        evidence = []
        errors.append("invalid_classification_evidence")
    result = {"document_type": document_type, "business_domain": business_domain, "project_phase": project_phase, "archive_phase": archive_phase, "confidence": confidence, "evidence": evidence, "requires_review": bool(payload.get("requires_review", False)) or bool(errors)}
    if errors:
        errors.insert(0, "invalid_document_classification")
        result["validation_errors"] = errors
    return result
