from typing import TYPE_CHECKING
from .models import (
    CapabilityReport,
    DocumentRef,
    ObjectStat,
    ProjectionRef,
    ProjectionRequest,
    StructureIndexRequest,
    StructureIndexResult,
)
from .ports import DocumentStore, ProjectionWriter, Repository, StructureIndex, UnitOfWork
from .registry import AdapterKind, AdapterRegistry, AdapterRegistryError
if TYPE_CHECKING:
    from .composition import RuntimeAdapters

from .settings import (
    AppSettings,
    DatabaseSettings,
    ProviderSettings,
    SettingsError,
    load_app_settings,
)


def __getattr__(name: str):
    if name in {"RuntimeAdapters", "build_default_registry", "build_runtime_adapters"}:
        from .composition import RuntimeAdapters, build_default_registry, build_runtime_adapters

        return {
            "RuntimeAdapters": RuntimeAdapters,
            "build_default_registry": build_default_registry,
            "build_runtime_adapters": build_runtime_adapters,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AdapterKind",
    "AdapterRegistry",
    "AdapterRegistryError",
    "AppSettings",
    "CapabilityReport",
    "DatabaseSettings",
    "DocumentRef",
    "DocumentStore",
    "ObjectStat",
    "ProjectionRef",
    "ProjectionRequest",
    "ProjectionWriter",
    "ProviderSettings",
    "Repository",
    "SettingsError",
    "StructureIndex",
    "StructureIndexRequest",
    "StructureIndexResult",
    "UnitOfWork",
    "RuntimeAdapters",
    "build_default_registry",
    "build_runtime_adapters",
    "load_app_settings",
]
