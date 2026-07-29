"""Host-facing workflow for proposing and executing file organization."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from infrastructure.file_organizer.repository import FileOrganizationRepository
from skills.file_organizer.archive import ArchiveSkill
from skills.file_organizer.business_query import BusinessQuerySkill


class FileOrganizerAgent:
    """Prepare explicit inputs, then archive only explicit confirmations."""

    def __init__(
        self,
        database_path: Path,
        registry: Any,
        parse_skill: Any,
        *,
        source_binding_id: str,
        archive_binding_id: str,
        structure_index: Any = None,
        pageindex_min_text_length: int = 16_000,
    ) -> None:
        self._database_path = Path(database_path)
        self._registry = registry
        self._parse_skill = parse_skill
        self._source_binding_id = source_binding_id
        self._archive_binding_id = archive_binding_id
        self._structure_index = structure_index
        self._pageindex_min_text_length = pageindex_min_text_length

    def prepare(self, files: Sequence[str], *, goal: str) -> dict[str, Any]:
        if not files:
            raise ValueError("at least one explicit source file is required")
        items: list[dict[str, Any]] = []
        with SqliteUnitOfWork(self._database_path, mode="write") as uow:
            repository = FileOrganizationRepository(uow.connection)
            run_id = repository.create_run(goal)
            query_skill = BusinessQuerySkill(
                repository,
                self._structure_index,
                pageindex_min_text_length=self._pageindex_min_text_length,
            )
            for source_path in files:
                source_ref = self._source_ref(source_path)
                parsed = self._parse_skill.parse(source_path, source_ref=source_ref)
                if parsed.get("status") != "success":
                    items.append({"source_ref": source_ref, "status": "needs_review", "reason": parsed.get("reason", "PARSE.UNAVAILABLE")})
                    continue
                document_id = repository.upsert_document(parsed)
                repository.record_location(document_id, source_ref, current=True)
                proposal = query_skill.propose(parsed, goal=goal, source_path=source_path)
                proposal["target_location"] = self._default_target(source_ref, proposal)
                item_id = repository.record_item(run_id, document_id, proposal)
                items.append({"item_id": item_id, "document_id": document_id, "proposal": proposal})
            uow.commit()
        return {"schema_version": "organization_run.v1", "run_id": run_id, "status": "prepared", "items": items}

    def execute_confirmed(self, run_id: str, confirmations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        scheduled: list[str] = []
        results: list[dict[str, Any]] = []
        with SqliteUnitOfWork(self._database_path, mode="write") as uow:
            repository = FileOrganizationRepository(uow.connection)
            for confirmation in confirmations:
                item_id = str(confirmation.get("item_id", ""))
                decision = str(confirmation.get("decision", ""))
                item = repository.item_for_run(run_id, item_id)
                if item is None:
                    results.append({"item_id": item_id, "status": "blocked", "reason": "CONFIRMATION.ITEM_UNKNOWN"})
                    continue
                if decision == "skipped":
                    repository.skip_item(item_id)
                    results.append({"item_id": item_id, "status": "skipped"})
                    continue
                if decision not in {"confirmed", "modified_target"}:
                    results.append({"item_id": item_id, "status": "blocked", "reason": "CONFIRMATION.DECISION_INVALID"})
                    continue
                target = confirmation.get("target_location") if decision == "modified_target" else item["proposal"].get("target_location")
                if not isinstance(target, Mapping):
                    results.append({"item_id": item_id, "status": "blocked", "reason": "CONFIRMATION.TARGET_INVALID"})
                    continue
                repository.confirm_item(item_id, target)
                scheduled.append(item_id)
            uow.commit()
        archive_skill = ArchiveSkill(self._database_path, self._registry)
        for item_id in scheduled:
            results.append(archive_skill.execute(item_id))
        return {"schema_version": "archive_results.v1", "run_id": run_id, "items": results}

    def _source_ref(self, source_path: str) -> dict[str, str]:
        reference = self._registry.document_ref_from_path(source_path)
        if reference.binding_id != self._source_binding_id:
            raise ValueError("source file is not in the selected source binding")
        return {"binding_id": reference.binding_id, "logical_uri": reference.logical_uri, "storage_provider": reference.storage_provider, "object_key": reference.object_key}

    def _default_target(self, source_ref: Mapping[str, str], proposal: Mapping[str, Any]) -> dict[str, str]:
        binding = self._registry.binding_for_id(self._archive_binding_id)
        candidate = proposal.get("candidate_project")
        project_key = candidate.get("project_code") if isinstance(candidate, Mapping) else None
        folder = _safe_segment(str(project_key or "needs-review"))
        object_key = f"{folder}/{Path(source_ref['object_key']).name}"
        return {"binding_id": binding.binding_id, "logical_uri": f"{binding.logical_root}{object_key}", "storage_provider": binding.provider, "object_key": object_key}


def _safe_segment(value: str) -> str:
    normalized = "".join(character for character in value if character.isalnum() or character in {"-", "_"})
    return normalized or "needs-review"
