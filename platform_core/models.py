from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DocumentRef:
    storage_provider: str
    object_key: str
    logical_uri: str
    binding_id: str = ""


@dataclass(frozen=True)
class ObjectStat:
    ref: DocumentRef
    size_bytes: int
    modified_at: float
    etag: str
    available: bool


@dataclass(frozen=True)
class CapabilityReport:
    status: str
    provider: str
    provider_version: str
    reason: str


@dataclass(frozen=True)
class StructureIndexRequest:
    document_version_id: str
    content_hash: str
    source_path: str
    media_type: str


@dataclass(frozen=True)
class StructureIndexResult:
    status: str
    provider: str
    external_ref: str
    structure: Sequence[Mapping[str, Any]]
    error_code: str
    error: str


@dataclass(frozen=True)
class BusinessContextQuery:
    """Evidence supplied by a caller for controlled business-context lookup."""

    document_type: str
    candidate_fields: Mapping[str, Any]
    text_segments: Sequence[Mapping[str, str]]


@dataclass(frozen=True)
class BusinessContextEvidence:
    """Candidate business context and the evidence used to rank it."""

    status: str
    candidates: Sequence[Mapping[str, Any]]
    evidence_refs: Sequence[Mapping[str, Any]]
    conflicts: Sequence[Mapping[str, Any]]
    diagnostics: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class ProjectionRequest:
    projection_type: str
    relative_path: str
    content: str
    media_type: str


@dataclass(frozen=True)
class ProjectionRef:
    provider: str
    logical_uri: str
    sha256: str
    size_bytes: int
