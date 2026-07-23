from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Union

from platform_core.models import ProjectionRef, ProjectionRequest


class ProjectionPathError(ValueError):
    """Raised when a projection would be written outside the configured root."""


class FilesystemProjectionWriter:
    """Atomically write UTF-8 projections beneath a configured root directory."""

    name = "filesystem"

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    def _resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ProjectionPathError("projection path resolves outside configured root") from exc
        return target

    def write(self, request: ProjectionRequest) -> ProjectionRef:
        target = self._resolve(request.relative_path)
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
            logical_uri=f"projection://{request.relative_path.replace(os.sep, '/')}",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
