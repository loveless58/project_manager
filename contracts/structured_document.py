"""Stable, bounded document output for the file-organizer skills."""

from __future__ import annotations

import re
from typing import Any, Mapping


SCHEMA_VERSION = "structured_document.v1"
MAX_TEXT_BYTES = 4_096
_HASH = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REF_KEYS = {
    "binding_id",
    "logical_uri",
    "storage_provider",
    "object_key",
}
_REQUIRED_KEYS = {
    "schema_version",
    "source_ref",
    "content_hash",
    "media_type",
    "status",
    "parser",
    "text",
    "pages",
    "tables",
    "fields",
}


class StructuredDocumentError(ValueError):
    """A document result does not meet the cross-skill contract."""


def truncate_utf8(value: str, *, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Truncate a text value without exceeding a UTF-8 byte boundary."""
    if not isinstance(value, str) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise StructuredDocumentError("structured document text is invalid")
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def build_structured_document(
    *,
    source_ref: Mapping[str, Any],
    content_hash: str,
    media_type: str,
    parser: str,
    text: str,
    pages: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate one successful normalized document result."""
    return validate_structured_document(
        {
            "schema_version": SCHEMA_VERSION,
            "source_ref": dict(source_ref),
            "content_hash": content_hash,
            "media_type": media_type,
            "status": "success",
            "parser": parser,
            "text": truncate_utf8(text),
            "pages": list(pages),
            "tables": list(tables),
            "fields": dict(fields),
        }
    )


def validate_structured_document(payload: object) -> dict[str, Any]:
    """Validate and return a detached ``structured_document.v1`` payload."""
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_KEYS:
        raise StructuredDocumentError("structured document fields are invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise StructuredDocumentError("structured document version is invalid")
    if payload.get("status") != "success":
        raise StructuredDocumentError("structured document status is invalid")
    source_ref = payload.get("source_ref")
    if (
        not isinstance(source_ref, dict)
        or set(source_ref) != _SOURCE_REF_KEYS
        or not all(isinstance(value, str) and value.strip() for value in source_ref.values())
    ):
        raise StructuredDocumentError("structured document source reference is invalid")
    content_hash = payload.get("content_hash")
    if not isinstance(content_hash, str) or _HASH.fullmatch(content_hash) is None:
        raise StructuredDocumentError("structured document content hash is invalid")
    for field in ("media_type", "parser"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise StructuredDocumentError(f"structured document {field} is invalid")
    text = payload.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise StructuredDocumentError("structured document text is invalid")
    if not isinstance(payload.get("pages"), list) or not isinstance(payload.get("tables"), list):
        raise StructuredDocumentError("structured document page data is invalid")
    if not isinstance(payload.get("fields"), dict):
        raise StructuredDocumentError("structured document fields are invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_ref": dict(source_ref),
        "content_hash": content_hash,
        "media_type": payload["media_type"],
        "status": "success",
        "parser": payload["parser"],
        "text": text,
        "pages": list(payload["pages"]),
        "tables": list(payload["tables"]),
        "fields": dict(payload["fields"]),
    }


__all__ = [
    "MAX_TEXT_BYTES",
    "SCHEMA_VERSION",
    "StructuredDocumentError",
    "build_structured_document",
    "truncate_utf8",
    "validate_structured_document",
]
