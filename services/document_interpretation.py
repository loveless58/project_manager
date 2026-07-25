"""Controlled, fail-closed document interpretation boundary."""

from __future__ import annotations

from datetime import date
import json
import math
import re
import unicodedata
from typing import Any

from contracts.document_interpretation import (
    DocumentInterpretationSchemaError,
    parse_candidate_document_interpretation,
)
from platform_core.models import BusinessContextEvidence, BusinessContextQuery


SCHEMA_VERSION = "candidate_document_interpretation.v1"
PROMPT_VERSION = "document_interpretation.v1"
POLICY_VERSION = "document_interpretation_policy.v1"

_REQUEST_FAILED = "DOCUMENT_INTERPRETATION.REQUEST_FAILED"
_ALLOWED_ERROR_CODES = {
    _REQUEST_FAILED,
    "DOCUMENT_INTERPRETATION.LLM.REQUEST_FAILED",
    "DOCUMENT_INTERPRETATION.LLM.RESPONSE_INVALID",
}
_INPUT_KEYS = {
    "parse_artifact_ref",
    "document_type_hint",
    "candidate_fields",
    "text_segments",
}
_DIRECT_FIELDS = {
    "invoice_number",
    "invoice_date",
    "amount",
    "tax_amount",
    "total_amount",
    "buyer_name",
    "buyer_tax_id",
    "seller_name",
    "seller_tax_id",
    "project_code",
    "project_name",
    "contract_code",
    "contract_name",
}
_INPUT_FIELDS = _DIRECT_FIELDS | {
    "buyer",
    "seller",
    "date",
    "line_items",
    "path",
    "path_hint",
}
_AMOUNT_FIELDS = {"amount", "tax_amount", "total_amount"}
_DATE_FIELDS = {"invoice_date", "date"}
_TAX_FIELDS = {"buyer_tax_id", "seller_tax_id"}
_FACTS = {"contract_code", "project_code", "amount", "date"}
_EVIDENCE = {
    "buyer.tax_id",
    "seller.tax_id",
    "buyer.name",
    "seller.name",
    "contract_code",
    "project_code",
    "amount",
    "date",
}
_DOCUMENT_TYPES = {
    "invoice",
    "project",
    "contract",
    "bid",
    "tender",
    "other",
    "发票",
    "项目",
    "合同",
    "投标",
    "招标",
    "其他",
}
_CONTEXT_STATUSES = {"matched", "needs_review", "blocked"}
_DIAGNOSTICS = {
    "BUSINESS_CONTEXT.CANDIDATES_FOUND",
    "BUSINESS_CONTEXT.NO_CANDIDATES",
    "BUSINESS_CONTEXT.CONFLICTS_FOUND",
}
_CANDIDATE_KEYS = {
    "id",
    "document_type",
    "parties",
    "facts",
    "documents",
    "path_hints",
    "match_score",
}
_ARTIFACT = re.compile(
    r"^artifact:[a-z][a-z0-9_-]{0,31}:[A-Za-z0-9_.:-]{1,128}$"
)
_ADAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TAX_ID = re.compile(r"^[A-Za-z0-9]{8,32}$")
_CONTENT_HASH = re.compile(r"^[0-9a-fA-F]{64}$")
_DOCUMENT_KEYS = {
    "path", "document_version_id", "content_hash", "media_type", "page_count",
    "requires_structure_index",
}
_EVIDENCE_KEYS = {"kind", "candidate_id", "field", "query_value", "candidate_value", "weight"}
_CREDENTIAL = re.compile(
    r"(?<!\w)(?:(?:authorization|token|api[_-]?(?:key|token)|password)\s*[:=]\s*\S|bearer\s+\S)",
    re.IGNORECASE,
)
_DOUBLE_SLASH = "/" * 2
_FILE_URI = "file:" + "/" * 3
_ABSOLUTE_PATH = re.compile(
    rf"(?:^|[\s\"'(=])(?:{re.escape(_FILE_URI)}|{re.escape(_DOUBLE_SLASH)}[^/\s]+/[^/\s]+|[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+|/(?!/))"
)

_MAX_REQUEST_BYTES = 64 * 1024
_MAX_FIELD_BYTES = 64 * 1024
_MAX_COLLECTION = 256
_MAX_TEXT_SEGMENTS = 64
_MAX_TEXT_BYTES = 4 * 1024
_MAX_DEPTH = 32
_MAX_NODES = 4_096


