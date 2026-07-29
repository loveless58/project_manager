"""The single, prepare-review-only Document Management Agent workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from connectors.artifacts.run_artifact_store import RunArtifactStore
from contracts.agent_run import build_agent_run
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from infrastructure.file_organizer.repository import FileOrganizationRepository

from .catalog import SkillCatalog


class DocumentManagementAgent:
    """Coordinate named Skills and Connectors without archive capabilities."""

    def __init__(
        self, database_path: str | Path, registry: Any, parse_skill: Any,
        facts_skill: Any, review_skill: Any, artifact_store: RunArtifactStore,
        *, source_binding_id: str, catalog: SkillCatalog | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._registry = registry
        self._parse_skill = parse_skill
        self._facts_skill = facts_skill
        self._review_skill = review_skill
        self._artifact_store = artifact_store
        self._source_binding_id = source_binding_id
        self._catalog = catalog or SkillCatalog()

    def prepare(self, files: Sequence[str], *, goal: str) -> dict[str, Any]:
        """Parse explicit files, publish review artifacts, and never archive."""
        if not files:
            raise ValueError("at least one explicit source file is required")
        source_refs = [self._source_ref(path) for path in files]
        loaded_skills: list[str] = []
        items: list[dict[str, Any]] = []
        document_results: list[dict[str, Any]] = []
        with SqliteUnitOfWork(self._database_path, mode="write") as uow:
            repository = FileOrganizationRepository(uow.connection)
            run_id = repository.create_run(goal)
            run = build_agent_run(run_id=run_id, goal=goal, input_refs=source_refs, status="prepared", artifacts={})
            for source_path, source_ref in zip(files, source_refs, strict=True):
                parsed = self._parse_skill.parse(source_path, source_ref=source_ref)
                document_id = repository.upsert_document(parsed)
                repository.record_location(document_id, source_ref, current=True)
                selected = self._catalog.for_document(parsed)
                for skill_name in selected:
                    if skill_name not in loaded_skills:
                        loaded_skills.append(skill_name)
                facts = self._facts_skill.extract(parsed) if "document_facts" in selected else {}
                document_result = self._review_skill.build(run, document_id, parsed, facts)
                document_json, review_markdown = self._artifact_store.publish_document(run, document_result)
                repository.record_artifact(run_id, document_id, document_json)
                repository.record_artifact(run_id, document_id, review_markdown)
                item_id = repository.record_item(run_id, document_id, {
                    "schema_version": "document_review_proposal.v1",
                    "decision": document_result["decision"],
                })
                items.append({
                    "item_id": item_id,
                    "document_id": document_id,
                    "decision": document_result["decision"],
                    "artifacts": {"document_json": document_json, "review_markdown": review_markdown},
                })
                document_results.append(document_result)
            run["status"] = "awaiting_review"
            run["loaded_skills"] = loaded_skills
            run["items"] = items
            run_json, run_summary = self._artifact_store.publish_run(run, document_results)
            run["artifacts"] = {"run_json": run_json, "run_summary": run_summary}
            uow.commit()
        return run

    def _source_ref(self, source_path: str) -> dict[str, str]:
        reference = self._registry.document_ref_from_path(source_path)
        if reference.binding_id != self._source_binding_id:
            raise ValueError("source file is not in the selected source binding")
        return {
            "binding_id": reference.binding_id,
            "logical_uri": reference.logical_uri,
            "storage_provider": reference.storage_provider,
            "object_key": reference.object_key,
        }


__all__ = ["DocumentManagementAgent"]
