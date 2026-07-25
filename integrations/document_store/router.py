from __future__ import annotations

from dataclasses import replace
from typing import BinaryIO, Mapping

from platform_core.models import DocumentRef, ObjectStat
from platform_core.ports import DocumentStore


class DocumentStoreRoutingError(ValueError):
    """A document reference cannot be read through the configured bindings."""


class DocumentStoreRouter:
    """Route read-only document operations by an explicit storage binding id."""

    name = "router"

    def __init__(self, stores_by_binding: Mapping[str, DocumentStore]) -> None:
        self.stores_by_binding = dict(stores_by_binding)

    def _store_for(self, ref: DocumentRef) -> DocumentStore:
        binding_id = ref.binding_id.strip()
        if not binding_id:
            raise DocumentStoreRoutingError("document reference requires binding_id")
        try:
            return self.stores_by_binding[binding_id]
        except KeyError as exc:
            raise DocumentStoreRoutingError(
                "document reference binding_id is not configured"
            ) from exc

    @staticmethod
    def _adapter_ref(ref: DocumentRef) -> DocumentRef:
        if ref.storage_provider != "local":
            return ref
        return replace(ref, logical_uri=f"local://{ref.object_key}")

    def stat(self, ref: DocumentRef) -> ObjectStat:
        store = self._store_for(ref)
        result = store.stat(self._adapter_ref(ref))
        return replace(result, ref=ref)

    def open_read(self, ref: DocumentRef) -> BinaryIO:
        return self._store_for(ref).open_read(self._adapter_ref(ref))


__all__ = ["DocumentStoreRouter", "DocumentStoreRoutingError"]
