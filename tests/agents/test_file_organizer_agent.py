import hashlib

from agents.file_organizer.agent import FileOrganizerAgent
from infrastructure.file_organizer.repository import FileOrganizationRepository
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry


class _ParseSkill:
    def parse(self, path, *, source_ref):
        with open(path, "rb") as file_handle:
            content_hash = hashlib.sha256(file_handle.read()).hexdigest()
        return {
            "schema_version": "structured_document.v1",
            "source_ref": source_ref,
            "content_hash": content_hash,
            "media_type": "text/markdown",
            "status": "success",
            "parser": "native_markdown",
            "text": "项目编号：SYN-001\n合同正文",
            "pages": [],
            "tables": [],
            "fields": {"project_code": "SYN-001"},
        }


def _registry(source_root, archive_root):
    return StorageBindingRegistry([
        StorageBinding("incoming", "local", "node", "business://incoming/", source_root, ("source",), True, False),
        StorageBinding("archive", "local", "node", "business://archive/", archive_root, ("archive",), True, True),
    ])


def test_agent_prepares_explicit_file_then_executes_only_confirmed_item(tmp_path, database_path):
    source_root = tmp_path / "source"
    archive_root = tmp_path / "archive"
    source_root.mkdir()
    archive_root.mkdir()
    source = source_root / "contract.md"
    source.write_text("contract", encoding="utf-8")
    registry = _registry(source_root, archive_root)
    with database_path.write_uow() as connection:
        FileOrganizationRepository(connection).create_project("项目 A", "SYN-001")
    agent = FileOrganizerAgent(
        database_path.path,
        registry,
        _ParseSkill(),
        source_binding_id="incoming",
        archive_binding_id="archive",
    )

    prepared = agent.prepare([str(source)], goal="按项目整理")

    assert len(prepared["items"]) == 1
    assert prepared["items"][0]["proposal"]["status"] == "needs_confirmation"
    assert source.exists()

    result = agent.execute_confirmed(
        prepared["run_id"], [{"item_id": prepared["items"][0]["item_id"], "decision": "confirmed"}]
    )

    assert result["items"][0]["status"] == "executed"
    assert not source.exists()
    assert (archive_root / "SYN-001" / "contract.md").exists()
