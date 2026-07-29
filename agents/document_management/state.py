"""Explicit states exposed by the Document Management Agent workflow."""

from __future__ import annotations

from enum import StrEnum


class WorkflowState(StrEnum):
    RECEIVED = "received"
    SCOPED = "scoped"
    PROFILED = "profiled"
    PARSED = "parsed"
    FACTS_EXTRACTED = "facts_extracted"
    ARTIFACTS_PUBLISHED = "artifacts_published"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"


__all__ = ["WorkflowState"]
