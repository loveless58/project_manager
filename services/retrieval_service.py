"""Evidence-based, candidate-first business context retrieval."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
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
        candidates, evidence_refs = _rank(self.business_context.search(query), query)
        if not candidates:
            return BusinessContextEvidence(
                "needs_review", (), (), (), ({"code": "BUSINESS_CONTEXT.NO_CANDIDATES"},)
            )
        diagnostics: list[Mapping[str, Any]] = [
            {"code": "BUSINESS_CONTEXT.CANDIDATES_FOUND", "count": len(candidates)}
        ]
        if self.structure_index is not None:
            evidence_refs.extend(self._index_declared_documents(candidates, diagnostics))
        return BusinessContextEvidence("matched", tuple(candidates), tuple(evidence_refs), (), tuple(diagnostics))

    def _index_declared_documents(
        self, candidates: Sequence[Mapping[str, Any]], diagnostics: list[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        declared: list[tuple[str, Mapping[str, Any]]] = []
        for candidate in candidates:
            documents = candidate.get("documents", ())
            if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)):
                continue
            for document in documents:
                if isinstance(document, Mapping) and _needs_index(document):
                    declared.append((str(candidate.get("id", "")), document))
        refs: list[Mapping[str, Any]] = []
        for candidate_id, document in declared[:3]:
            request = _request(document)
            if request is None:
                diagnostics.append({"code": "BUSINESS_CONTEXT.STRUCTURE_INDEX_SKIPPED", "candidate_id": candidate_id})
                continue
            result = self.structure_index.index(request)
            refs.append({
                "kind": "structure_index", "candidate_id": candidate_id,
                "document_version_id": request.document_version_id,
                "external_ref": result.external_ref, "status": result.status,
            })
        return refs


def _rank(raw: Sequence[Mapping[str, Any]], query: BusinessContextQuery) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(query, BusinessContextQuery) or not isinstance(query.candidate_fields, Mapping):
        return [], []
    scored: list[tuple[int, str, dict[str, Any], list[dict[str, Any]]]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        candidate = deepcopy(dict(item))
        score, refs = _score(candidate, query.candidate_fields)
        if score:
            candidate["match_score"] = score
            scored.append((score, str(candidate.get("id", "")), candidate, refs))
    scored.sort(key=lambda value: (-value[0], value[1]))
    return [value[2] for value in scored], [ref for _, _, _, refs in scored for ref in refs]


def _score(candidate: Mapping[str, Any], fields: Mapping[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    parties, facts = candidate.get("parties", {}), candidate.get("facts", {})
    if not isinstance(parties, Mapping): parties = {}
    if not isinstance(facts, Mapping): facts = {}
    score, refs = 0, []
    for role in ("buyer", "seller"):
        queried, stored = fields.get(role), parties.get(role)
        if not isinstance(queried, Mapping) or not isinstance(stored, Mapping): continue
        for field in ("tax_id", "name"):
            key = f"{role}.{field}"
            if _same(field, queried.get(field), stored.get(field)):
                score += _WEIGHTS[key]
                refs.append(_ref(candidate, key, queried[field], stored[field]))
    for field in ("contract_code", "project_code", "amount", "date"):
        if _same(field, fields.get(field), facts.get(field)):
            score += _WEIGHTS[field]
            refs.append(_ref(candidate, field, fields[field], facts[field]))
    hint = fields.get("path_hint", fields.get("path"))
    if isinstance(hint, str) and _path_matches(hint, candidate):
        score += _WEIGHTS["path_hint"]
        refs.append(_ref(candidate, "path_hint", hint, "declared path hint"))
    return score, refs


def _ref(candidate: Mapping[str, Any], field: str, query_value: Any, candidate_value: Any) -> dict[str, Any]:
    return {"candidate_id": str(candidate.get("id", "")), "field": field, "query_value": query_value, "candidate_value": candidate_value, "weight": _WEIGHTS[field]}


def _needs_index(document: Mapping[str, Any]) -> bool:
    pages = document.get("page_count", 0)
    return document.get("requires_structure_index") is True or (isinstance(pages, int) and not isinstance(pages, bool) and pages >= 10)


def _request(document: Mapping[str, Any]) -> StructureIndexRequest | None:
    fields = ("document_version_id", "content_hash", "path", "media_type")
    if not all(isinstance(document.get(field), str) and document[field] for field in fields): return None
    return StructureIndexRequest(document["document_version_id"], document["content_hash"], document["path"], document["media_type"])


def _same(field: str, left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str): return False
    if field == "name": return _name(left) == _name(right)
    if field in {"tax_id", "contract_code", "project_code"}:
        return re.sub(r"[\s-]+", "", left).upper() == re.sub(r"[\s-]+", "", right).upper()
    return left.strip() == right.strip()


def _name(value: str) -> str:
    return re.sub(r"[\s()（）,，.。]+", "", value).upper()


def _path_matches(query_path: str, candidate: Mapping[str, Any]) -> bool:
    query_path = query_path.replace("\\", "/").casefold().strip("/")
    hints = candidate.get("path_hints", ())
    return bool(query_path and isinstance(hints, Sequence) and not isinstance(hints, (str, bytes)) and any(isinstance(hint, str) and (query_path in hint.replace("\\", "/").casefold().strip("/") or hint.replace("\\", "/").casefold().strip("/") in query_path) for hint in hints))


__all__ = ["RetrievalService"]
