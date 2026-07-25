"""Strict, bounded contracts for Task5 archive-intent run artifacts."""
from __future__ import annotations

import json
import math
import os
import re
import stat
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
MAX_STRING_BYTES = 64 * 1024
MAX_SCALAR_BYTES = 2048
MAX_INPUT_FILE_BYTES = MAX_BYTES
MAX_INTEGER_ABS = 10**18
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
_NATIVE_PARSE_SCHEMA_VERSION = "document.extract.v1"
_NATIVE_PARSE_ROOT_KEYS = {
    "schema_version", "status", "file", "filename", "file_type",
    "document_type", "classification", "paragraph_count", "table_count",
    "table_row_count", "sheet_count", "text_length", "extracted_text",
    "fields", "extract_method", "is_scanned", "ocr", "engine_candidates",
    "needs_human_review",
}
_NATIVE_PARSE_REQUIRED_KEYS = {
    "schema_version", "document_type", "extracted_text", "fields",
}
_NATIVE_FIELD_KEYS = {
    "invoice_number", "invoice_date", "untaxed_amount", "tax_amount",
    "total_amount", "project_code", "project_name", "budget", "deadline",
    "customer", "customer_name", "bid_status", "contract_status",
    "registration_status", "sales_owner", "lifecycle_stage",
    "closed_reason_type", "payer", "payee", "payment_date",
    "payment_amount", "payment_summary", "registration_deadline",
    "bid_deadline", "bid_open_time", "amount", "amount_label",
    "supplier_name", "document_type", "document_date", "quoted_amount",
    "service_rate", "penalty_rate", "bid_fee", "bid_code",
    "business_reference_no", "contract_date", "bid_announcement_date",
    "contract_code", "contract_name", "date", "buyer_name", "buyer_tax_id",
    "seller_name", "seller_tax_id",
}


class ArchiveRunArtifactError(ValueError):
    """An archive-run artifact failed a strict boundary contract."""


def strict_json_load(path: str) -> dict[str, Any]:
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ArchiveRunArtifactError("JSON input is not a regular file")
        if before.st_size > MAX_INPUT_FILE_BYTES:
            raise ArchiveRunArtifactError("JSON input exceeds byte limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                raise ArchiveRunArtifactError("JSON input changed during open")
            raw = os.read(descriptor, MAX_INPUT_FILE_BYTES + 1)
            if os.read(descriptor, 1) or len(raw) > MAX_INPUT_FILE_BYTES:
                raise ArchiveRunArtifactError("JSON input exceeds byte limit")
        finally:
            os.close(descriptor)
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs,
            parse_constant=lambda item: _reject_constant(item),
        )
        validate_json_tree(value, inspect_sensitive=False)
        if type(value) is not dict:
            raise ArchiveRunArtifactError("JSON root must be an object")
        return value
    except ArchiveRunArtifactError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        raise ArchiveRunArtifactError("invalid JSON artifact") from None


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
        elif item is None or type(item) is bool:
            continue
        elif type(item) is int:
            if abs(item) > MAX_INTEGER_ABS:
                raise ArchiveRunArtifactError("JSON integer exceeds range")
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



def validate_archive_execution_plan(payload: dict[str, Any], run_id: str) -> None:
    validate_json_tree(payload, inspect_sensitive=False)
    if set(payload) not in (
        {"schema_version", "run_id", "actions"},
        {"schema_version", "run_id", "actions", "archive_intent_required"},
    ):
        raise ArchiveRunArtifactError("archive plan fields")
    if payload.get("schema_version") != "archive_plan.v1":
        raise ArchiveRunArtifactError("archive plan version")
    if payload.get("run_id") != run_id:
        raise ArchiveRunArtifactError("archive plan run identity")
    if "archive_intent_required" in payload and type(payload["archive_intent_required"]) is not bool:
        raise ArchiveRunArtifactError("archive plan intent flag")
    actions = payload.get("actions")
    if type(actions) is not list or len(actions) > MAX_COLLECTION:
        raise ArchiveRunArtifactError("archive actions")
    for action in actions:
        _validate_legacy_archive_action(action, run_id)


