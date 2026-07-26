"""Explicit runtime adapter composition for the project-manager application."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from integrations.document_store import DisabledDocumentStore, LocalDocumentStore
from integrations.document_store import DocumentStoreRouter
from integrations.pageindex import PageIndexStructureIndex
from integrations.projections import FilesystemProjectionWriter
from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult
from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex
from platform_core.registry import AdapterKind, AdapterRegistry
from platform_core.settings import AppSettings, validate_runtime_storage_isolation
from platform_core.storage_bindings import StorageBindingRegistry
from services.archive_targets import ArchiveTargetResolver


class DisabledStructureIndex:
    """An explicit no-op structure-index provider with a clear capability result."""

    name = "disabled"

    def probe(self) -> CapabilityReport:
        return CapabilityReport("blocked", self.name, "", "structure index is disabled")

    def index(self, request: StructureIndexRequest) -> StructureIndexResult:
        return StructureIndexResult(
            "blocked",
            self.name,
            "",
            (),
            "INDEX.CAPABILITY_DISABLED",
            "structure index is disabled",
        )


@dataclass(frozen=True)
class RuntimeAdapters:
    """The concrete port implementations selected for one application runtime."""

    document_store: DocumentStore
    structure_index: StructureIndex
    projection_writer: ProjectionWriter
    storage_binding_registry: Optional[StorageBindingRegistry] = None
    document_store_router: Optional[DocumentStoreRouter] = None
    retrieval_service: Optional[object] = None
    interpretation_service: Optional[object] = None
    archive_target_resolver: Optional[ArchiveTargetResolver] = None

    def summary(self) -> dict[str, str]:
        return {
            "document_store": self.document_store.name,
            "structure_index": self.structure_index.name,
            "projection_writer": self.projection_writer.name,
        }


def build_default_registry() -> AdapterRegistry:
    """Register every supported adapter explicitly, including disabled choices."""
    registry = AdapterRegistry()
    registry.register(
        AdapterKind.DOCUMENT_STORE,
        "local",
        lambda settings: LocalDocumentStore(settings.business_root),
    )
    registry.register(
        AdapterKind.DOCUMENT_STORE,
        "disabled",
        lambda settings: DisabledDocumentStore(),
    )
    registry.register(
        AdapterKind.STRUCTURE_INDEX,
        "disabled",
        lambda settings: DisabledStructureIndex(),
    )
    registry.register(
        AdapterKind.STRUCTURE_INDEX,
        "pageindex",
        lambda settings: PageIndexStructureIndex(
            settings.providers.pageindex_dir,
            workspace_root=settings.runtime_workspace / "pageindex",
        ),
    )
    registry.register(
        AdapterKind.PROJECTION_WRITER,
        "filesystem",
        lambda settings: FilesystemProjectionWriter(
            settings.providers.projection_root,
            protected_roots=tuple(binding.physical_root for binding in settings.storage_bindings),
        ),
    )
    return registry


def build_runtime_adapters(
    settings: AppSettings,
    registry: Optional[AdapterRegistry] = None,
) -> RuntimeAdapters:
    """Build the adapters configured by ``settings`` without implicit discovery."""
    selected = registry or build_default_registry()
    validate_runtime_storage_isolation(settings)
    storage_binding_registry = StorageBindingRegistry(settings.storage_bindings)
    stores_by_binding = {
        binding.binding_id: LocalDocumentStore(binding.physical_root)
        for binding in storage_binding_registry.bindings
        if binding.enabled and binding.readable and binding.provider == "local"
    }
    document_store_router = DocumentStoreRouter(storage_binding_registry, stores_by_binding)
    return RuntimeAdapters(
        document_store=selected.build(
            AdapterKind.DOCUMENT_STORE,
            settings.providers.document_store,
            settings,
        ),
        storage_binding_registry=storage_binding_registry,
        document_store_router=document_store_router,
        archive_target_resolver=ArchiveTargetResolver(storage_binding_registry),
        structure_index=selected.build(
            AdapterKind.STRUCTURE_INDEX,
            settings.providers.structure_index,
            settings,
        ),
        projection_writer=selected.build(
            AdapterKind.PROJECTION_WRITER,
            settings.providers.projection_writer,
            settings,
        ),
    )
