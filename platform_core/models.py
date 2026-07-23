from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DocumentRef:
    storage_provider: str
    object_key: str
    logical_uri: str


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
