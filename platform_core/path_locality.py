"""Reusable node-local path validation for runtime-owned artifacts.

The validator intentionally detects only locations that are unambiguously remote
from their syntax (UNC paths and SMB/NFS/AFP URIs).  A Windows drive letter does
not reveal whether the drive is fixed or mapped, so deployment checks must also
verify mapped-drive configuration outside this portable core.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Union


PathLike = Union[str, os.PathLike[str]]
_NETWORK_URI = re.compile(r"^(?:smb|nfs|afp):[\\/]{1,2}", re.IGNORECASE)
_WINDOWS_EXTENDED_LOCAL_PATH = re.compile(r"^\\\\\?\\[A-Za-z]:[\\/]", re.IGNORECASE)


class NodeLocalPathError(ValueError):
    """A runtime-owned path is not demonstrably separate and node-local."""


def is_obvious_network_location(value: PathLike) -> bool:
    """Return whether *value* is syntactically an obvious network location."""
    raw = os.fspath(value).strip()
    if _NETWORK_URI.match(raw):
        return True
    if _WINDOWS_EXTENDED_LOCAL_PATH.match(raw):
        return False
    return raw.startswith((r"\\", "//"))


def is_path_within(path: PathLike, root: PathLike) -> bool:
    """Return whether *path* equals or descends from canonical *root*."""
    candidate = Path(path).expanduser().resolve()
    canonical_root = Path(root).expanduser().resolve()
    try:
        candidate.relative_to(canonical_root)
    except ValueError:
        return False
    return True


def ensure_node_local_path(
    *,
    field_name: str,
    raw_value: PathLike,
    resolved_path: PathLike,
    business_root: PathLike | None,
) -> Path:
    """Validate and return a resolved runtime path.

    Runtime-owned state must not use an obvious network location and must not be
    equal to, or descend from, the business-data root.  The raw value is retained
    for URI/UNC detection because ``Path.resolve()`` can obscure that syntax.
    """
    if is_obvious_network_location(raw_value):
        raise NodeLocalPathError(f"{field_name} must be node-local")

    candidate = Path(resolved_path).expanduser().resolve()
    if business_root is not None:
        root = Path(business_root).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            pass
        else:
            raise NodeLocalPathError(
                f"{field_name} must not be inside business_root"
            )
    return candidate


__all__ = [
    "NodeLocalPathError",
    "ensure_node_local_path",
    "is_obvious_network_location",
    "is_path_within",
]