class DocumentInterpretationService:
    """Build a bounded evidence request and fail closed on every boundary error."""

    def __init__(self, retrieval_service: Any, interpreter: Any) -> None:
        self.retrieval_service = retrieval_service
        self.interpreter = interpreter

    def interpret(self, evidence_pack: Any) -> dict[str, Any]:
        ref = (
            _artifact(evidence_pack.get("parse_artifact_ref"))
            if type(evidence_pack) is dict
            else ""
        )
        try:
            _validate_adapter(self.interpreter)
            document = _document(evidence_pack)
            context = self.retrieval_service.find_business_candidates(
                BusinessContextQuery(
                    document["document_type_hint"],
                    document["candidate_fields"],
                    document["text_segments"],
                )
            )
            if not isinstance(context, BusinessContextEvidence):
                raise DocumentInterpretationSchemaError("business context")
            request = _request(ref, document, context)
            result = parse_candidate_document_interpretation(
                self.interpreter.complete_json(request)
            )
            _identity(result, self.interpreter)
            _trace(result, request["business_context"])
        except DocumentInterpretationSchemaError:
            return _blocked(ref, "DOCUMENT_INTERPRETATION.SCHEMA_INVALID")
        except Exception as error:
            code = getattr(error, "code", None)
            return _blocked(
                ref,
                code
                if type(code) is str and code in _ALLOWED_ERROR_CODES
                else _REQUEST_FAILED,
            )

        output = dict(result)
        output["parse_artifact_ref"] = ref
        output["confirmed"] = False
        if output["status"] == "success" and output["relations"]:
            output["status"] = "needs_review"
        return output


def _validate_adapter(interpreter: Any) -> None:
    identity = (
        getattr(interpreter, "name", None),
        getattr(interpreter, "model", None),
        getattr(interpreter, "schema_version", None),
        getattr(interpreter, "prompt_version", None),
        getattr(interpreter, "policy_version", None),
    )
    if any(
        type(value) is not str or not _ADAPTER_ID.fullmatch(value)
        for value in identity
    ) or identity[2:] != (SCHEMA_VERSION, PROMPT_VERSION, POLICY_VERSION):
        raise DocumentInterpretationSchemaError("adapter identity")


def _artifact(value: object) -> str:
    return value if type(value) is str and bool(_ARTIFACT.fullmatch(value)) else ""


def _document(raw: object) -> dict[str, Any]:
    _validate_json_tree(raw, inspect_sensitive=True)
    if type(raw) is not dict or set(raw) != _INPUT_KEYS:
        raise DocumentInterpretationSchemaError("input")
    if _json_bytes(raw) > _MAX_REQUEST_BYTES:
        raise DocumentInterpretationSchemaError("input size")

    ref = _artifact(raw.get("parse_artifact_ref"))
    document_type = raw.get("document_type_hint")
    fields = raw.get("candidate_fields")
    segments = raw.get("text_segments")
    if (
        not ref
        or type(document_type) is not str
        or document_type not in _DOCUMENT_TYPES
        or type(fields) is not dict
        or type(segments) is not list
        or len(segments) > _MAX_TEXT_SEGMENTS
    ):
        raise DocumentInterpretationSchemaError("input")

    llm_fields = _candidate_fields(fields)
    clean_segments = [_segment(segment) for segment in segments]
    return {
        "document_type_hint": document_type,
        "candidate_fields": fields,
        "llm_candidate_fields": llm_fields,
        "text_segments": clean_segments,
    }


def _candidate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    if set(fields) - _INPUT_FIELDS:
        raise DocumentInterpretationSchemaError("candidate field")

    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"buyer", "seller"}:
            clean.update(_party_input(key, value))
        elif key in _AMOUNT_FIELDS:
            clean[key] = _amount(value)
        elif key in _DATE_FIELDS:
            parsed = _date_text(value)
            if key == "invoice_date":
                clean[key] = parsed
        elif key in _TAX_FIELDS:
            clean[key] = _tax_id(value)
        elif key in _DIRECT_FIELDS:
            clean[key] = _safe_text(value)
        elif key == "line_items":
            if type(value) is not list:
                raise DocumentInterpretationSchemaError("line items")
        elif key in {"path", "path_hint"}:
            _safe_text(value)
        else:
            raise DocumentInterpretationSchemaError("candidate field")
    return clean