def _validate_legacy_archive_action(action: object, run_id: str) -> None:
    if type(action) is not dict or set(action) - _ACTION_KEYS:
        raise ArchiveRunArtifactError("archive action fields")
    required = {"status", "source_file", "target_path", "blockers"}
    if not required <= set(action):
        raise ArchiveRunArtifactError("archive action required fields")
    if action.get("schema_version", "archive_action.v1") != "archive_action.v1":
        raise ArchiveRunArtifactError("archive action version")
    if action.get("run_id", run_id) != run_id:
        raise ArchiveRunArtifactError("archive action run identity")
    if action.get("status") not in {"ready", "needs_review", "already_archived"}:
        raise ArchiveRunArtifactError("archive action status")
    if type(action.get("source_file")) is not str:
        raise ArchiveRunArtifactError("archive action source")
    if action.get("target_path") is not None and type(action.get("target_path")) is not str:
        raise ArchiveRunArtifactError("archive action target")
    blockers = action.get("blockers")
    if type(blockers) is not list or any(type(item) is not str for item in blockers):
        raise ArchiveRunArtifactError("archive action blockers")
    if "confirmed" in action and type(action["confirmed"]) is not bool:
        raise ArchiveRunArtifactError("archive action confirmation")
    source_ref = action.get("source_ref")
    if source_ref is not None:
        if type(source_ref) is not dict or set(source_ref) != _REF_KEYS:
            raise ArchiveRunArtifactError("archive action source ref")
        _validate_ref(source_ref)
    intent = action.get("archive_intent")
    if intent is not None:
        if type(intent) is not dict:
            raise ArchiveRunArtifactError("archive action intent")
        # A legacy unresolved intent marker is accepted only as non-executable data;
        # complete Task5 intents must satisfy the full contract.
        if set(intent) == {"schema_version", "destination_status"}:
            if intent != {"schema_version": "archive_intent.v1", "destination_status": "unresolved"}:
                raise ArchiveRunArtifactError("archive action intent marker")
        else:
            validate_archive_intent(intent, run_id)
    decision = action.get("archive_decision")
    if decision is not None and type(decision) is not dict:
        raise ArchiveRunArtifactError("archive action decision")
    for key in ("project_name", "document_type", "proposed_name", "target_dir", "archive_intent_ref"):
        if key in action and action[key] is not None and type(action[key]) is not str:
            raise ArchiveRunArtifactError("archive action text field")


