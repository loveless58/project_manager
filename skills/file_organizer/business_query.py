"""SQLite-first project matching with an optional PageIndex evidence hook."""

from __future__ import annotations

from typing import Any, Mapping

from platform_core.models import StructureIndexRequest


class BusinessQuerySkill:
    def __init__(self, repository: Any, structure_index: Any = None, *, pageindex_min_text_length: int = 16_000) -> None:
        self._repository = repository
        self._structure_index = structure_index
        self._pageindex_min_text_length = pageindex_min_text_length

    def propose(self, structured_document: Mapping[str, Any], *, goal: str, source_path: str | None = None) -> dict[str, Any]:
        fields = structured_document.get("fields") if isinstance(structured_document.get("fields"), Mapping) else {}
        candidates = self._repository.find_project_candidates(fields)
        reasons: list[str] = []
        candidate_project = candidates[0] if len(candidates) == 1 else None
        if candidate_project is not None:
            reasons.append("PROJECT.MATCHED")
            status = "needs_confirmation"
        elif len(candidates) > 1:
            reasons.append("PROJECT.AMBIGUOUS")
            status = "needs_review"
        else:
            reasons.append("PROJECT.UNMATCHED")
            status = "needs_review"

        evidence, index_reason = self._pageindex_evidence(structured_document, source_path)
        if index_reason:
            reasons.append(index_reason)
        return {
            "schema_version": "organization_proposal.v1",
            "status": status,
            "goal": goal,
            "candidate_project": candidate_project,
            "candidate_count": len(candidates),
            "pageindex_evidence": evidence,
            "reasons": reasons,
        }

    def _pageindex_evidence(self, document: Mapping[str, Any], source_path: str | None) -> tuple[list[dict[str, Any]], str]:
        text = document.get("text") if isinstance(document.get("text"), str) else ""
        if len(text.encode("utf-8")) < self._pageindex_min_text_length:
            return [], ""
        if document.get("media_type") not in {"application/pdf", "text/markdown"}:
            return [], ""
        if self._structure_index is None:
            return [], "PAGEINDEX.UNAVAILABLE"
        try:
            probe = self._structure_index.probe()
            if getattr(probe, "status", None) != "ready":
                return [], "PAGEINDEX.UNAVAILABLE"
            result = self._structure_index.index(
                StructureIndexRequest(
                    document_version_id=str(document.get("content_hash", "")),
                    content_hash=str(document.get("content_hash", "")),
                    source_path=source_path or str(document.get("source_ref", {}).get("object_key", "")),
                    media_type=str(document.get("media_type", "")),
                )
            )
        except Exception:
            return [], "PAGEINDEX.UNAVAILABLE"
        if getattr(result, "status", None) != "success":
            return [], "PAGEINDEX.UNAVAILABLE"
        evidence = []
        for node in getattr(result, "structure", ()):
            if not isinstance(node, Mapping):
                continue
            page = node.get("page") or node.get("page_start") or node.get("start_page")
            evidence.append({"title": str(node.get("title", "")), "page": page, "page_end": node.get("page_end"), "index_ref": getattr(result, "external_ref", "")})
        return evidence, ""
