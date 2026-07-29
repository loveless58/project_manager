"""Pathless references to review artifacts published by a connector."""

from __future__ import annotations

import re
from typing import Any, Mapping


SCHEMA_VERSION = "artifact_reference.v1"
_KINDS = {"document_json", "review_markdown", "run_json", "run_summary"}
_HASH = re.compile(r"^[0-9a-f]{64}$")


def build_artifact_reference(
    *, kind: str, logical_uri: str, sha256: str, size_bytes: int
) -> dict[str, Any]:
    """Build one physical-path-free projection reference."""
    if kind not in _KINDS:
        raise ValueError("unsupported artifact kind")
    if not isinstance(logical_uri, str) or not logical_uri.startswith("projection://"):
        raise ValueError("artifact logical URI must be projection scoped")
    if not isinstance(sha256, str) or _HASH.fullmatch(sha256) is None:
        raise ValueError("artifact hash is invalid")
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
        raise ValueError("artifact size is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "logical_uri": logical_uri,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def validate_artifact_reference(payload: object) -> dict[str, Any]:
    """Validate a detached artifact payload and return its canonical shape."""
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version", "kind", "logical_uri", "sha256", "size_bytes"
    }:
        raise ValueError("artifact reference fields are invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("artifact reference version is invalid")
    return build_artifact_reference(
        kind=payload["kind"],
        logical_uri=payload["logical_uri"],
        sha256=payload["sha256"],
        size_bytes=payload["size_bytes"],
    )


__all__ = ["SCHEMA_VERSION", "build_artifact_reference", "validate_artifact_reference"]
