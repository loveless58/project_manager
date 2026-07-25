"""Canonical recursive detection for sensitive paths and credential material."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
import unicodedata
from typing import Any


_DRIVE_PATH = re.compile(r"[a-z]:[\\/]", re.IGNORECASE)
_UNC_PATH = re.compile(r"(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+")  # repo-hygiene: allow=synthetic-path
_FILE_URI = re.compile(r"file\s*:\s*/{2,}", re.IGNORECASE)
_POSIX_PATH = re.compile(r"(?<![\w.~%:/-])/(?!/)[^\s\]\[{}]+", re.IGNORECASE)
_SENSITIVE_COMPOUND_KEYS = (
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"secret[_-]?access[_-]?key|session[_-]?id|cookie[_-]?value"
)
_CREDENTIAL_KEY = re.compile(
    rf"(?<![a-z0-9])(?:authorization|bearer|token|key|password|cookie|session|"
    rf"credential|signature|{_SENSITIVE_COMPOUND_KEYS})\s*[\"']?\s*[:=]",
    re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"(?<![a-z0-9])bearer\s+\S", re.IGNORECASE)
_X_AMZ_KEY = re.compile(r"(?<![a-z0-9])x-amz-[a-z0-9-]+\s*[\"']?\s*[:=]", re.IGNORECASE)
_SAFE_LOGICAL_URI = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_PATH_ESCAPE = re.compile(
    r"(?:^|[\\/\s])(?:~[\\/]|\.\.[\\/])|\$(?:home|\{home\})[\\/]",
    re.IGNORECASE,
)
_MAPPING_CREDENTIAL_KEY = re.compile(
    rf"(?:authorization|bearer|token|key|password|cookie|session|credential|signature|"
    rf"{_SENSITIVE_COMPOUND_KEYS}|x-amz-[a-z0-9-]+)",
    re.IGNORECASE,
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
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return bool(_MAPPING_CREDENTIAL_KEY.fullmatch(normalized))


def _string_contains_sensitive_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    path_text = _SAFE_LOGICAL_URI.sub("", normalized)
    if (
        _PATH_ESCAPE.search(path_text)
        or _UNC_PATH.search(path_text)
        or _FILE_URI.search(normalized)
        or _POSIX_PATH.search(path_text)
        or _CREDENTIAL_KEY.search(normalized)
        or _BEARER_VALUE.search(normalized)
        or _X_AMZ_KEY.search(normalized)
    ):
        return True
    for match in _DRIVE_PATH.finditer(normalized):
        separator_index = match.end() - 1
        if (
            normalized[separator_index] == "/"
            and separator_index + 1 < len(normalized)
            and normalized[separator_index + 1] == "/"
        ):
            continue
        return True
    return False


__all__ = ["contains_sensitive_text"]
