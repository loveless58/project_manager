"""Host-independent parsing and resolution of logical object paths.

Logical paths are protocol identifiers, not native filesystem paths.  They
therefore always use canonical relative POSIX syntax, even when the adapter is
running on Windows.  Native :class:`~pathlib.Path` handling happens only after
the logical path has been validated and split into trusted components.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Union


class LogicalPathError(ValueError):
    """Raised when a logical path is unsafe or non-canonical."""


@dataclass(frozen=True)
class CanonicalLogicalPath:
    """Validated canonical POSIX logical path and its component sequence."""

    value: str
    parts: tuple[str, ...]


def parse_logical_path(value: str) -> CanonicalLogicalPath:
    """Validate a non-empty, relative, canonical POSIX logical path.

    Both POSIX and Windows path grammars are inspected explicitly so that the
    accepted protocol domain is identical on every host OS.
    """

    if not isinstance(value, str) or not value:
        raise LogicalPathError("logical path must be a non-empty string")
    if "\\" in value or "\x00" in value:
        raise LogicalPathError("logical path must use canonical POSIX separators")

    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
    ):
        raise LogicalPathError("logical path must be relative on POSIX and Windows")

    parts = tuple(value.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise LogicalPathError("logical path contains a non-canonical segment")
    if posix_path.as_posix() != value or posix_path.parts != parts:
        raise LogicalPathError("logical path is not canonical POSIX syntax")
    return CanonicalLogicalPath(value=value, parts=parts)


def resolve_logical_path(
    root: Union[str, Path],
    value: str,
) -> tuple[Path, CanonicalLogicalPath]:
    """Resolve a validated logical path beneath a trusted native root."""

    canonical = parse_logical_path(value)
    resolved_root = Path(root).expanduser().resolve()
    candidate = resolved_root.joinpath(*canonical.parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise LogicalPathError("logical path resolves outside configured root") from exc
    if candidate == resolved_root:
        raise LogicalPathError("logical path must name an entry below configured root")
    return candidate, canonical


__all__ = [
    "CanonicalLogicalPath",
    "LogicalPathError",
    "parse_logical_path",
    "resolve_logical_path",
]
