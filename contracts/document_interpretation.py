"""Fail-closed contract for ``candidate_document_interpretation.v1``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from typing import Any


DOCUMENT_INTERPRETATION_SCHEMA_VERSION = "candidate_document_interpretation.v1"
_MAX_RESPONSE_BYTES = 32_000
_ROOT_FIELDS = {
    "schema_version", "status", "document_type", "fields", "relations", "evidence",
    "confidence", "interpreter", "model", "prompt_version", "policy_version", "blocked_reason",
}
_EVIDENCE_FIELDS = {"kind", "candidate_id", "field"}
_RELATION_FIELDS = {"relation_type", "target_candidate_id"}
_STATUSES = {"success", "needs_review", "blocked"}


class DocumentInterpretationSchemaError(ValueError):
    """Raised when a model response does not satisfy the public contract."""


def parse_candidate_document_interpretation(payload: object) -> dict[str, Any]:
    """Parse a complete JSON response, rejecting duplicate keys and extensions."""
    decoded = _decode(payload)
    _validate_response_size(decoded)
    _validate_value(decoded)
    root = _mapping(decoded)
    _only(root, _ROOT_FIELDS)
    _required(root, _ROOT_FIELDS - {"blocked_reason"})
    if root["schema_version"] != DOCUMENT_INTERPRETATION_SCHEMA_VERSION:
        raise DocumentInterpretationSchemaError("unsupported schema version")
    if root["status"] not in _STATUSES:
        raise DocumentInterpretationSchemaError("invalid status")
    if not _string(root["document_type"]):
        raise DocumentInterpretationSchemaError("invalid document type")
    _fields(root["fields"])
    _relations(root["relations"])
    _evidence(root["evidence"])
    _confidence(root["confidence"])
    for key in ("interpreter", "model", "prompt_version", "policy_version"):
        if not _string(root[key]):
            raise DocumentInterpretationSchemaError(f"invalid {key}")
    if root["status"] == "blocked":
        if not _string(root.get("blocked_reason")):
            raise DocumentInterpretationSchemaError("blocked status requires a reason")
    elif "blocked_reason" in root:
        raise DocumentInterpretationSchemaError("non-blocked status cannot include a reason")
    return dict(root)


def _decode(payload: object) -> object:
    if isinstance(payload, str):
        try:
            return json.loads(payload, object_pairs_hook=_without_duplicate_keys, parse_constant=_reject_non_finite)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise DocumentInterpretationSchemaError("invalid JSON response") from error
    return payload

def _validate_response_size(value: object) -> None:
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise DocumentInterpretationSchemaError("non-JSON response") from error
    if len(serialized.encode("utf-8")) > _MAX_RESPONSE_BYTES:
        raise DocumentInterpretationSchemaError("response exceeds the safe size limit")



def _without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DocumentInterpretationSchemaError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise DocumentInterpretationSchemaError(f"non-finite number: {value}")


def _validate_value(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise DocumentInterpretationSchemaError("non-finite number")
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise DocumentInterpretationSchemaError("non-string JSON key")
        for item in value.values():
            _validate_value(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _validate_value(item)


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DocumentInterpretationSchemaError("expected object")
    return value


def _only(value: Mapping[str, Any], allowed: set[str]) -> None:
    if set(value).difference(allowed):
        raise DocumentInterpretationSchemaError("unknown field")


def _required(value: Mapping[str, Any], required: set[str]) -> None:
    if not required.issubset(value):
        raise DocumentInterpretationSchemaError("missing required field")


def _string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _fields(value: object) -> None:
    fields = _mapping(value)
    if not all(isinstance(key, str) and key.strip() == key for key in fields):
        raise DocumentInterpretationSchemaError("invalid field name")


def _relations(value: object) -> None:
    if not isinstance(value, list):
        raise DocumentInterpretationSchemaError("relations must be an array")
    for relation in value:
        item = _mapping(relation)
        _only(item, _RELATION_FIELDS)
        _required(item, _RELATION_FIELDS)
        if not all(_string(item[key]) for key in _RELATION_FIELDS):
            raise DocumentInterpretationSchemaError("invalid relation")


def _evidence(value: object) -> None:
    if not isinstance(value, list) or not value:
        raise DocumentInterpretationSchemaError("evidence is required")
    for evidence in value:
        item = _mapping(evidence)
        _only(item, _EVIDENCE_FIELDS)
        _required(item, _EVIDENCE_FIELDS)
        if not all(_string(item[key]) for key in _EVIDENCE_FIELDS):
            raise DocumentInterpretationSchemaError("invalid evidence")


def _confidence(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentInterpretationSchemaError("invalid confidence")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise DocumentInterpretationSchemaError("confidence out of range")


__all__ = ["DOCUMENT_INTERPRETATION_SCHEMA_VERSION", "DocumentInterpretationSchemaError", "parse_candidate_document_interpretation"]
