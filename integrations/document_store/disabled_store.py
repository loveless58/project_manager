from __future__ import annotations

from typing import BinaryIO

from platform_core.models import DocumentRef, ObjectStat


class DocumentStoreUnavailable(RuntimeError):
    """Raised when a document-store operation is unavailable in this runtime."""


class DisabledDocumentStore:
    """Document-store adapter that explicitly rejects all I/O operations."""

    name = "disabled"

    def stat(self, ref: DocumentRef) -> ObjectStat:
        raise DocumentStoreUnavailable("document store is disabled on this runtime")

    def open_read(self, ref: DocumentRef) -> BinaryIO:
        raise DocumentStoreUnavailable("document store is disabled on this runtime")