def validate_audit_review(payload: dict[str, Any], run_id: str) -> None:
    validate_json_tree(payload, inspect_sensitive=False)
    allowed = {
        "schema_version", "run_id", "agent_role", "status", "timestamp",
        "audit_verdict", "missing_artifacts", "policy_violations",
        "human_confirmation_required", "human_feedback_required",
        "required_feedback_items", "next_actions", "artifact_paths",
        "loop_trace_summary", "artifact_path",
    }
    if set(payload) - allowed or not {"schema_version", "run_id"} <= set(payload):
        raise ArchiveRunArtifactError("audit review fields")
    if payload.get("schema_version") != "audit_review.v1":
        raise ArchiveRunArtifactError("audit review version")
    if payload.get("run_id") != run_id:
        raise ArchiveRunArtifactError("audit review run identity")
    for key in ("human_confirmation_required", "human_feedback_required"):
        if key in payload and type(payload[key]) is not bool:
            raise ArchiveRunArtifactError("audit review boolean")
    for key in ("missing_artifacts", "required_feedback_items", "next_actions"):
        if key in payload and (type(payload[key]) is not list or any(type(item) is not str for item in payload[key])):
            raise ArchiveRunArtifactError("audit review list")
    for key in ("agent_role", "status", "timestamp", "audit_verdict", "artifact_path"):
        if key in payload and type(payload[key]) is not str:
            raise ArchiveRunArtifactError("audit review text")
    if "agent_role" in payload and payload["agent_role"] != "audit_agent":
        raise ArchiveRunArtifactError("audit agent role")
    if "status" in payload and payload["status"] not in {"success", "blocked", "failed"}:
        raise ArchiveRunArtifactError("audit status")
    if "audit_verdict" in payload and payload["audit_verdict"] not in {
        "acceptable", "acceptable_with_warnings", "blocked",
        "needs_human_review", "needs_human_feedback",
    }:
        raise ArchiveRunArtifactError("audit verdict")
    if "artifact_paths" in payload:
        paths = payload["artifact_paths"]
        if type(paths) is not dict or any(type(key) is not str or type(value) is not str for key, value in paths.items()):
            raise ArchiveRunArtifactError("audit artifact paths")
    if "loop_trace_summary" in payload:
        summary = payload["loop_trace_summary"]
        if type(summary) is not dict or set(summary) != {"trace_id", "status", "round_count"}:
            raise ArchiveRunArtifactError("audit trace summary")
        if type(summary["trace_id"]) is not str or type(summary["status"]) is not str or type(summary["round_count"]) is not int or summary["round_count"] < 0:
            raise ArchiveRunArtifactError("audit trace summary types")
    if "policy_violations" in payload:
        violations = payload["policy_violations"]
        if type(violations) is not list:
            raise ArchiveRunArtifactError("audit policy violations")
        for violation in violations:
            if type(violation) is not dict or set(violation) != {"id", "severity", "rule", "message"} or any(type(value) is not str for value in violation.values()):
                raise ArchiveRunArtifactError("audit policy violation")


def validate_review_queue(payload: dict[str, Any], run_id: str) -> None:
    validate_json_tree(payload, inspect_sensitive=False)
    required = {"schema_version", "run_id", "status", "items"}
    if not required <= set(payload) or set(payload) - (required | {"feedback_summary"}):
        raise ArchiveRunArtifactError("review queue fields")
    if payload.get("schema_version") != "review_queue.v2":
        raise ArchiveRunArtifactError("review queue version")
    if payload.get("run_id") != run_id:
        raise ArchiveRunArtifactError("review queue run identity")
    if payload.get("status") not in {"clear", "needs_review", "reviewed"}:
        raise ArchiveRunArtifactError("review queue status")
    items = payload.get("items")
    if type(items) is not list or len(items) > MAX_COLLECTION:
        raise ArchiveRunArtifactError("review queue items")
    allowed = {
        "id", "item_id", "run_id", "type", "severity", "risk", "question",
        "feedback_type", "allowed_decisions", "recommended_decision", "evidence",
        "feedback_status", "feedback_ids", "feedback_decisions", "source_ref",
        "blockers", "recommended_action", "project_name", "source_file",
        "target_path", "missing_fields", "risk_reasons", "recommended_actions",
        "project_overview_md", "file", "reason", "message", "verdict",
        "block_reason", "low_confidence_items", "verification_path",
        "feedback_updated_at",
    }
    for item in items:
        if type(item) is not dict or set(item) - allowed:
            raise ArchiveRunArtifactError("review queue item fields")
        if "run_id" in item and item["run_id"] != run_id:
            raise ArchiveRunArtifactError("review queue item run identity")
        identifier = item.get("id", item.get("item_id"))
        if identifier is not None and type(identifier) is not str:
            raise ArchiveRunArtifactError("review queue item identity")
        for key in ("allowed_decisions", "feedback_ids", "feedback_decisions", "blockers", "missing_fields", "risk_reasons", "recommended_actions"):
            if key in item and (type(item[key]) is not list or any(type(value) is not str for value in item[key])):
                raise ArchiveRunArtifactError("review queue item list")
        if "evidence" in item and type(item["evidence"]) is not list:
            raise ArchiveRunArtifactError("review queue evidence")
        if "source_ref" in item:
            source_ref = item["source_ref"]
            if type(source_ref) is not dict or set(source_ref) != _REF_KEYS:
                raise ArchiveRunArtifactError("review queue source ref")
            _validate_ref(source_ref)
        if "feedback_status" in item and item["feedback_status"] not in {"pending", "feedback_received"}:
            raise ArchiveRunArtifactError("review queue feedback status")
        if "low_confidence_items" in item and type(item["low_confidence_items"]) is not list:
            raise ArchiveRunArtifactError("review queue low confidence items")
        for key, value in item.items():
            if key not in {"evidence", "source_ref", "feedback_decisions", "allowed_decisions", "feedback_ids", "blockers", "missing_fields", "risk_reasons", "recommended_actions", "low_confidence_items"} and value is not None and type(value) is not str:
                raise ArchiveRunArtifactError("review queue item scalar")
    if "feedback_summary" in payload:
        summary = payload["feedback_summary"]
        if type(summary) is not dict or set(summary) != {"updated", "pending", "updated_at"}:
            raise ArchiveRunArtifactError("review feedback summary")
        if type(summary["updated"]) is not int or summary["updated"] < 0:
            raise ArchiveRunArtifactError("review feedback count")
        if type(summary["pending"]) is not list or any(type(item) is not str for item in summary["pending"]):
            raise ArchiveRunArtifactError("review feedback pending")
        if type(summary["updated_at"]) is not str:
            raise ArchiveRunArtifactError("review feedback timestamp")

