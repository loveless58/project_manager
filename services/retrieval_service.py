"""Evidence-based, candidate-first business context retrieval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from platform_core.models import BusinessContextEvidence, BusinessContextQuery, StructureIndexRequest
from platform_core.ports import BusinessContextProvider, StructureIndex

_WEIGHTS = {
    "buyer.tax_id": 100, "seller.tax_id": 100,
    "buyer.name": 80, "seller.name": 80,
    "contract_code": 70, "project_code": 70,
    "amount": 40, "date": 30, "path_hint": 1,
}


class RetrievalService:
    """Rank evidence-backed candidates before asking PageIndex for structure."""

    def __init__(self, business_context: BusinessContextProvider, structure_index: StructureIndex | None) -> None:
        self.business_context = business_context
        self.structure_index = structure_index

    def find_business_candidates(self, query: BusinessContextQuery) -> BusinessContextEvidence:
        candidates, evidence_refs, conflicts = _rank(self.business_context.search(query), query)
        if conflicts:
            return BusinessContextEvidence(
                "needs_review", (), (), tuple(conflicts),
                ({"code": "BUSINESS_CONTEXT.CONFLICTS_FOUND"},),
            )
        if not candidates:
            return BusinessContextEvidence(
                "needs_review", (), (), (),
                ({"code": "BUSINESS_CONTEXT.NO_CANDIDATES"},),
            )
        diagnostics: list[Mapping[str, Any]] = [
            {"code": "BUSINESS_CONTEXT.CANDIDATES_FOUND", "count": len(candidates)}
        ]
        if self.structure_index is not None:
            evidence_refs.extend(self._index_declared_documents(candidates, diagnostics))
        return BusinessContextEvidence(
            "matched", tuple(candidates), tuple(evidence_refs), (), tuple(diagnostics)
        )

    def _index_declared_documents(self, candidates: Sequence[Mapping[str, Any]], diagnostics: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        groups: dict[str, list[tuple[str, StructureIndexRequest, tuple[object, ...]]]] = {}
        for candidate in candidates:
            documents = candidate.get("documents", ())
            if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
                continue
            candidate_id = str(candidate.get("id", ""))
            for document in documents:
                if not isinstance(document, Mapping):
                    diagnostics.append(_document_diagnostic("DOCUMENT_INVALID", candidate_id))
                    continue
                validated = _validated_document_request(document)
                if validated is None:
                    diagnostics.append(_document_diagnostic("DOCUMENT_INVALID", candidate_id))
                    continue
                request, identity, eligible = validated
                groups.setdefault(request.document_version_id, []).append(
                    (candidate_id, request, identity)
                )
        declared: list[tuple[str, StructureIndexRequest]] = []
        for version, variants in groups.items():
            identities = {variant[2] for variant in variants}
            if len(identities) != 1:
                diagnostics.append(_document_diagnostic("DOCUMENT_IDENTITY_CONFLICT", ""))
                continue
            candidate_id, request, _ = variants[0]
            if _request_is_eligible(request, variants[0][2]):
                declared.append((candidate_id, request))
        refs: list[Mapping[str, Any]] = []
        for candidate_id, request in declared[:3]:
            result = self.structure_index.index(request)
            if result.status == "success":
                refs.append({
                    "kind": "structure_index",
                    "candidate_id": candidate_id,
                    "document_version_id": request.document_version_id,
                    "external_ref": result.external_ref,
                    "status": result.status,
                })
                continue
            diagnostics.append({
                "code": "BUSINESS_CONTEXT.STRUCTURE_INDEX_FAILED",
                "candidate_id": candidate_id,
                "provider": _safe_symbol(result.provider, "unknown"),
                "status": _safe_status(result.status),
                "error_code": _safe_symbol(result.error_code, "INDEX.PROVIDER_FAILED"),
            })
        return refs


def _rank(raw: Sequence[Mapping[str, Any]], query: BusinessContextQuery) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(query, BusinessContextQuery) or not isinstance(query.candidate_fields, Mapping):
        return [], [], []
    scored: list[tuple[int, str, dict[str, Any], list[dict[str, Any]]]] = []
    conflicts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        candidate = deepcopy(dict(item))
        score, refs, candidate_conflicts, has_business_evidence = _score(candidate, query.candidate_fields)
        conflicts.extend(candidate_conflicts)
        if candidate_conflicts or not has_business_evidence:
            continue
        candidate["match_score"] = score
        scored.append((score, str(candidate.get("id", "")), candidate, refs))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return [value[2] for value in scored], [ref for _, _, _, refs in scored for ref in refs], conflicts


def _score(candidate: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]], bool]:
    parties, facts = candidate.get("parties", {}), candidate.get("facts", {})
    if not isinstance(parties, Mapping):
        parties = {}
    if not isinstance(facts, Mapping):
        facts = {}
    score, has_business_evidence = 0, False
    refs: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for role in ("buyer", "seller"):
        queried, stored = fields.get(role), parties.get(role)
        if not isinstance(queried, Mapping) or not isinstance(stored, Mapping):
            continue
        for field in ("tax_id", "name"):
            key, query_value, stored_value = f"{role}.{field}", queried.get(field), stored.get(field)
            if field == "tax_id" and _different(field, query_value, stored_value):
                conflicts.append(_conflict(candidate, key, query_value, stored_value))
            elif _same(field, query_value, stored_value):
                score += _WEIGHTS[key]
                has_business_evidence = True
                refs.append(_ref(candidate, key, query_value, stored_value))
    for field in ("contract_code", "project_code", "amount", "date"):
        query_value, stored_value = fields.get(field), facts.get(field)
        if field in {"contract_code", "project_code"} and _different(field, query_value, stored_value):
            conflicts.append(_conflict(candidate, field, query_value, stored_value))
        elif _same(field, query_value, stored_value):
            score += _WEIGHTS[field]
            has_business_evidence = True
            refs.append(_ref(candidate, field, query_value, stored_value))
    hint = fields.get("path_hint", fields.get("path"))
    if has_business_evidence and isinstance(hint, str) and _path_matches(hint, candidate):
        score += _WEIGHTS["path_hint"]
        refs.append(_ref(candidate, "path_hint", hint, "declared path hint"))
    if not has_business_evidence:
        conflicts = []
    return score, refs, conflicts, has_business_evidence


def _ref(candidate: Mapping[str, Any], field: str, query_value: Any, candidate_value: Any) -> dict[str, Any]:
    return {"candidate_id": str(candidate.get("id", "")), "field": field, "query_value": query_value, "candidate_value": candidate_value, "weight": _WEIGHTS[field]}


def _conflict(candidate: Mapping[str, Any], field: str, query_value: object, candidate_value: object) -> dict[str, Any]:
    return {"code": "BUSINESS_CONTEXT.CONFLICT", "candidate_id": str(candidate.get("id", "")), "field": field, "query_value": _redact(query_value), "candidate_value": _redact(candidate_value)}


def _redact(value: object) -> str:
    text = str(value)
    return "***" if len(text) <= 4 else f"***{text[-4:]}"


def _validated_document_request(document: Mapping[str, Any]) -> tuple[StructureIndexRequest, tuple[object, ...], bool] | None:
    version, digest, path, media_type = (
        document.get("document_version_id"),
        document.get("content_hash"),
        document.get("path"),
        document.get("media_type"),
    )
    page_count, requires = document.get("page_count", 0), document.get("requires_structure_index", False)
    if (
        not _canonical_string(version)
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
        or not _canonical_path(path)
        or media_type not in {"application/pdf", "text/markdown"}
        or isinstance(page_count, bool)
        or not isinstance(page_count, int)
        or page_count < 0
        or not isinstance(requires, bool)
    ):
        return None
    request = StructureIndexRequest(version, digest.lower(), str(Path(path).expanduser().resolve()), media_type)
    identity = (request.content_hash, request.source_path, request.media_type, page_count, requires)
    return request, identity, requires or page_count >= 10


def _canonical_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _canonical_path(value: object) -> bool:
    return _canonical_string(value) and Path(value).expanduser().is_absolute()


def _request_is_eligible(request: StructureIndexRequest, identity: tuple[object, ...]) -> bool:
    return bool(identity[-1]) or int(identity[-2]) >= 10


def _document_diagnostic(reason: str, candidate_id: str) -> dict[str, str]:
    result = {"code": f"BUSINESS_CONTEXT.{reason}"}
    if candidate_id:
        result["candidate_id"] = candidate_id
    return result


def _same(field: str, left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if field == "name":
        return _name(left) == _name(right)
    if field in {"tax_id", "contract_code", "project_code"}:
        return _identifier(left) == _identifier(right)
    return left.strip() == right.strip()


def _different(field: str, left: object, right: object) -> bool:
    return isinstance(left, str) and bool(left.strip()) and isinstance(right, str) and bool(right.strip()) and not _same(field, left, right)


def _identifier(value: str) -> str:
    return re.sub(r"[\s-]+", "", value).upper()


def _name(value: str) -> str:
    return re.sub(r"[^\w]+", "", value).upper()


def _path_matches(query_path: str, candidate: Mapping[str, Any]) -> bool:
    query_path = query_path.replace("\\", "/").casefold().strip("/")
    hints = candidate.get("path_hints", ())
    if not query_path or not isinstance(hints, Sequence) or isinstance(hints, (str, bytes)):
        return False
    for hint in hints:
        if isinstance(hint, str):
            normalized = hint.replace("\\", "/").casefold().strip("/")
            if normalized and (query_path in normalized or normalized in query_path):
                return True
    return False


def _safe_symbol(value: object, fallback: str) -> str:
    value = str(value)
    return value if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) else fallback


def _safe_status(value: object) -> str:
    return str(value) if value in {"blocked", "failed"} else "failed"


__all__ = ["RetrievalService"]
