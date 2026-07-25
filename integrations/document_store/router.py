from __future__ import annotations

from dataclasses import replace
from typing import BinaryIO, Mapping

from platform_core.logical_paths import LogicalPathError, parse_logical_path
from platform_core.models import DocumentRef, ObjectStat
from platform_core.ports import DocumentStore
from platform_core.storage_bindings import (
    StorageBindingNotFoundError,
    StorageBindingRegistry,
)


class DocumentStoreRoutingError(ValueError):
    """A document reference cannot be read through the configured bindings."""


class DocumentStoreRouter:
    """Route read-only document operations by verified storage-binding metadata."""

    name = "router"

    def __init__(
        self,
        storage_binding_registry: StorageBindingRegistry,
        stores_by_binding: Mapping[str, DocumentStore],
    ) -> None:
        self.storage_binding_registry = storage_binding_registry
        self.stores_by_binding = dict(stores_by_binding)

    def _store_for(self, ref: DocumentRef) -> DocumentStore:
        binding_id = ref.binding_id
        if (
            not isinstance(binding_id, str)
            or not binding_id
            or binding_id != binding_id.strip()
        ):
            raise DocumentStoreRoutingError(
                "document reference requires a canonical binding_id"
            )
        try:
            binding = self.storage_binding_registry.binding_for_id(binding_id)
        except StorageBindingNotFoundError as exc:
            raise DocumentStoreRoutingError(
                "document reference binding_id is not configured"
            ) from exc
        if not binding.enabled or not binding.readable:
            raise DocumentStoreRoutingError(
                "document reference binding is not readable"
            )
        if ref.storage_provider != binding.provider:
            raise DocumentStoreRoutingError(
                "document reference storage_provider does not match binding"
            )
        try:
            object_key = parse_logical_path(ref.object_key).value
        except LogicalPathError as exc:
            raise DocumentStoreRoutingError(
                "document reference object_key is not canonical"
            ) from exc
        expected_uri = f"{binding.logical_root}{object_key}"
        if ref.logical_uri != expected_uri:
            raise DocumentStoreRoutingError(
                "document reference logical_uri does not match binding"
            )
        try:
            store = self.stores_by_binding[binding_id]
        except KeyError as exc:
            raise DocumentStoreRoutingError(
                "document reference binding_id is not configured"
            ) from exc
        if getattr(store, "name", None) != binding.provider:
            raise DocumentStoreRoutingError("binding provider is not supported")
        return store

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
