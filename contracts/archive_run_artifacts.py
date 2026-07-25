"""Strict, bounded contracts for Task5 archive-intent run artifacts."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any

from contracts.document_interpretation import (
    DocumentInterpretationSchemaError,
    parse_candidate_document_interpretation,
)
from platform_core.models import BusinessContextEvidence


MAX_BYTES = 128 * 1024
MAX_DEPTH = 24
MAX_NODES = 4096
MAX_COLLECTION = 256
MAX_STRING_BYTES = 8192
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_.]{1,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL = re.compile(
    r"(?<!\w)(?:(?:authorization|token|api[_-]?(?:key|token)|password)\s*[:=]\s*\S|bearer\s+\S)",
    re.IGNORECASE,
)
_DOUBLE_SLASH = "/" * 2
_FILE_URI = "file:" + "/" * 3
_ABSOLUTE_PATH = re.compile(
    rf"(?:^|[\s\"'(=])(?:{re.escape(_FILE_URI)}|{re.escape(_DOUBLE_SLASH)}[^/\s]+/[^/\s]+|[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+|/(?!/))"
)
_REF_KEYS = {"storage_provider", "object_key", "logical_uri", "binding_id"}
_INTERPRETATION_KEYS = {
    "schema_version", "status", "document_type", "fields", "relations", "evidence",
    "confidence", "interpreter", "model", "prompt_version", "policy_version",
    "blocked_reason", "parse_artifact_ref", "confirmed",
}
_ENVELOPE_KEYS = {
    "run_id", "source_ref", "content_hash", "business_context", "business_relation",
}
_INTENT_KEYS = {
    "schema_version", "run_id", "source_ref", "destination_status",
    "candidate_target_binding_ids", "project_id", "archive_phase", "content_hash",
    "intent_id", "normalized_status", "blockers", "classification",
}
_ACTION_KEYS = {
    "schema_version", "run_id", "status", "normalized_status", "confirmed",
    "source_file", "source_ref", "project_name", "document_type",
    "business_judgement", "archive_decision", "proposed_name", "target_dir",
    "target_path", "blockers", "archive_intent_ref", "archive_intent",
}


class ArchiveRunArtifactError(ValueError):
    """An archive-run artifact failed a strict boundary contract."""


def strict_json_load(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as stream:
        value = json.load(
            stream,
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda value: _reject_constant(value),
        )
    validate_json_tree(value, inspect_sensitive=False)
    if type(value) is not dict:
        raise ArchiveRunArtifactError("JSON root must be an object")
    return value


def validate_json_tree(value: object, *, inspect_sensitive: bool = True) -> None:
    stack = [(value, 1)]
    nodes = 0
    encoded_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ArchiveRunArtifactError("JSON tree exceeds limits")
        if type(item) is str:
            size = len(item.encode("utf-8"))
            encoded_bytes += size
            if size > MAX_STRING_BYTES or (
                inspect_sensitive and _contains_sensitive(item)
            ):
                raise ArchiveRunArtifactError("unsafe string")
        elif item is None or type(item) in (bool, int):
            continue
        elif type(item) is float:
            if not math.isfinite(item):
                raise ArchiveRunArtifactError("non-finite number")
        elif type(item) is list or type(item) is tuple:
            if len(item) > MAX_COLLECTION:
                raise ArchiveRunArtifactError("collection exceeds limit")
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            if len(item) > MAX_COLLECTION or any(type(key) is not str for key in item):
                raise ArchiveRunArtifactError("invalid object")
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        else:
            raise ArchiveRunArtifactError("non-JSON value")
        if encoded_bytes > MAX_BYTES:
            raise ArchiveRunArtifactError("JSON artifact exceeds size limit")


def normalize_business_context(
    context: object,
) -> tuple[BusinessContextEvidence, dict[str, Any]]:
    try:
        if not isinstance(context, BusinessContextEvidence):
            raise ArchiveRunArtifactError("context type")
        if context.status not in {"matched", "needs_review", "blocked"}:
            raise ArchiveRunArtifactError("context status")
        for sequence in (
            context.candidates, context.evidence_refs, context.conflicts, context.diagnostics,
        ):
            if type(sequence) not in (tuple, list) or len(sequence) > MAX_COLLECTION:
                raise ArchiveRunArtifactError("context collection")
            validate_json_tree(sequence)
        candidate_ids = []
        for candidate in context.candidates:
            if type(candidate) is not dict or set(candidate) - {
                "id", "document_type", "parties", "facts", "documents", "path_hints", "match_score",
            }:
                raise ArchiveRunArtifactError("candidate schema")
            identifier = candidate.get("id")
            if type(identifier) is not str or not _ID.fullmatch(identifier):
                raise ArchiveRunArtifactError("candidate id")
            candidate_ids.append(identifier)
        diagnostics = [_diagnostic(item) for item in context.diagnostics]
        conflicts = [_conflict(item) for item in context.conflicts]
        payload = {
            "status": context.status,
            "candidate_ids": candidate_ids,
            "conflicts": conflicts,
            "diagnostics": diagnostics,
        }
        validate_json_tree(payload)
        return context, payload
    except ArchiveRunArtifactError:
        blocked = BusinessContextEvidence(
            "blocked", (), (), (), ({"code": "BUSINESS_CONTEXT.SCHEMA_INVALID"},),
        )
        return blocked, {
            "status": "blocked", "candidate_ids": [], "conflicts": [],
            "diagnostics": [{"code": "BUSINESS_CONTEXT.SCHEMA_INVALID"}],
        }


def normalize_interpretation_output(
    value: object, *, parse_artifact_ref: str,
) -> dict[str, Any]:
    try:
        validate_json_tree(value)
        if type(value) is not dict or set(value) - _INTERPRETATION_KEYS:
            raise ArchiveRunArtifactError("interpretation schema")
        if value.get("status") == "blocked":
            if set(value) != {"status", "blocked_reason", "parse_artifact_ref", "confirmed"}:
                raise ArchiveRunArtifactError("blocked interpretation schema")
            reason = value.get("blocked_reason")
            if (
                type(reason) is not str or not _CODE.fullmatch(reason)
                or value.get("parse_artifact_ref") != parse_artifact_ref
                or value.get("confirmed") is not False
            ):
                raise ArchiveRunArtifactError("blocked interpretation identity")
            return dict(value)
        canonical = {
            key: item for key, item in value.items()
            if key not in {"parse_artifact_ref", "confirmed"}
        }
        parsed = parse_candidate_document_interpretation(canonical)
        if value.get("parse_artifact_ref", parse_artifact_ref) != parse_artifact_ref:
            raise ArchiveRunArtifactError("parse artifact mismatch")
        if value.get("confirmed", False) is not False:
            raise ArchiveRunArtifactError("interpretation confirmation")
        parsed["parse_artifact_ref"] = parse_artifact_ref
        parsed["confirmed"] = False
        return parsed
    except (ArchiveRunArtifactError, DocumentInterpretationSchemaError):
        return {
            "status": "blocked",
            "blocked_reason": "DOCUMENT_INTERPRETATION.SCHEMA_INVALID",
            "parse_artifact_ref": parse_artifact_ref,
            "confirmed": False,
        }


def validate_task5_collection_artifact(
    payload: dict[str, Any], *, kind: str, run_id: str,
) -> None:
    validate_json_tree(payload)
    specifications = {
        "input_manifest": (
            "file_organization.input_manifest.v1", {"schema_version", "run_id", "files"}, "files",
        ),
        "candidate_interpretations": (
            "candidate_interpretations.v1", {"schema_version", "run_id", "items"}, "items",
        ),
        "archive_intents": (
            "archive_intents.v1", {"schema_version", "run_id", "items"}, "items",
        ),
        "planned_archive_actions": (
            "archive_plan.v1", {"schema_version", "run_id", "archive_intent_required", "actions"}, "actions",
        ),
    }
    if kind not in specifications:
        raise ArchiveRunArtifactError("unknown artifact kind")
    schema, root_fields, collection_key = specifications[kind]
    if type(payload) is not dict or set(payload) != root_fields:
        raise ArchiveRunArtifactError("artifact root fields")
    if payload.get("schema_version") != schema or payload.get("run_id") != run_id:
        raise ArchiveRunArtifactError("artifact identity")
    items = payload.get(collection_key)
    if type(items) is not list or len(items) > MAX_COLLECTION:
        raise ArchiveRunArtifactError("artifact items")
    if kind == "planned_archive_actions":
        if payload.get("archive_intent_required") is not True:
            raise ArchiveRunArtifactError("intent plan marker")
        for item in items:
            validate_archive_action(item, run_id)
    elif kind == "archive_intents":
        for item in items:
            validate_archive_intent(item, run_id)
    elif kind == "candidate_interpretations":
        for item in items:
            validate_candidate_interpretation(item, run_id)
    else:
        for item in items:
            if type(item) is not dict or set(item) != {
                "run_id", "source_ref", "name", "content_hash",
            }:
                raise ArchiveRunArtifactError("manifest item fields")
            _run_hash_ref(item, run_id)
            name = item.get("name")
            if type(name) is not str or not name or name != name.strip():
                raise ArchiveRunArtifactError("manifest name")


def validate_candidate_interpretation(entry: dict[str, Any], run_id: str) -> None:
    validate_json_tree(entry)
    blocked_fields = {
        "status", "blocked_reason", "parse_artifact_ref", "confirmed",
    } | _ENVELOPE_KEYS
    canonical_fields = {
        "schema_version", "status", "document_type", "fields", "relations", "evidence",
        "confidence", "interpreter", "model", "prompt_version", "policy_version",
        "parse_artifact_ref", "confirmed",
    } | _ENVELOPE_KEYS
    expected = blocked_fields if entry.get("status") == "blocked" else canonical_fields
    if type(entry) is not dict or set(entry) != expected:
        raise ArchiveRunArtifactError("candidate interpretation fields")
    _run_hash_ref(entry, run_id)
    if entry.get("parse_artifact_ref") != f"artifact:parsed:{entry['content_hash'][:24]}":
        raise ArchiveRunArtifactError("parse artifact identity")
    context = entry.get("business_context")
    if type(context) is not dict or set(context) != {
        "status", "candidate_ids", "conflicts", "diagnostics",
    } or context.get("status") not in {"matched", "needs_review", "blocked"}:
        raise ArchiveRunArtifactError("business context envelope")
    candidate_ids = context.get("candidate_ids")
    if type(candidate_ids) is not list or any(
        type(identifier) is not str or not _ID.fullmatch(identifier)
        for identifier in candidate_ids
    ) or len(candidate_ids) != len(set(candidate_ids)):
        raise ArchiveRunArtifactError("business context ids")
    if type(context.get("diagnostics")) is not list or type(context.get("conflicts")) is not list:
        raise ArchiveRunArtifactError("business context collections")
    for item in context["diagnostics"]:
        _diagnostic(item)
    for item in context["conflicts"]:
        _conflict(item)
    relation = entry.get("business_relation")
    if type(relation) is not dict or set(relation) - {
        "relation_type", "candidate_contract_id", "candidate_project_id",
    } or relation.get("relation_type", "") not in {
        "", "invoice_contract", "invoice_project", "contract_project",
    }:
        raise ArchiveRunArtifactError("business relation envelope")
    for key in ("candidate_contract_id", "candidate_project_id"):
        if key in relation and (
            type(relation[key]) is not str or not _ID.fullmatch(relation[key])
        ):
            raise ArchiveRunArtifactError("business relation id")


def validate_archive_intent(intent: dict[str, Any], run_id: str) -> None:
    validate_json_tree(intent)
    if type(intent) is not dict or set(intent) != _INTENT_KEYS:
        raise ArchiveRunArtifactError("archive intent fields")
    _run_hash_ref(intent, run_id)
    if intent.get("schema_version") != "archive_intent.v1":
        raise ArchiveRunArtifactError("archive intent version")
    candidates = intent.get("candidate_target_binding_ids")
    if type(candidates) is not list or any(
        type(identifier) is not str or not _ID.fullmatch(identifier)
        for identifier in candidates
    ) or len(candidates) != len(set(candidates)):
        raise ArchiveRunArtifactError("archive target candidates")
    if intent.get("destination_status") not in {"resolved", "unresolved"} or (
        intent.get("destination_status") == "resolved" and len(candidates) != 1
    ):
        raise ArchiveRunArtifactError("archive destination status")
    if intent.get("intent_id") != f"archive-intent:{intent['content_hash'][:24]}":
        raise ArchiveRunArtifactError("archive intent identity")
    classification = intent.get("classification")
    classification_fields = {
        "document_type", "business_domain", "project_phase", "archive_phase",
        "confidence", "evidence", "requires_review",
    }
    if type(classification) is not dict or set(classification) not in {
        frozenset(classification_fields),
        frozenset(classification_fields | {"validation_errors"}),
    }:
        raise ArchiveRunArtifactError("classification fields")


def validate_archive_action(action: dict[str, Any], run_id: str) -> None:
    validate_json_tree(action)
    if type(action) is not dict or set(action) != _ACTION_KEYS or action.get("run_id") != run_id:
        raise ArchiveRunArtifactError("archive action fields")
    if action.get("confirmed") is not False or action.get("status") != "needs_review":
        raise ArchiveRunArtifactError("archive action must remain review-only")
    intent = action.get("archive_intent")
    if intent is not None:
        validate_archive_intent(intent, run_id)
        if action.get("source_ref") != intent.get("source_ref"):
            raise ArchiveRunArtifactError("action source ref mismatch")
        if action.get("archive_intent_ref") != intent.get("intent_id"):
            raise ArchiveRunArtifactError("action intent ref mismatch")


def _run_hash_ref(value: dict[str, Any], run_id: str) -> None:
    if value.get("run_id") != run_id:
        raise ArchiveRunArtifactError("run id mismatch")
    digest = value.get("content_hash")
    if type(digest) is not str or not _HASH.fullmatch(digest):
        raise ArchiveRunArtifactError("content hash")
    ref = value.get("source_ref")
    if type(ref) is not dict or set(ref) != _REF_KEYS or any(
        type(item) is not str or not item or item != item.strip() for item in ref.values()
    ):
        raise ArchiveRunArtifactError("source ref")


def _diagnostic(item: object) -> dict[str, Any]:
    if type(item) is not dict or set(item) - {"code", "count"}:
        raise ArchiveRunArtifactError("diagnostic schema")
    code = item.get("code")
    if type(code) is not str or not _CODE.fullmatch(code):
        raise ArchiveRunArtifactError("diagnostic code")
    result: dict[str, Any] = {"code": code}
    if "count" in item:
        count = item["count"]
        if type(count) is not int or not 0 <= count <= MAX_COLLECTION:
            raise ArchiveRunArtifactError("diagnostic count")
        result["count"] = count
    return result


def _conflict(item: object) -> dict[str, str]:
    if type(item) is not dict or set(item) != {"code"}:
        raise ArchiveRunArtifactError("conflict schema")
    code = item.get("code")
    if type(code) is not str or not _CODE.fullmatch(code):
        raise ArchiveRunArtifactError("conflict code")
    return {"code": code}


def _contains_sensitive(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return bool(_CREDENTIAL.search(normalized) or _ABSOLUTE_PATH.search(normalized))


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ArchiveRunArtifactError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ArchiveRunArtifactError(f"non-standard JSON constant: {value}")


__all__ = [
    "ArchiveRunArtifactError", "normalize_business_context",
    "normalize_interpretation_output", "strict_json_load", "validate_archive_action",
    "validate_archive_intent", "validate_candidate_interpretation",
    "validate_json_tree", "validate_task5_collection_artifact",
]
