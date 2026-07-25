"""Independent, non-executable archive intent contract."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from platform_core.document_refs import (
    DocumentRefValidationError,
    validate_document_ref,
)
from platform_core.models import DocumentRef


ARCHIVE_INTENT_SCHEMA_VERSION = "archive_intent.v1"
_CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class ArchiveIntentError(ValueError):
    """The proposed intent does not satisfy the fail-closed contract."""


@dataclass(frozen=True)
class ArchiveIntent:
    source_ref: DocumentRef
    destination_status: str
    candidate_target_binding_ids: tuple[str, ...]
    project_id: str
    archive_phase: str
    content_hash: str

    def __post_init__(self) -> None:
        ref = self.source_ref
        try:
            validate_document_ref(ref, require_binding=True)
        except DocumentRefValidationError:
            raise ArchiveIntentError("source_ref requires a bound logical document reference")
        if self.destination_status not in {"resolved", "unresolved"}:
            raise ArchiveIntentError("destination_status must be resolved or unresolved")
        if not isinstance(self.candidate_target_binding_ids, tuple):
            raise ArchiveIntentError("candidate_target_binding_ids must be a tuple")
        if any(
            not isinstance(value, str)
            or not _IDENTIFIER.fullmatch(value)
            for value in self.candidate_target_binding_ids
        ) or len(set(self.candidate_target_binding_ids)) != len(self.candidate_target_binding_ids):
            raise ArchiveIntentError("candidate target binding ids must be unique identifiers")
        if self.destination_status == "resolved" and len(self.candidate_target_binding_ids) != 1:
            raise ArchiveIntentError("a resolved destination requires exactly one candidate binding")
        if not isinstance(self.project_id, str) or self.project_id != self.project_id.strip():
            raise ArchiveIntentError("project_id must be a canonical string")
        if not isinstance(self.archive_phase, str) or self.archive_phase != self.archive_phase.strip():
            raise ArchiveIntentError("archive_phase must be a canonical string")
        if not isinstance(self.content_hash, str) or not _CONTENT_HASH.fullmatch(self.content_hash):
            raise ArchiveIntentError("content_hash must be a lowercase SHA-256 digest")

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": ARCHIVE_INTENT_SCHEMA_VERSION,
            "source_ref": {
                "storage_provider": self.source_ref.storage_provider,
                "object_key": self.source_ref.object_key,
                "logical_uri": self.source_ref.logical_uri,
                "binding_id": self.source_ref.binding_id,
            },
            "destination_status": self.destination_status,
            "candidate_target_binding_ids": list(self.candidate_target_binding_ids),
            "project_id": self.project_id,
            "archive_phase": self.archive_phase,
            "content_hash": self.content_hash,
        }


__all__ = [
    "ARCHIVE_INTENT_SCHEMA_VERSION",
    "ArchiveIntent",
    "ArchiveIntentError",
]
