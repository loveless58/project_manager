"""Canonical recursive detection for sensitive paths and credential material."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit


_DRIVE_PATH = re.compile(r"[a-z]:[\\/]", re.IGNORECASE)
_UNC_PATH = re.compile(r"(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+")  # repo-hygiene: allow=synthetic-path
_FILE_URI = re.compile(r"file\s*:\s*/{2,}", re.IGNORECASE)
_POSIX_PATH = re.compile(r"(?<![\w.~%:/-])/(?!/)[^\s\]\[{}]+", re.IGNORECASE)
_SENSITIVE_FIELD_PATTERN = (
    r"private[_-]?key|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|id[_-]?token|csrf[_-]?token|client[_-]?secret|"
    r"db[_-]?password|secret[_-]?access[_-]?key|aws[_-]?session[_-]?token|"
    r"proxy[_-]?authorization|session[_-]?id|cookie[_-]?value"
)
_CREDENTIAL_KEY = re.compile(
    rf"(?<![a-z0-9])(?:authorization|bearer|token|key|password|cookie|session|"
    rf"credential|signature|{_SENSITIVE_FIELD_PATTERN})\s*[\"']?\s*[:=]",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?<![a-z0-9])bearer\s+\S", re.IGNORECASE)
_X_AMZ_KEY = re.compile(r"(?<![a-z0-9])x-amz-[a-z0-9-]+\s*[\"']?\s*[:=]", re.IGNORECASE)
_LOGICAL_URI = re.compile(
    r"\b[a-z][a-z0-9+.-]*://[^\s\]\[{}<>\"']+",
    re.IGNORECASE,
)
_PATH_ESCAPE = re.compile(
    r"(?:^|[\\/\s])(?:~|\.\.)(?=$|[\\/])|\$(?:home|\{home\})(?=$|[\\/])",
    re.IGNORECASE,
)
_GENERIC_SENSITIVE_FIELDS = frozenset({
    "authorization", "bearer", "token", "key", "password", "cookie",
    "session", "credential", "signature",
})
_SENSITIVE_FIELD_SUFFIXES = (
    "private_key", "api_key", "access_token", "refresh_token", "auth_token",
    "id_token", "csrf_token", "client_secret", "db_password",
    "secret_access_key", "aws_session_token", "proxy_authorization",
    "session_id", "cookie_value",
)
_COMPACT_SENSITIVE_FIELD_SUFFIXES = tuple(
    suffix.replace("_", "") for suffix in _SENSITIVE_FIELD_SUFFIXES
)


def contains_sensitive_text(value: Any) -> bool:
    """Return whether a bounded provenance/value tree contains sensitive text."""

    if type(value) is str:
        return _string_contains_sensitive_text(value)
    if isinstance(value, Mapping):
        return any(
            _is_sensitive_mapping_key(key) or contains_sensitive_text(key) or contains_sensitive_text(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(contains_sensitive_text(item) for item in value)
    return False


def _is_sensitive_mapping_key(value: Any) -> bool:
    if type(value) is not str:
        return False
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", normalized)
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", normalized)
    canonical = re.sub(r"[^a-z0-9]+", "_", normalized.casefold()).strip("_")
    compact = canonical.replace("_", "")
    return (
        canonical in _GENERIC_SENSITIVE_FIELDS
        or canonical.startswith("x_amz_")
        or compact.startswith("xamz")
        or any(
            canonical == suffix or canonical.endswith(f"_{suffix}")
            for suffix in _SENSITIVE_FIELD_SUFFIXES
        )
        or any(
            compact.endswith(suffix)
            for suffix in _COMPACT_SENSITIVE_FIELD_SUFFIXES
        )
    )


def _string_contains_sensitive_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    if any(
        _uri_contains_sensitive_text(match.group(0))
        for match in _LOGICAL_URI.finditer(normalized)
    ):
        return True
    path_text = _LOGICAL_URI.sub("", normalized)
    return bool(
        _PATH_ESCAPE.search(path_text)
        or _UNC_PATH.search(path_text)
        or _FILE_URI.search(normalized)
        or _POSIX_PATH.search(path_text)
        or _CREDENTIAL_KEY.search(path_text)
        or _BEARER_VALUE.search(path_text)
        or _X_AMZ_KEY.search(path_text)
        or _contains_drive_path(path_text)
    )


def _uri_contains_sensitive_text(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return True
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return True
    if parsed.scheme == "file":
        return True
    authority_host = _decoded_uri_component(parsed.hostname or "")
    if (
        not authority_host
        or "/" in authority_host
        or "\\" in authority_host
        or "@" in authority_host
        or _normalized_uri_component_contains_sensitive_text(authority_host)
    ):
        return True
    for component, inspect_posix_path in (
        (parsed.path, False),
        (parsed.query, True),
        (parsed.fragment, True),
    ):
        normalized = _decoded_uri_component(component)
        if normalized is None:
            return True
        if (
            _normalized_uri_component_contains_sensitive_text(normalized)
            or (inspect_posix_path and _POSIX_PATH.search(normalized))
        ):
            return True
    return False


def _normalized_uri_component_contains_sensitive_text(value: str) -> bool:
    return bool(
        _PATH_ESCAPE.search(value)
        or _UNC_PATH.search(value)
        or _FILE_URI.search(value)
        or _CREDENTIAL_KEY.search(value)
        or _BEARER_VALUE.search(value)
        or _X_AMZ_KEY.search(value)
        or _contains_drive_path(value)
    )


def _decoded_uri_component(value: str) -> str | None:
    decoded = value
    for _ in range(8):
        normalized = unicodedata.normalize("NFKC", decoded).casefold()
        unquoted = unquote(normalized)
        if normalized == decoded and unquoted == normalized:
            return normalized
        decoded = unquoted
    return None


def _contains_drive_path(value: str) -> bool:
    for match in _DRIVE_PATH.finditer(value):
        separator_index = match.end() - 1
        if (
            value[separator_index] == "/"
            and separator_index + 1 < len(value)
            and value[separator_index + 1] == "/"
        ):
            continue
        return True
    return False


__all__ = ["contains_sensitive_text"]
