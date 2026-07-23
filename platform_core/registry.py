from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Tuple

from platform_core.settings import AppSettings


AdapterFactory = Callable[[AppSettings], object]


class AdapterKind(str, Enum):
    DOCUMENT_STORE = "document_store"
    STRUCTURE_INDEX = "structure_index"
    PROJECTION_WRITER = "projection_writer"


class AdapterRegistryError(LookupError):
    pass


class AdapterRegistry:
    """An explicit mapping from adapter kinds and names to factories.

    All names, including ``disabled``, must be registered deliberately. The
    registry never imports or discovers adapters implicitly.
    """

    def __init__(self) -> None:
        self._factories: Dict[Tuple[AdapterKind, str], AdapterFactory] = {}

    def register(self, kind: AdapterKind, name: str, factory: AdapterFactory) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise AdapterRegistryError("adapter name must not be empty")
        key = (kind, normalized)
        if key in self._factories:
            raise AdapterRegistryError(f"{kind.value}:{normalized} already registered")
        self._factories[key] = factory

    def build(self, kind: AdapterKind, name: str, settings: AppSettings) -> object:
        normalized = name.strip().lower()
        factory = self._factories.get((kind, normalized))
        if factory is None:
            available = ",".join(self.names(kind)) or "<none>"
            raise AdapterRegistryError(
                f"unknown {kind.value} adapter {normalized!r}; available={available}"
            )
        return factory(settings)

    def names(self, kind: AdapterKind) -> List[str]:
        return sorted(name for registered_kind, name in self._factories if registered_kind == kind)
