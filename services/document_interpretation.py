"""Controlled interpretation boundary between parsed documents and LLM adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contracts.document_interpretation import DocumentInterpretationSchemaError, parse_candidate_document_interpretation
from platform_core.models import BusinessContextEvidence, BusinessContextQuery
from platform_core.ports.document_interpreter import DocumentInterpreter


_EVIDENCE_PACK_VERSION = "document_interpretation_evidence_pack.v1"
_SAFE_PARTY_VALUE_FIELDS = {"name", "tax_id"}
_SAFE_FACT_FIELDS = {"contract_code", "project_code", "amount", "date"}
_SAFE_EVIDENCE_FIELDS = {"kind", "candidate_id", "field"}


class DocumentInterpretationService:
    """Interpret a narrow evidence pack; this service never confirms relationships."""

    def __init__(self, retrieval_service: Any, interpreter: DocumentInterpreter) -> None:
        self.retrieval_service = retrieval_service
        self.interpreter = interpreter

    def interpret(self, evidence_pack: Mapping[str, Any]) -> dict[str, Any]:
        parse_artifact_ref = _artifact_ref(evidence_pack)
        try:
            document = _document_evidence(evidence_pack)
            context = self.retrieval_service.find_business_candidates(BusinessContextQuery(
                document_type=document["document_type_hint"], candidate_fields=document["candidate_fields"], text_segments=document["text_segments"],
            ))
            if not isinstance(context, BusinessContextEvidence):
                raise ValueError("invalid retrieval result")
            request = _controlled_request(parse_artifact_ref, document, context)
            interpretation = parse_candidate_document_interpretation(self.interpreter.complete_json(request))
            _validate_identity(interpretation, self.interpreter)
            _validate_evidence(interpretation, request["business_context"]["evidence"])
        except DocumentInterpretationSchemaError:
            return _blocked(parse_artifact_ref, "DOCUMENT_INTERPRETATION.SCHEMA_INVALID")
        except Exception as error:
            code = getattr(error, "code", "DOCUMENT_INTERPRETATION.LLM.REQUEST_FAILED")
            return _blocked(parse_artifact_ref, code if isinstance(code, str) and code.startswith("DOCUMENT_INTERPRETATION.") else "DOCUMENT_INTERPRETATION.LLM.REQUEST_FAILED")
        result = dict(interpretation)
        result["parse_artifact_ref"] = parse_artifact_ref
        result["confirmed"] = False
        if result["status"] == "success" and result["relations"]:
            result["status"] = "needs_review"
        return result


def _artifact_ref(evidence_pack: object) -> str:
    value = evidence_pack.get("parse_artifact_ref") if isinstance(evidence_pack, Mapping) else None
    return value if _string(value) else ""


def _document_evidence(evidence_pack: Mapping[str, Any]) -> dict[str, Any]:
    hint, fields, segments = evidence_pack.get("document_type_hint"), evidence_pack.get("candidate_fields"), evidence_pack.get("text_segments")
    if not _string(evidence_pack.get("parse_artifact_ref")) or not _string(hint) or not isinstance(fields, Mapping) or not isinstance(segments, list):
        raise DocumentInterpretationSchemaError("invalid evidence pack")
    normalized = []
    for segment in segments:
        if not isinstance(segment, Mapping) or set(segment).difference({"id", "text"}) or not _string(segment.get("id")) or not isinstance(segment.get("text"), str):
            raise DocumentInterpretationSchemaError("invalid text segment")
        normalized.append({"id": segment["id"], "text": segment["text"]})
    return {"document_type_hint": hint, "candidate_fields": dict(fields), "text_segments": normalized}


def _controlled_request(parse_artifact_ref: str, document: Mapping[str, Any], context: BusinessContextEvidence) -> dict[str, Any]:
    return {
        "schema_version": _EVIDENCE_PACK_VERSION, "parse_artifact_ref": parse_artifact_ref, "document": dict(document),
        "business_context": {
            "status": context.status if _string(context.status) else "unknown",
            "candidates": [_candidate(item) for item in context.candidates if isinstance(item, Mapping)],
            "evidence": [_evidence(item) for item in context.evidence_refs if isinstance(item, Mapping)],
            "conflicts": [_diagnostic(item) for item in context.conflicts if isinstance(item, Mapping)],
            "diagnostics": [_diagnostic(item) for item in context.diagnostics if isinstance(item, Mapping)],
        },
    }


def _candidate(item: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("id", "document_type"):
        if _string(item.get(key)):
            result[key] = item[key]
    if isinstance(item.get("parties"), Mapping):
        parties = {}
        for role in ("buyer", "seller"):
            party = item["parties"].get(role)
            if isinstance(party, Mapping):
                values = {key: party[key] for key in _SAFE_PARTY_VALUE_FIELDS if _string(party.get(key))}
                if values:
                    parties[role] = values
        if parties:
            result["parties"] = parties
    if isinstance(item.get("facts"), Mapping):
        facts = {key: item["facts"][key] for key in _SAFE_FACT_FIELDS if _string(item["facts"].get(key))}
        if facts:
            result["facts"] = facts
    return result


def _evidence(item: Mapping[str, Any]) -> dict[str, str]:
    return {key: item[key] for key in _SAFE_EVIDENCE_FIELDS if _string(item.get(key))}


def _diagnostic(item: Mapping[str, Any]) -> dict[str, str]:
    return {"code": item["code"]} if _string(item.get("code")) else {"code": "UNKNOWN"}


def _validate_identity(result: Mapping[str, Any], interpreter: DocumentInterpreter) -> None:
    if result["interpreter"] != getattr(interpreter, "name", None) or result["model"] != getattr(interpreter, "model", None):
        raise DocumentInterpretationSchemaError("untrusted interpreter identity")


def _validate_evidence(result: Mapping[str, Any], supplied: object) -> None:
    if not isinstance(supplied, list):
        raise DocumentInterpretationSchemaError("missing supplied evidence")
    allowed = {(item.get("kind"), item.get("candidate_id"), item.get("field")) for item in supplied if isinstance(item, Mapping)}
    used = {(item.get("kind"), item.get("candidate_id"), item.get("field")) for item in result["evidence"]}
    if not allowed or not used.issubset(allowed):
        raise DocumentInterpretationSchemaError("untraceable evidence")


def _blocked(parse_artifact_ref: str, reason: str) -> dict[str, Any]:
    return {"status": "blocked", "blocked_reason": reason, "parse_artifact_ref": parse_artifact_ref, "confirmed": False}


def _string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


__all__ = ["DocumentInterpretationService"]