def normalize_native_parse_output(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ArchiveRunArtifactError("native parse root")
    if not _NATIVE_PARSE_REQUIRED_KEYS <= set(value) or set(value) - _NATIVE_PARSE_ROOT_KEYS:
        raise ArchiveRunArtifactError("native parse fields")
    if value.get("schema_version") != _NATIVE_PARSE_SCHEMA_VERSION:
        raise ArchiveRunArtifactError("native parse version")
    if value.get("status", "success") != "success":
        raise ArchiveRunArtifactError("native parse status")
    document_type = value.get("document_type")
    fields = value.get("fields")
    classification = value.get("classification")
    extracted_text = value.get("extracted_text")
    if not _bounded_text(document_type, 128):
        raise ArchiveRunArtifactError("native document type")
    if type(fields) is not dict or type(extracted_text) is not str:
        raise ArchiveRunArtifactError("native parse types")
    for key in ("file", "filename", "file_type", "extract_method"):
        if key in value and type(value[key]) is not str:
            raise ArchiveRunArtifactError("native parse metadata text")
    for key in ("paragraph_count", "table_count", "table_row_count", "sheet_count", "text_length"):
        if key in value and (
            type(value[key]) is not int or not 0 <= value[key] <= MAX_INTEGER_ABS
        ):
            raise ArchiveRunArtifactError("native parse metadata integer")
    for key in ("is_scanned", "needs_human_review"):
        if key in value and type(value[key]) is not bool:
            raise ArchiveRunArtifactError("native parse metadata boolean")
    if "ocr" in value and type(value["ocr"]) is not dict:
        raise ArchiveRunArtifactError("native parse OCR diagnostics")
    if "engine_candidates" in value and type(value["engine_candidates"]) is not list:
        raise ArchiveRunArtifactError("native parse engine diagnostics")
    safe_root = dict(value)
    safe_root.pop("file", None)
    validate_json_tree(safe_root)
    _serialized_bytes(value)
    candidate_fields = _scalar_candidate_fields(fields)
    return {
        "document_type": document_type,
        "classification": classification,
        "candidate_fields": candidate_fields,
        "text": extracted_text,
    }


def validate_extracted_document_artifact(
    payload: dict[str, Any], run_id: str,
) -> None:
    validate_json_tree(payload)
    _serialized_bytes(payload)
    expected = {
        "schema_version", "run_id", "parse_artifact_ref", "source_ref",
        "content_hash", "document_type", "classification", "candidate_fields",
        "text_length",
    }
    if type(payload) is not dict or set(payload) != expected:
        raise ArchiveRunArtifactError("extracted artifact fields")
    if payload.get("schema_version") != "file_organization.extracted_document.v1":
        raise ArchiveRunArtifactError("extracted artifact version")
    _run_hash_ref(payload, run_id)
    if payload.get("parse_artifact_ref") != f"artifact:parsed:{payload['content_hash'][:24]}":
        raise ArchiveRunArtifactError("extracted parse identity")
    if not _bounded_text(payload.get("document_type"), 128):
        raise ArchiveRunArtifactError("extracted document type")
    fields = payload.get("candidate_fields")
    if type(fields) is not dict or fields != _scalar_candidate_fields(fields):
        raise ArchiveRunArtifactError("extracted candidate fields")
    _validate_classification(payload.get("classification"))
    text_length = payload.get("text_length")
    if type(text_length) is not int or not 0 <= text_length <= MAX_STRING_BYTES:
        raise ArchiveRunArtifactError("extracted text length")


def _scalar_candidate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    if set(fields) - _NATIVE_FIELD_KEYS:
        raise ArchiveRunArtifactError("native candidate field names")
    result: dict[str, Any] = {}
    for key, item in fields.items():
        if item is None:
            continue
        if type(item) is str:
            if not _bounded_text(item, MAX_SCALAR_BYTES):
                raise ArchiveRunArtifactError("candidate scalar")
            result[key] = item
        elif type(item) in (int, float):
            if type(item) is float and not math.isfinite(item):
                raise ArchiveRunArtifactError("candidate number")
            if abs(item) > 10**15:
                raise ArchiveRunArtifactError("candidate number range")
            result[key] = item
        else:
            raise ArchiveRunArtifactError("candidate scalar type")
    return result


def _validate_classification(value: object) -> None:
    base_fields = {
        "document_type", "business_domain", "project_phase", "archive_phase",
        "confidence", "evidence", "requires_review",
    }
    if type(value) is not dict or set(value) not in {
        frozenset(base_fields), frozenset(base_fields | {"validation_errors"}),
    }:
        raise ArchiveRunArtifactError("classification fields")
    for key in ("document_type", "business_domain"):
        if not _bounded_text(value.get(key), 128):
            raise ArchiveRunArtifactError("classification text")
    for key in ("project_phase", "archive_phase"):
        if value.get(key) is not None and not _bounded_text(value.get(key), 128):
            raise ArchiveRunArtifactError("classification phase")
    confidence = value.get("confidence")
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ArchiveRunArtifactError("classification confidence")
    if type(value.get("requires_review")) is not bool:
        raise ArchiveRunArtifactError("classification review flag")
    for key in ("evidence", "validation_errors"):
        if key not in value:
            continue
        items = value[key]
        if type(items) is not list or any(not _bounded_text(item, 512) for item in items):
            raise ArchiveRunArtifactError("classification list")


def _bounded_text(value: object, limit: int) -> bool:
    return (
        type(value) is str
        and value == value.strip()
        and len(value.encode("utf-8")) <= limit
        and not _contains_sensitive(value)
    )


def _serialized_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ArchiveRunArtifactError("JSON serialization") from None
    if len(encoded) > MAX_BYTES:
        raise ArchiveRunArtifactError("serialized artifact size")
    return encoded


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
    _validate_classification(intent.get("classification"))


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
    _validate_ref(value.get("source_ref"))


def _validate_ref(ref: object) -> None:
    if type(ref) is not dict or set(ref) != _REF_KEYS or any(
        type(item) is not str or not item or item != item.strip() or _contains_sensitive(item)
        for item in ref.values()
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
    "validate_archive_execution_plan", "validate_audit_review", "validate_review_queue",
    "normalize_native_parse_output",
    "normalize_interpretation_output", "strict_json_load", "validate_archive_action",
    "validate_archive_intent", "validate_candidate_interpretation",
    "validate_extracted_document_artifact", "validate_json_tree",
    "validate_task5_collection_artifact",
]
