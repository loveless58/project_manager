"""Construct a no-archive review result from parsed document facts."""

from __future__ import annotations

from typing import Any, Mapping

from contracts.document_result import build_document_result


class DocumentReviewSkill:
    """Produce a reviewable document result without querying business context."""

    def build(
        self,
        run: Mapping[str, Any],
        document_id: str,
        structured_document: Mapping[str, Any],
        facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        reason = structured_document.get("reason")
        reasons = [reason] if isinstance(reason, str) and reason else []
        return build_document_result(
            run=run,
            document_id=document_id,
            structured_document=structured_document,
            facts=facts,
            decision={
                "status": "needs_review",
                "suggested_action": "no_archive",
                "reasons": reasons,
            },
            artifacts=[],
        )


__all__ = ["DocumentReviewSkill"]
