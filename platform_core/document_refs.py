"""Strict validation for bound and adapter-level document references."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import urlsplit

from platform_core.logical_paths import LogicalPathError, parse_logical_path
from platform_core.models import DocumentRef
from platform_core.sensitive_text import contains_sensitive_text


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_REF_KEYS = frozenset({
    "storage_provider", "object_key", "logical_uri", "binding_id",
})


class DocumentRefValidationError(ValueError):
    """A document reference is not canonical or internally consistent."""


def validate_document_ref(
    value: DocumentRef | Mapping[str, Any],
    *,
    require_binding: bool = True,
) -> DocumentRef:
    """Validate a canonical logical reference without touching physical storage."""

    ref = _coerce_document_ref(value)
    if not _IDENTIFIER.fullmatch(ref.storage_provider):
        raise DocumentRefValidationError("storage_provider must be a safe identifier")
    if require_binding:
        if not _IDENTIFIER.fullmatch(ref.binding_id):
            raise DocumentRefValidationError("binding_id must be a safe identifier")
    elif ref.binding_id and not _IDENTIFIER.fullmatch(ref.binding_id):
        raise DocumentRefValidationError("binding_id must be a safe identifier")

    try:
        canonical_key = parse_logical_path(ref.object_key).value
    except LogicalPathError as exc:
        raise DocumentRefValidationError("object_key must be canonical") from exc
    if canonical_key != ref.object_key or contains_sensitive_text(ref.object_key):
        raise DocumentRefValidationError("object_key must be canonical")

    uri = ref.logical_uri
    if (
        type(uri) is not str
        or not uri
        or uri != uri.strip()
        or len(uri.encode("utf-8")) > 2048
        or contains_sensitive_text(uri)
    ):
        raise DocumentRefValidationError("logical_uri must be safe and canonical")
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
    except ValueError as exc:
        raise DocumentRefValidationError("logical_uri must be a valid URI") from exc
    if (
        not parsed.scheme
        or parsed.scheme != parsed.scheme.lower()
        or parsed.scheme not in {"business", ref.storage_provider}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        raise DocumentRefValidationError("logical_uri must be a controlled URI")

    if not ref.binding_id:
        expected = f"{ref.storage_provider}://{ref.object_key}"
        if parsed.scheme == "business" or uri != expected:
            raise DocumentRefValidationError(
                "logical_uri must match provider and object_key",
            )
        return ref

    suffix = f"/{ref.object_key}"
    if parsed.netloc != ref.binding_id or not parsed.path.endswith(suffix):
        raise DocumentRefValidationError(
            "logical_uri must match provider, binding, and object_key",
        )
    prefix_path = parsed.path[:-len(suffix)]
    if prefix_path:
        if prefix_path == "/" or not prefix_path.startswith("/"):
            raise DocumentRefValidationError(
                "logical_uri prefix must be canonical",
            )
        logical_prefix = prefix_path[1:]
        try:
            canonical_prefix = parse_logical_path(logical_prefix).value
        except LogicalPathError as exc:
            raise DocumentRefValidationError(
                "logical_uri prefix must be canonical",
            ) from exc
        if canonical_prefix != logical_prefix:
            raise DocumentRefValidationError(
                "logical_uri prefix must be canonical",
            )
    return ref


def _coerce_document_ref(value: DocumentRef | Mapping[str, Any]) -> DocumentRef:
    if isinstance(value, DocumentRef):
        return value
    if not isinstance(value, Mapping) or set(value) != _REF_KEYS:
        raise DocumentRefValidationError("document reference fields are invalid")
    if any(type(value[key]) is not str for key in _REF_KEYS):
        raise DocumentRefValidationError("document reference values must be strings")
    return DocumentRef(
        storage_provider=value["storage_provider"],
        object_key=value["object_key"],
        logical_uri=value["logical_uri"],
        binding_id=value["binding_id"],
    )


__all__ = [
    "DocumentRefValidationError",
    "validate_document_ref",
]