def _party_input(role: str, value: object) -> dict[str, str]:
    if type(value) is not dict or not value or set(value) - {"name", "tax_id"}:
        raise DocumentInterpretationSchemaError("party")
    result: dict[str, str] = {}
    if "name" in value:
        result[f"{role}_name"] = _safe_text(value["name"])
    if "tax_id" in value:
        result[f"{role}_tax_id"] = _tax_id(value["tax_id"])
    return result


def _segment(value: object) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"id", "text"}:
        raise DocumentInterpretationSchemaError("segment")
    identifier = value.get("id")
    if type(identifier) is not str or not _ID.fullmatch(identifier):
        raise DocumentInterpretationSchemaError("segment id")
    text = _safe_text(value.get("text"), max_bytes=_MAX_TEXT_BYTES)
    return {"id": identifier, "text": text}


def _request(
    ref: str,
    document: dict[str, Any],
    context: BusinessContextEvidence,
) -> dict[str, Any]:
    _validate_business_context_source(context)
    status = context.status
    if type(status) is not str or status not in _CONTEXT_STATUSES:
        raise DocumentInterpretationSchemaError("context status")

    candidates = _bounded_sequence(context.candidates, "candidates")
    evidence_refs = _bounded_sequence(context.evidence_refs, "evidence")
    diagnostics = _bounded_sequence(context.diagnostics, "diagnostics")
    _bounded_sequence(context.conflicts, "conflicts")

    clean_candidates = [_candidate(item) for item in candidates]
    candidate_ids = {item["id"] for item in clean_candidates}
    clean_evidence = [_evidence(item) for item in evidence_refs]
    if not clean_evidence:
        raise DocumentInterpretationSchemaError("evidence")
    if any(item["candidate_id"] not in candidate_ids for item in clean_evidence):
        raise DocumentInterpretationSchemaError("evidence candidate")

    request = {
        "schema_version": "document_interpretation_evidence_pack.v1",
        "parse_artifact_ref": ref,
        "document": {
            "document_type_hint": document["document_type_hint"],
            "candidate_fields": document["llm_candidate_fields"],
            "text_segments": document["text_segments"],
        },
        "business_context": {
            "status": status,
            "candidates": clean_candidates,
            "evidence": clean_evidence,
            "conflicts": [],
            "diagnostics": [_diagnostic(item) for item in diagnostics],
        },
    }
    _validate_json_tree(request, inspect_sensitive=True)
    if _json_bytes(request) > _MAX_REQUEST_BYTES:
        raise DocumentInterpretationSchemaError("request size")
    return request


def _validate_business_context_source(context: BusinessContextEvidence) -> None:
    candidates = _bounded_sequence(context.candidates, "candidates")
    evidence = _bounded_sequence(context.evidence_refs, "evidence")
    conflicts = _bounded_sequence(context.conflicts, "conflicts")
    diagnostics = _bounded_sequence(context.diagnostics, "diagnostics")
    source = {
        "status": context.status,
        "candidates": candidates,
        "evidence": evidence,
        "conflicts": conflicts,
        "diagnostics": diagnostics,
    }
    _validate_json_tree(source, inspect_sensitive=False)
    if _json_bytes(source) > _MAX_REQUEST_BYTES:
        raise DocumentInterpretationSchemaError("context size")
    if type(context.status) is not str or context.status not in _CONTEXT_STATUSES:
        raise DocumentInterpretationSchemaError("context status")
    for item in candidates:
        _candidate(item)
    for item in evidence:
        _evidence(item)
    for item in conflicts:
        _conflict(item)
    for item in diagnostics:
        _diagnostic(item)


def _bounded_sequence(value: object, label: str) -> list[Any]:
    if type(value) not in (list, tuple) or len(value) > _MAX_COLLECTION:
        raise DocumentInterpretationSchemaError(label)
    return list(value)


def _candidate(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) - _CANDIDATE_KEYS:
        raise DocumentInterpretationSchemaError("candidate")
    identifier = value.get("id")
    document_type = value.get("document_type")
    if (
        type(identifier) is not str
        or not _ID.fullmatch(identifier)
        or type(document_type) is not str
        or document_type not in _DOCUMENT_TYPES
    ):
        raise DocumentInterpretationSchemaError("candidate identity")

    result: dict[str, Any] = {
        "id": identifier,
        "document_type": document_type,
    }
    if "match_score" in value:
        _amount(value["match_score"])
    if "documents" in value:
        _candidate_documents(value["documents"])
    if "path_hints" in value:
        _candidate_path_hints(value["path_hints"])
    if "parties" in value:
        result["parties"] = _candidate_parties(value["parties"])
    if "facts" in value:
        result["facts"] = _candidate_facts(value["facts"])
    return result


