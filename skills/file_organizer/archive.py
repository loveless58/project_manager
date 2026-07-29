"""Confirmed-only physical archive execution for the File Organizer Agent."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from infrastructure.file_organizer.repository import FileOrganizationRepository


class ArchiveSkill:
    def __init__(self, database_path: Path, registry: Any) -> None:
        self._database_path = Path(database_path)
        self._registry = registry

    def execute(self, item_id: str) -> dict[str, Any]:
        with SqliteUnitOfWork(self._database_path, mode="write") as uow:
            repository = FileOrganizationRepository(uow.connection)
            context = repository.archive_context(item_id)
            if context is None or context["item_state"] != "confirmed":
                return {"status": "blocked", "reason": "ARCHIVE.NOT_CONFIRMED"}
            source = context["source_location"]
            target = context["target_location"]
            try:
                source_path = self._resolve(source, writable=False)
                target_path = self._resolve(target, writable=True)
            except ValueError:
                return {"status": "blocked", "reason": "ARCHIVE.BINDING_INVALID"}
            if not source_path.is_file():
                return {"status": "blocked", "reason": "ARCHIVE.SOURCE_UNAVAILABLE"}
            if target_path.exists():
                return {"status": "blocked", "reason": "ARCHIVE.TARGET_EXISTS"}
            if _sha256(source_path) != context["content_hash"]:
                return {"status": "blocked", "reason": "ARCHIVE.HASH_MISMATCH"}
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source_path, target_path)
                if _sha256(target_path) != context["content_hash"]:
                    return {"status": "failed", "reason": "ARCHIVE.RECONCILIATION_REQUIRED"}
                repository.record_location(context["document_id"], target, current=True)
                repository.complete_archive(item_id, target)
                uow.commit()
            except Exception:
                return {
                    "status": "failed",
                    "reason": "ARCHIVE.RECONCILIATION_REQUIRED",
                    "source_location": source["logical_uri"],
                    "target_location": target["logical_uri"],
                }
        return {"status": "executed", "item_id": item_id, "location": target}

    def _resolve(self, location: dict[str, str], *, writable: bool) -> Path:
        binding = self._registry.binding_for_id(location["binding_id"])
        if not binding.enabled or (not binding.writable if writable else not binding.readable):
            raise ValueError("storage binding is unavailable")
        target = (binding.physical_root / location["object_key"]).resolve()
        try:
            target.relative_to(binding.physical_root)
        except ValueError as error:
            raise ValueError("storage target escapes binding") from error
        return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
