from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Union

from platform_core.models import DocumentRef, ObjectStat
from platform_core.logical_paths import LogicalPathError, resolve_logical_path


class DocumentStorePathError(ValueError):
    """Raised when a document reference is incompatible with this local store."""


class LocalDocumentStore:
    """Read document objects rooted at a trusted, exclusively managed directory.

    The configured root and its directory components must not be concurrently
    replaced by untrusted actors while this adapter performs I/O.
    """

    name = "local"

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    def _resolve(self, ref: DocumentRef) -> Path:
        if ref.storage_provider != self.name:
            raise DocumentStorePathError(
                f"provider mismatch: expected {self.name}, got {ref.storage_provider}"
            )
        try:
            candidate, canonical = resolve_logical_path(self.root, ref.object_key)
        except LogicalPathError as exc:
            raise DocumentStorePathError(
                "object key is non-canonical or resolves outside configured root"
            ) from exc
        expected_uri = f"local://{canonical.value}"
        if ref.logical_uri != expected_uri:
            raise DocumentStorePathError(
                "logical URI does not match the canonical local object key"
            )
        return candidate

    def stat(self, ref: DocumentRef) -> ObjectStat:
        path = self._resolve(ref)
        details = path.stat()
        etag = hashlib.sha256(
            f"{details.st_size}:{details.st_mtime_ns}".encode("ascii")
        ).hexdigest()
        return ObjectStat(
            ref=ref,
            size_bytes=details.st_size,
            modified_at=details.st_mtime,
            etag=etag,
            available=path.is_file(),
        )

    def open_read(self, ref: DocumentRef) -> BinaryIO:
        return self._resolve(ref).open("rb")
