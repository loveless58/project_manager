"""Auditable per-document result contract for a prepare-review run."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .artifact_reference import validate_artifact_reference


SCHEMA_VERSION = "document_result.v1"
_DECISION_STATUSES = {"needs_review"}
_DECISION_ACTIONS = {"no_archive"}
_PARSE_REQUIRED = {
    "schema_version",
    "status",
    "source_ref",
    "content_hash",
    "media_type",
    "parser",
    "text",
    "pages",
    "tables",
    "fields",
}


def build_document_result(
    *,
    run: Mapping[str, Any],
    document_id: str,
    structured_document: Mapping[str, Any],
    facts: Mapping[str, Any],
    decision: Mapping[str, Any],
    artifacts: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one reviewable result without authorising any source-file mutation."""
    run_view = _run_view(run)
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document id is invalid")
    parse = _parse_view(structured_document)
    if not isinstance(facts, Mapping):
        raise ValueError("facts are invalid")
    decision_view = _decision_view(decision)
    if not isinstance(artifacts, list):
        raise ValueError("artifacts are invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "run": run_view,
        "document": {
            "document_id": document_id,
            "source_ref": deepcopy(parse["source_ref"]),
            "content_hash": parse["content_hash"],
            "media_type": parse["media_type"],
        },
        "parse": parse,
        "facts": deepcopy(dict(facts)),
        "decision": decision_view,
        "artifacts": [validate_artifact_reference(item) for item in artifacts],
    }


def _run_view(run: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(run, Mapping):
        raise ValueError("run is invalid")
    run_id, goal, status = run.get("run_id"), run.get("goal"), run.get("status")
    if not all(isinstance(value, str) and value.strip() for value in (run_id, goal, status)):
        raise ValueError("run is invalid")
    return {"run_id": run_id, "goal": goal, "status": status}


def _parse_view(structured_document: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(structured_document, Mapping):
        raise ValueError("structured document is invalid")
    if structured_document.get("schema_version") != "structured_document.v1":
        raise ValueError("structured document version is invalid")
    if not _PARSE_REQUIRED.issubset(structured_document):
        raise ValueError("structured document fields are invalid")
    if structured_document.get("status") not in {"success", "needs_review"}:
        raise ValueError("structured document status is invalid")
    if not isinstance(structured_document.get("source_ref"), Mapping):
        raise ValueError("structured document source reference is invalid")
    if not isinstance(structured_document.get("text"), str):
        raise ValueError("structured document text is invalid")
    return deepcopy(dict(structured_document))


def _decision_view(decision: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(decision, Mapping):
        raise ValueError("decision is invalid")
    if decision.get("status") not in _DECISION_STATUSES:
        raise ValueError("decision status is invalid")
    if decision.get("suggested_action") not in _DECISION_ACTIONS:
        raise ValueError("suggested action is invalid")
    reasons = decision.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        raise ValueError("decision reasons are invalid")
    return {
        "status": decision["status"],
        "suggested_action": decision["suggested_action"],
        "reasons": list(reasons),
    }


__all__ = ["SCHEMA_VERSION", "build_document_result"]
