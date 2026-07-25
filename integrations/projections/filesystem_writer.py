from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Union

from platform_core.models import ProjectionRef, ProjectionRequest
from platform_core.logical_paths import LogicalPathError, resolve_logical_path


class ProjectionPathError(ValueError):
    """Raised when a projection would be written outside the configured root."""


class FilesystemProjectionWriter:
    """Atomically write projections beneath a trusted, exclusively managed root.

    The configured root and its directory components must not be concurrently
    replaced by untrusted actors while this adapter performs I/O.
    """

    name = "filesystem"

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    def _resolve(self, relative_path: str) -> tuple[Path, str]:
        try:
            target, canonical = resolve_logical_path(self.root, relative_path)
        except LogicalPathError as exc:
            raise ProjectionPathError(
                "projection path must name a file below configured root; "
                "non-canonical paths may resolve outside configured root"
            ) from exc
        return target, canonical.value

    def write(self, request: ProjectionRequest) -> ProjectionRef:
        target, logical_path = self._resolve(request.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = request.content.encode("utf-8")
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_name = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
        return ProjectionRef(
            provider=self.name,
            logical_uri=f"projection://{logical_path}",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