def _candidate_parties(value: object) -> dict[str, dict[str, str]]:
    if type(value) is not dict or set(value) - {"buyer", "seller"}:
        raise DocumentInterpretationSchemaError("candidate parties")
    parties: dict[str, dict[str, str]] = {}
    for role, party in value.items():
        if type(party) is not dict or set(party) - {"name", "tax_id"}:
            raise DocumentInterpretationSchemaError("candidate party")
        clean: dict[str, str] = {}
        if "name" in party:
            clean["name"] = _safe_text(party["name"])
        if "tax_id" in party:
            clean["tax_id"] = _tax_id(party["tax_id"])
        parties[role] = clean
    return parties


def _candidate_facts(value: object) -> dict[str, Any]:
    if type(value) is not dict or set(value) - _FACTS:
        raise DocumentInterpretationSchemaError("candidate facts")
    facts: dict[str, Any] = {}
    for key, item in value.items():
        if key == "amount":
            facts[key] = _amount(item)
        elif key == "date":
            facts[key] = _date_text(item)
        else:
            facts[key] = _safe_text(item, max_bytes=128)
    return facts


def _candidate_documents(value: object) -> None:
    documents = _bounded_sequence(value, "candidate documents")
    for document in documents:
        if (
            type(document) is not dict
            or set(document) - _DOCUMENT_KEYS
            or not {
                "path", "document_version_id", "content_hash", "media_type", "page_count"
            } <= set(document)
        ):
            raise DocumentInterpretationSchemaError("candidate document")
        _source_path(document["path"])
        version = document["document_version_id"]
        digest = document["content_hash"]
        if type(version) is not str or not _ID.fullmatch(version):
            raise DocumentInterpretationSchemaError("document version")
        if type(digest) is not str or not _CONTENT_HASH.fullmatch(digest):
            raise DocumentInterpretationSchemaError("document hash")
        if document["media_type"] not in {"application/pdf", "text/markdown"}:
            raise DocumentInterpretationSchemaError("document media type")
        page_count = document["page_count"]
        if type(page_count) is not int or not 0 <= page_count <= 1_000_000:
            raise DocumentInterpretationSchemaError("document page count")
        requires = document.get("requires_structure_index", False)
        if type(requires) is not bool:
            raise DocumentInterpretationSchemaError("document index flag")


def _candidate_path_hints(value: object) -> None:
    for hint in _bounded_sequence(value, "candidate path hints"):
        _safe_text(hint, max_bytes=4_096)


def _evidence(value: object) -> dict[str, str]:
    if (
        type(value) is not dict
        or set(value) - _EVIDENCE_KEYS
        or not {"candidate_id", "field"} <= set(value)
    ):
        raise DocumentInterpretationSchemaError("evidence")
    field = value.get("field")
    if field not in _EVIDENCE:
        raise DocumentInterpretationSchemaError("evidence field")
    identifier = value.get("candidate_id")
    kind = value.get("kind", "business_context")
    if (
        type(identifier) is not str
        or not _ID.fullmatch(identifier)
        or kind != "business_context"
    ):
        raise DocumentInterpretationSchemaError("evidence")
    detail_keys = {"query_value", "candidate_value", "weight"}
    present = detail_keys & set(value)
    if present and present != detail_keys:
        raise DocumentInterpretationSchemaError("evidence detail")
    if present:
        _context_scalar(value["query_value"])
        _context_scalar(value["candidate_value"])
        weight = value["weight"]
        if type(weight) not in (int, float) or not math.isfinite(weight):
            raise DocumentInterpretationSchemaError("evidence weight")
    return {
        "kind": "business_context",
        "candidate_id": identifier,
        "field": field,
    }


def _conflict(value: object) -> None:
    required = {"code", "candidate_id", "field", "query_value", "candidate_value"}
    if type(value) is not dict or set(value) != required:
        raise DocumentInterpretationSchemaError("conflict")
    if value["code"] != "BUSINESS_CONTEXT.CONFLICT":
        raise DocumentInterpretationSchemaError("conflict code")
    identifier = value["candidate_id"]
    if type(identifier) is not str or not _ID.fullmatch(identifier):
        raise DocumentInterpretationSchemaError("conflict id")
    if value["field"] not in _EVIDENCE:
        raise DocumentInterpretationSchemaError("conflict field")
    _context_scalar(value["query_value"])
    _context_scalar(value["candidate_value"])


