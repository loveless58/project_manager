"""Fail-closed contract for a local, review-only agent judgement exchange."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping
import unicodedata

from contracts.document_interpretation import (
    DocumentInterpretationSchemaError,
    parse_candidate_document_interpretation,
)

REQUEST_SCHEMA_VERSION = "agent_judgement_request.v1"
RESPONSE_SCHEMA_VERSION = "agent_judgement_response.v1"
MAX_BYTES, MAX_DEPTH, MAX_NODES, MAX_COLLECTION, MAX_STRING = 64_000, 32, 4_096, 256, 4_096

_RUN_ID = re.compile(r"^run_[0-9a-f]{32}$")
_REQUEST_ID = re.compile(r"^agent-request:[A-Za-z0-9_.:-]{1,128}$")
_ADAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_ARTIFACT_REF = re.compile(r"^artifact:[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9_.:-]{1,128}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_CREDENTIAL = re.compile(r"(?<!\w)(?:(?:authorization|token|api[_-]?(?:key|token)|password)\s*[:=]\s*\S|bearer\s+\S)", re.IGNORECASE)
_PHYSICAL_PATH = re.compile(r"(?:^|[\s\"'(=])(?:file:///|//[^/\s]+/[^/\s]+|[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+|/(?!/))")

_REQUEST_ROOT = {"schema_version", "run_id", "request_id", "interpretation_request", "request_hash"}
_RESPONSE_ROOT = {"schema_version", "run_id", "request_id", "request_hash", "interpreter", "model", "created_at", "interpretation"}
_EVIDENCE_FIELDS = {"buyer.tax_id", "seller.tax_id", "buyer.name", "seller.name", "contract_code", "project_code", "amount", "date"}
_DOCUMENT_TYPES = {"invoice", "project", "contract", "bid", "tender", "other", "发票", "项目", "合同", "投标", "招标", "其他"}
_CONTEXT_STATUSES = {"matched", "needs_review", "blocked"}
_DIAGNOSTICS = {"BUSINESS_CONTEXT.CANDIDATES_FOUND", "BUSINESS_CONTEXT.NO_CANDIDATES", "BUSINESS_CONTEXT.CONFLICTS_FOUND"}
_FIELD_NAMES = {"invoice_number", "invoice_date", "amount", "tax_amount", "total_amount", "buyer_name", "buyer_tax_id", "seller_name", "seller_tax_id", "project_code", "project_name", "contract_code", "contract_name"}


class AgentJudgementSchemaError(ValueError):
    """Public fail-closed schema error for every agent exchange boundary."""


def canonical_hash(payload: Mapping[str, Any]) -> str:
    try:
        value = _normalize_json(payload)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        raise AgentJudgementSchemaError("non-canonical payload") from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_run_id(run_id: str) -> str:
    if type(run_id) is not str or not _RUN_ID.fullmatch(run_id):
        raise AgentJudgementSchemaError("run_id")
    return run_id


def validate_request_id(request_id: str) -> str:
    if type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id):
        raise AgentJudgementSchemaError("request_id")
    return request_id


def build_agent_judgement_request(*, run_id: str, request_id: str, interpretation_request: Mapping[str, Any]) -> dict[str, Any]:
    safe_request = validate_interpretation_request(interpretation_request)
    envelope: dict[str, Any] = {"schema_version": REQUEST_SCHEMA_VERSION, "run_id": validate_run_id(run_id), "request_id": validate_request_id(request_id), "interpretation_request": safe_request}
    envelope["request_hash"] = canonical_hash(envelope)
    return envelope


def parse_agent_judgement_response(payload: object, *, request: Mapping[str, Any]) -> dict[str, Any]:
    try:
        bound_request = _validate_request_envelope(request)
        value = _normalize_json(_decode(payload))
        _validate_tree(value)
        if type(value) is not dict:
            raise AgentJudgementSchemaError("response root")
        _exact_keys(value, _RESPONSE_ROOT)
        if value["schema_version"] != RESPONSE_SCHEMA_VERSION:
            raise AgentJudgementSchemaError("response schema_version")
        if (value["run_id"], value["request_id"], value["request_hash"]) != (bound_request["run_id"], bound_request["request_id"], bound_request["request_hash"]):
            raise AgentJudgementSchemaError("response request binding")
        _adapter_identity(value["interpreter"], "interpreter")
        _adapter_identity(value["model"], "model")
        _created_at(value["created_at"])
        interpretation = parse_candidate_document_interpretation(value["interpretation"])
        if interpretation["interpreter"] != value["interpreter"] or interpretation["model"] != value["model"]:
            raise AgentJudgementSchemaError("response identity binding")
        _bind_interpretation(interpretation, bound_request["interpretation_request"])
        return _copy_json(value)
    except (AgentJudgementSchemaError, DocumentInterpretationSchemaError):
        raise AgentJudgementSchemaError("invalid agent judgement response") from None
    except Exception:
        raise AgentJudgementSchemaError("invalid agent judgement response") from None


def validate_interpretation_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = _normalize_json(payload)
        _validate_tree(value)
        if type(value) is not dict:
            raise AgentJudgementSchemaError("interpretation request root")
        _exact_keys(value, {"schema_version", "parse_artifact_ref", "document", "business_context"})
        if value["schema_version"] != "document_interpretation_evidence_pack.v1":
            raise AgentJudgementSchemaError("interpretation request schema_version")
        if type(value["parse_artifact_ref"]) is not str or not _ARTIFACT_REF.fullmatch(value["parse_artifact_ref"]):
            raise AgentJudgementSchemaError("parse_artifact_ref")
        _document(value["document"])
        _business_context(value["business_context"])
        return _copy_json(value)
    except AgentJudgementSchemaError:
        raise
    except Exception:
        raise AgentJudgementSchemaError("invalid interpretation request") from None


def _validate_request_envelope(request: Mapping[str, Any]) -> dict[str, Any]:
    value = _normalize_json(request)
    _validate_tree(value)
    if type(value) is not dict:
        raise AgentJudgementSchemaError("request type")
    _exact_keys(value, _REQUEST_ROOT)
    if value["schema_version"] != REQUEST_SCHEMA_VERSION:
        raise AgentJudgementSchemaError("request schema_version")
    validate_run_id(value["run_id"])
    validate_request_id(value["request_id"])
    safe_request = validate_interpretation_request(value["interpretation_request"])
    request_hash = value["request_hash"]
    if type(request_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", request_hash):
        raise AgentJudgementSchemaError("request_hash")
    unhashed = {"schema_version": value["schema_version"], "run_id": value["run_id"], "request_id": value["request_id"], "interpretation_request": safe_request}
    if canonical_hash(unhashed) != request_hash:
        raise AgentJudgementSchemaError("request hash binding")
    return {**unhashed, "request_hash": request_hash}


def _document(value: object) -> None:
    if type(value) is not dict:
        raise AgentJudgementSchemaError("document")
    _exact_keys(value, {"document_type_hint", "candidate_fields", "text_segments"})
    if type(value["document_type_hint"]) is not str or value["document_type_hint"] not in _DOCUMENT_TYPES:
        raise AgentJudgementSchemaError("document type")
    _candidate_fields(value["candidate_fields"])
    segments = value["text_segments"]
    if type(segments) is not list or len(segments) > 64:
        raise AgentJudgementSchemaError("text segments")
    for segment in segments:
        if type(segment) is not dict:
            raise AgentJudgementSchemaError("text segment")
        _exact_keys(segment, {"id", "text"})
        _id(segment["id"], "text segment id")
        _text(segment["text"], "text segment text")


def _candidate_fields(value: object) -> None:
    if type(value) is not dict or set(value) - _FIELD_NAMES:
        raise AgentJudgementSchemaError("candidate fields")
    for name, item in value.items():
        if name in {"amount", "tax_amount", "total_amount"}:
            _finite_number(item, "candidate amount")
        else:
            _semantic_text(item, "candidate field")


def _business_context(value: object) -> None:
    if type(value) is not dict:
        raise AgentJudgementSchemaError("business context")
    _exact_keys(value, {"status", "candidates", "evidence", "conflicts", "diagnostics"})
    if type(value["status"]) is not str or value["status"] not in _CONTEXT_STATUSES:
        raise AgentJudgementSchemaError("business context status")
    candidates, evidence, diagnostics = value["candidates"], value["evidence"], value["diagnostics"]
    if type(candidates) is not list or type(evidence) is not list or type(diagnostics) is not list:
        raise AgentJudgementSchemaError("business context collections")
    if len(candidates) > MAX_COLLECTION or len(evidence) > MAX_COLLECTION or len(diagnostics) > MAX_COLLECTION or value["conflicts"] != []:
        raise AgentJudgementSchemaError("business context collection limit")
    ids: set[str] = set()
    for candidate in candidates:
        if type(candidate) is not dict:
            raise AgentJudgementSchemaError("candidate")
        _allowed_keys(candidate, {"id", "document_type", "parties", "facts"})
        if not {"id", "document_type"} <= set(candidate):
            raise AgentJudgementSchemaError("candidate required")
        _id(candidate["id"], "candidate id")
        if candidate["id"] in ids:
            raise AgentJudgementSchemaError("duplicate candidate id")
        ids.add(candidate["id"])
        if type(candidate["document_type"]) is not str or candidate["document_type"] not in _DOCUMENT_TYPES:
            raise AgentJudgementSchemaError("candidate document type")
        if "parties" in candidate:
            _candidate_parties(candidate["parties"])
        if "facts" in candidate:
            _candidate_facts(candidate["facts"])
    seen_evidence: set[tuple[str, str, str]] = set()
    for item in evidence:
        if type(item) is not dict:
            raise AgentJudgementSchemaError("evidence")
        _exact_keys(item, {"kind", "candidate_id", "field"})
        if item["kind"] != "business_context":
            raise AgentJudgementSchemaError("evidence kind")
        _id(item["candidate_id"], "evidence candidate")
        if item["candidate_id"] not in ids or item["field"] not in _EVIDENCE_FIELDS:
            raise AgentJudgementSchemaError("evidence binding")
        marker = (item["kind"], item["candidate_id"], item["field"])
        if marker in seen_evidence:
            raise AgentJudgementSchemaError("duplicate evidence")
        seen_evidence.add(marker)
    if not evidence:
        raise AgentJudgementSchemaError("empty evidence")
    for item in diagnostics:
        if type(item) is not dict:
            raise AgentJudgementSchemaError("diagnostic")
        _exact_keys(item, {"code"})
        if item["code"] not in _DIAGNOSTICS:
            raise AgentJudgementSchemaError("diagnostic code")


def _candidate_parties(value: object) -> None:
    if type(value) is not dict or set(value) - {"buyer", "seller"}:
        raise AgentJudgementSchemaError("candidate parties")
    for party in value.values():
        if type(party) is not dict or set(party) - {"name", "tax_id"}:
            raise AgentJudgementSchemaError("candidate party")
        for item in party.values():
            _text(item, "candidate party")


def _candidate_facts(value: object) -> None:
    if type(value) is not dict or set(value) - {"contract_code", "project_code", "amount", "date"}:
        raise AgentJudgementSchemaError("candidate facts")
    for name, item in value.items():
        if name == "amount":
            _finite_number(item, "candidate fact amount")
        else:
            _text(item, "candidate fact")


def _bind_interpretation(interpretation: Mapping[str, Any], request: Mapping[str, Any]) -> None:
    context = request["business_context"]
    ids = {item["id"] for item in context["candidates"]}
    evidence = {(item["kind"], item["candidate_id"], item["field"]) for item in context["evidence"]}
    for relation in interpretation["relations"]:
        if relation["target_candidate_id"] not in ids:
            raise AgentJudgementSchemaError("relation request binding")
    for item in interpretation["evidence"]:
        if (item["kind"], item["candidate_id"], item["field"]) not in evidence:
            raise AgentJudgementSchemaError("interpretation evidence binding")


def _decode(payload: object) -> object:
    if type(payload) is not str:
        return payload
    if len(payload.encode("utf-8")) > MAX_BYTES:
        raise AgentJudgementSchemaError("response size")
    try:
        return json.loads(payload, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except AgentJudgementSchemaError:
        raise
    except Exception:
        raise AgentJudgementSchemaError("response json") from None


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AgentJudgementSchemaError("duplicate key")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise AgentJudgementSchemaError("nonfinite number")


def _normalize_json(value: object, depth: int = 1) -> object:
    if depth > MAX_DEPTH:
        raise AgentJudgementSchemaError("tree limit")
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        try:
            for key, item in value.items():
                if type(key) is not str or key in result:
                    raise AgentJudgementSchemaError("key type")
                result[key] = _normalize_json(item, depth + 1)
        except AgentJudgementSchemaError:
            raise
        except Exception:
            raise AgentJudgementSchemaError("mapping") from None
        return result
    if type(value) is list:
        return [_normalize_json(item, depth + 1) for item in value]
    return value


def _validate_tree(root: object) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    nodes = 0
    string_bytes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise AgentJudgementSchemaError("tree limit")
        if type(value) is str:
            _safe_string(value)
            string_bytes += len(value.encode("utf-8"))
        elif value is None or type(value) in {bool, int}:
            pass
        elif type(value) is float:
            if not math.isfinite(value):
                raise AgentJudgementSchemaError("nonfinite number")
        elif type(value) is list:
            if len(value) > MAX_COLLECTION:
                raise AgentJudgementSchemaError("array limit")
            stack.extend((item, depth + 1) for item in value)
        elif type(value) is dict:
            if len(value) > MAX_COLLECTION:
                raise AgentJudgementSchemaError("object limit")
            for key, item in value.items():
                if type(key) is not str:
                    raise AgentJudgementSchemaError("key type")
                _safe_key(key)
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        else:
            raise AgentJudgementSchemaError("non-json type")
        if string_bytes > MAX_BYTES:
            raise AgentJudgementSchemaError("size")


def _safe_string(value: str) -> None:
    if len(value.encode("utf-8")) > MAX_STRING:
        raise AgentJudgementSchemaError("string limit")
    normalized = unicodedata.normalize("NFKC", value).casefold()
    if _CREDENTIAL.search(normalized):
        raise AgentJudgementSchemaError("credential")
    if _looks_physical_path(value):
        raise AgentJudgementSchemaError("physical path")


def _safe_key(value: str) -> None:
    _safe_string(value)
    if _looks_sensitive_key(unicodedata.normalize("NFKC", value).casefold()):
        raise AgentJudgementSchemaError("credential key")


def _looks_sensitive_key(value: str) -> bool:
    return any(token in value for token in ("authorization", "credential", "token", "api_key", "api-key", "password"))


def _looks_physical_path(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return bool(
        _PHYSICAL_PATH.search(value)
        or re.search(r"(?:^|[^a-z0-9_])(?:file|ref):(?:/{1,3}|\\)", normalized)
        or re.search(r"(?:^|[\s\"'(=])(?:[^/\\\s]+[/\\])+(?:[^/\\\s]+\.(?:pdf|docx|xlsx|xls|csv|xml|json|md|txt))\b", value, re.IGNORECASE)
    )


def _semantic_text(value: object, label: str) -> None:
    _text(value, label)
    if "/" in value or "\\" in value or re.search(r"(?i)(?:file|ref):", value):
        raise AgentJudgementSchemaError("physical path")


def _text(value: object, label: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise AgentJudgementSchemaError(label)
    _safe_string(value)


def _id(value: object, label: str) -> None:
    if type(value) is not str or not _ID.fullmatch(value):
        raise AgentJudgementSchemaError(label)


def _finite_number(value: object, label: str) -> None:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise AgentJudgementSchemaError(label)


def _adapter_identity(value: object, label: str) -> None:
    if type(value) is not str or not _ADAPTER_ID.fullmatch(value):
        raise AgentJudgementSchemaError(label)
    _safe_string(value)


def _created_at(value: object) -> None:
    if type(value) is not str or not _UTC_TIMESTAMP.fullmatch(value):
        raise AgentJudgementSchemaError("created_at")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise AgentJudgementSchemaError("created_at") from None
    if parsed.tzinfo != timezone.utc:
        raise AgentJudgementSchemaError("created_at")


def _exact_keys(value: Mapping[str, object], expected: set[str]) -> None:
    if set(value) != expected:
        raise AgentJudgementSchemaError("root fields")


def _allowed_keys(value: Mapping[str, object], allowed: set[str]) -> None:
    if set(value) - allowed:
        raise AgentJudgementSchemaError("unknown field")


def _copy_json(value: object) -> dict[str, Any]:
    try:
        result = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        raise AgentJudgementSchemaError("json copy") from None
    if type(result) is not dict:
        raise AgentJudgementSchemaError("root")
    return result


__all__ = ["AgentJudgementSchemaError", "MAX_BYTES", "MAX_COLLECTION", "MAX_DEPTH", "MAX_NODES", "MAX_STRING", "REQUEST_SCHEMA_VERSION", "RESPONSE_SCHEMA_VERSION", "build_agent_judgement_request", "canonical_hash", "parse_agent_judgement_response", "validate_interpretation_request", "validate_request_id", "validate_run_id"]
