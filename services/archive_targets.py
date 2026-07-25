"""Read-only, explicit archive target binding resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from platform_core.models import DocumentRef
from platform_core.storage_bindings import StorageBindingNotFoundError, StorageBindingRegistry


@dataclass(frozen=True)
class ArchiveTargetResolution:
    destination_status: str
    candidate_target_binding_ids: tuple[str, ...]
    resolved_binding_id: str
    diagnostics: tuple[str, ...]


class ArchiveTargetResolver:
    """Resolve only caller-declared binding IDs; never infer a physical target."""

    def __init__(self, registry: StorageBindingRegistry) -> None:
        self.registry = registry

    def resolve(
        self,
        source_ref: DocumentRef,
        candidate_target_binding_ids: Iterable[str] = (),
    ) -> ArchiveTargetResolution:
        candidates = tuple(dict.fromkeys(candidate_target_binding_ids))
        diagnostics: list[str] = []
        eligible: list[str] = []
        invalid = False
        for binding_id in candidates:
            if not isinstance(binding_id, str) or not binding_id or binding_id != binding_id.strip():
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.INVALID_BINDING_ID")
                continue
            if binding_id == source_ref.binding_id:
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.SOURCE_BINDING_FORBIDDEN")
                continue
            if binding_id.casefold() in {"synologydrive", "business_root", "legacy"}:
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.DEFAULT_BINDING_FORBIDDEN")
                continue
            try:
                binding = self.registry.binding_for_id(binding_id)
            except StorageBindingNotFoundError:
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.BINDING_NOT_FOUND")
                continue
            if not binding.enabled:
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.BINDING_DISABLED")
            elif not binding.writable:
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.BINDING_READ_ONLY")
            elif "archive_target" not in binding.roles:
                invalid = True
                diagnostics.append("ARCHIVE_TARGET.ROLE_MISSING")
            else:
                eligible.append(binding_id)

        resolved = len(candidates) == 1 and len(eligible) == 1 and not invalid
        if not candidates:
            diagnostics.append("ARCHIVE_TARGET.NOT_EXPLICIT")
        elif len(candidates) != 1:
            diagnostics.append("ARCHIVE_TARGET.AMBIGUOUS")
        if not resolved:
            diagnostics.append("ARCHIVE_TARGET.UNRESOLVED")
        return ArchiveTargetResolution(
            destination_status="resolved" if resolved else "unresolved",
            candidate_target_binding_ids=tuple(eligible),
            resolved_binding_id=eligible[0] if resolved else "",
            diagnostics=tuple(dict.fromkeys(diagnostics)),
        )


__all__ = ["ArchiveTargetResolution", "ArchiveTargetResolver"]