def _context_scalar(value: object) -> None:
    if type(value) is str:
        _safe_text(value)
    elif type(value) in (int, float):
        _amount(value)
    else:
        raise DocumentInterpretationSchemaError("context scalar")


def _source_path(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 4_096
        or _CREDENTIAL.search(unicodedata.normalize("NFKC", value).casefold())
        or re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", value) is None
    ):
        raise DocumentInterpretationSchemaError("source path")
    return value


def _diagnostic(value: object) -> dict[str, str]:
    if type(value) is not dict:
        raise DocumentInterpretationSchemaError("diagnostic")
    code = value.get("code")
    if code not in _DIAGNOSTICS or set(value) - {"code", "count"}:
        raise DocumentInterpretationSchemaError("diagnostic")
    count = value.get("count")
    if "count" in value and (
        type(count) is not int or not 0 <= count <= _MAX_COLLECTION
    ):
        raise DocumentInterpretationSchemaError("diagnostic count")
    return {"code": code}


def _identity(result: dict[str, Any], interpreter: Any) -> None:
    if (
        result["schema_version"],
        result["prompt_version"],
        result["policy_version"],
    ) != (
        interpreter.schema_version,
        interpreter.prompt_version,
        interpreter.policy_version,
    ) or result["interpreter"] != getattr(interpreter, "name", None) or result[
        "model"
    ] != getattr(interpreter, "model", None):
        raise DocumentInterpretationSchemaError("identity")


def _trace(result: dict[str, Any], context: dict[str, Any]) -> None:
    allowed = {
        (item["kind"], item["candidate_id"], item["field"])
        for item in context["evidence"]
    }
    candidate_ids = {item["id"] for item in context["candidates"]}
    used = {
        (item["kind"], item["candidate_id"], item["field"])
        for item in result["evidence"]
    }
    if not allowed or not used <= allowed:
        raise DocumentInterpretationSchemaError("evidence")
    for relation in result["relations"]:
        target = relation["target_candidate_id"]
        if target not in candidate_ids or not any(item[1] == target for item in used):
            raise DocumentInterpretationSchemaError("relation trace")


def _amount(value: object) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise DocumentInterpretationSchemaError("amount")
    return value


def _date_text(value: object) -> str:
    text = _safe_text(value, max_bytes=10)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise DocumentInterpretationSchemaError("date") from None
    if parsed.isoformat() != text:
        raise DocumentInterpretationSchemaError("date")
    return text


def _tax_id(value: object) -> str:
    text = _safe_text(value, max_bytes=32)
    if not _TAX_ID.fullmatch(text):
        raise DocumentInterpretationSchemaError("tax id")
    return text


def _safe_text(value: object, *, max_bytes: int = _MAX_FIELD_BYTES) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > max_bytes
        or _contains_sensitive(value)
    ):
        raise DocumentInterpretationSchemaError("text")
    return value


def _contains_sensitive(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return bool(_CREDENTIAL.search(normalized) or _ABSOLUTE_PATH.search(normalized))


def _validate_json_tree(root: object, *, inspect_sensitive: bool) -> None:
    stack = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise DocumentInterpretationSchemaError("json tree")
        if type(value) is str:
            if len(value.encode("utf-8")) > _MAX_FIELD_BYTES or (
                inspect_sensitive and _contains_sensitive(value)
            ):
                raise DocumentInterpretationSchemaError("text")
        elif value is None or type(value) in (int, bool):
            continue
        elif type(value) is float:
            if not math.isfinite(value):
                raise DocumentInterpretationSchemaError("number")
        elif type(value) is list:
            if len(value) > _MAX_COLLECTION:
                raise DocumentInterpretationSchemaError("array")
            stack.extend((item, depth + 1) for item in value)
        elif type(value) is dict:
            if len(value) > _MAX_COLLECTION or any(
                type(key) is not str for key in value
            ):
                raise DocumentInterpretationSchemaError("object")
            for key, item in value.items():
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
        else:
            raise DocumentInterpretationSchemaError("non-json")


def _json_bytes(value: object) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise DocumentInterpretationSchemaError("json") from None


def _blocked(ref: str, reason: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "blocked_reason": reason[:128],
        "parse_artifact_ref": ref[:512],
        "confirmed": False,
    }


__all__ = ["DocumentInterpretationService"]
