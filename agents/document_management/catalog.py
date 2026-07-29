"""Progressive Skill selection policy for the first document workflow."""

from __future__ import annotations

from typing import Any, Mapping


class SkillCatalog:
    """Select only Skills justified by the current parsed document state."""

    def for_document(self, parsed: Mapping[str, Any]) -> tuple[str, ...]:
        if parsed.get("status") != "success":
            return ("document_parse", "document_review")
        text = parsed.get("text")
        if isinstance(text, str) and "发票" in text and (
            "发票号码" in text or "发票号" in text or "价税合计" in text
        ):
            return ("document_parse", "document_facts", "document_review")
        return ("document_parse", "document_review")


__all__ = ["SkillCatalog"]
