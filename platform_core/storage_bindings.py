from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import DocumentRef


class StorageBindingError(ValueError):
    """Base error for storage-binding registration and resolution."""


class StorageBindingNotFoundError(StorageBindingError):
    """No enabled binding owns the requested physical path."""


class AmbiguousStorageBindingError(StorageBindingError):
    """More than one enabled binding owns the requested physical path."""


@dataclass(frozen=True)
class StorageBinding:
    """One explicitly declared business-document storage location."""

    binding_id: str
    provider: str
    node_id: str
    logical_root: str
    physical_root: Path
    roles: tuple[str, ...]
    readable: bool
    writable: bool
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.binding_id, str) or not self.binding_id.strip():
            raise StorageBindingError("binding_id must be a non-empty string")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise StorageBindingError("provider must be a non-empty string")
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise StorageBindingError("node_id must be a non-empty string")
        if not isinstance(self.logical_root, str) or not self.logical_root.endswith("/"):
            raise StorageBindingError("logical_root must be a non-empty URI ending in '/'")
        if not self.logical_root.strip():
            raise StorageBindingError("logical_root must be a non-empty URI ending in '/'")
        object.__setattr__(
            self,
            "physical_root",
            Path(self.physical_root).expanduser().resolve(),
        )
        object.__setattr__(self, "roles", tuple(self.roles))


class StorageBindingRegistry:
    """Resolve business files only through an explicitly named binding."""

    def __init__(self, bindings: Iterable[StorageBinding]) -> None:
        self.bindings = tuple(bindings)
        self._by_id = {binding.binding_id: binding for binding in self.bindings}
        if len(self._by_id) != len(self.bindings):
            raise StorageBindingError("storage binding ids must be unique")

    def binding_for_id(self, binding_id: str) -> StorageBinding:
        try:
            return self._by_id[binding_id]
        except KeyError as exc:
            raise StorageBindingNotFoundError(
                "storage binding is not registered"
            ) from exc

    def document_ref_from_path(self, path: str | Path) -> DocumentRef:
        candidate = Path(path).expanduser().resolve()
        matches: list[tuple[StorageBinding, Path]] = []
        for binding in self.bindings:
            if not binding.enabled:
                continue
            try:
                relative_path = candidate.relative_to(binding.physical_root)
            except ValueError:
                continue
            if relative_path.parts:
                matches.append((binding, relative_path))

        if not matches:
            raise StorageBindingNotFoundError(
                "path is not contained by an enabled storage binding"
            )
        if len(matches) > 1:
            raise AmbiguousStorageBindingError(
                "path is contained by more than one enabled storage binding"
            )

        binding, relative_path = matches[0]
        object_key = relative_path.as_posix()
        return DocumentRef(
            storage_provider=binding.provider,
            object_key=object_key,
            logical_uri=f"{binding.logical_root}{object_key}",
            binding_id=binding.binding_id,
        )


__all__ = [
    "AmbiguousStorageBindingError",
    "StorageBinding",
    "StorageBindingError",
    "StorageBindingNotFoundError",
    "StorageBindingRegistry",
]
