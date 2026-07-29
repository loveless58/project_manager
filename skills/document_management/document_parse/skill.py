"""Workflow-facing adapter for the existing native-first parsing Skill."""

from __future__ import annotations

from typing import Any, Mapping

from skills.file_organizer.document_parse import DocumentParseSkill


class DocumentParseWorkflowSkill:
    """Expose parsing as one named Skill without duplicating parser behavior."""

    def __init__(self, parser: DocumentParseSkill | None = None) -> None:
        self._parser = parser or DocumentParseSkill()

    def parse(
        self, source_path: str, *, source_ref: Mapping[str, str]
    ) -> dict[str, Any]:
        return self._parser.parse(source_path, source_ref=source_ref)


__all__ = ["DocumentParseWorkflowSkill"]
