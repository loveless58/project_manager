import hashlib

from infrastructure.file_organizer.repository import FileOrganizationRepository
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry
from skills.file_organizer.archive import ArchiveSkill


def _registry(source_root, archive_root):
    return StorageBindingRegistry(
        [
            StorageBinding("incoming", "local", "node", "business://incoming/", source_root, ("source",), True, False),
            StorageBinding("archive", "local", "node", "business://archive/", archive_root, ("archive",), True, True),
        ]
    )


def _document(source, source_ref):
    return {
        "schema_version": "structured_document.v1",
        "source_ref": source_ref,
        "content_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
        "media_type": "text/markdown",
        "status": "success",
        "parser": "native_markdown",
        "text": "合同正文",
        "pages": [],
        "tables": [],
        "fields": {"project_code": "PRJ-001"},
    }


def _confirmed_item(database_path, source, source_ref, target_ref):
    with database_path.write_uow() as connection:
        repository = FileOrganizationRepository(connection)
        document_id = repository.upsert_document(_document(source, source_ref))
        repository.record_location(document_id, source_ref, current=True)
        run_id = repository.create_run("按项目归档")
        item_id = repository.record_item(run_id, document_id, {"status": "needs_confirmation"})
        repository.confirm_item(item_id, target_ref)
    return item_id


def test_archive_skill_moves_only_confirmed_item_and_updates_location(tmp_path, database_path):
    source_root = tmp_path / "source"
    archive_root = tmp_path / "archive"
    source_root.mkdir()
    archive_root.mkdir()
    source = source_root / "contract.md"
    source.write_text("contract", encoding="utf-8")
    source_ref = {"binding_id": "incoming", "logical_uri": "business://incoming/contract.md", "storage_provider": "local", "object_key": "contract.md"}
    target_ref = {"binding_id": "archive", "logical_uri": "business://archive/project-a/contract.md", "storage_provider": "local", "object_key": "project-a/contract.md"}
    item_id = _confirmed_item(database_path, source, source_ref, target_ref)

    result = ArchiveSkill(database_path.path, _registry(source_root, archive_root)).execute(item_id)

    target = archive_root / "project-a" / "contract.md"
    assert result["status"] == "executed"
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "contract"
    assert result["location"]["logical_uri"] == target_ref["logical_uri"]


def test_archive_skill_rejects_unconfirmed_item_without_touching_source(tmp_path, database_path):
    source_root = tmp_path / "source"
    archive_root = tmp_path / "archive"
    source_root.mkdir()
    archive_root.mkdir()
    source = source_root / "contract.md"
    source.write_text("contract", encoding="utf-8")
    source_ref = {"binding_id": "incoming", "logical_uri": "business://incoming/contract.md", "storage_provider": "local", "object_key": "contract.md"}
    with database_path.write_uow() as connection:
        repository = FileOrganizationRepository(connection)
        document_id = repository.upsert_document(_document(source, source_ref))
        repository.record_location(document_id, source_ref, current=True)
        run_id = repository.create_run("按项目归档")
        item_id = repository.record_item(run_id, document_id, {"status": "needs_confirmation"})

    result = ArchiveSkill(database_path.path, _registry(source_root, archive_root)).execute(item_id)

    assert result == {"status": "blocked", "reason": "ARCHIVE.NOT_CONFIRMED"}
    assert source.exists()
