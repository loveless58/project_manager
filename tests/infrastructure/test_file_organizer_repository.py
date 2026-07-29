import sqlite3
from pathlib import Path

from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from infrastructure.file_organizer.repository import FileOrganizationRepository
from tests.database.migration_authorization import create_migration_authorization


STRUCTURED_DOCUMENT = {
    "schema_version": "structured_document.v1",
    "source_ref": {
        "binding_id": "source",
        "logical_uri": "business://source/contracts/CT-001.docx",
        "storage_provider": "local",
        "object_key": "contracts/CT-001.docx",
    },
    "content_hash": "a" * 64,
    "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "status": "success",
    "parser": "native_docx",
    "text": "合同编号：CT-001",
    "pages": [],
    "tables": [],
    "fields": {"project_code": "PRJ-001", "contract_code": "CT-001"},
}


def _migrate(database_path: Path, tmp_path: Path) -> None:
    catalog = load_migration_catalog(Path("migrations/sqlite"))
    initialize_database(database_path)
    authorization = create_migration_authorization(
        database_path,
        catalog,
        tmp_path / "migration-backup.sqlite3",
    )
    apply_pending_migrations(
        database_path,
        catalog,
        backup_authorization=authorization,
    )


def test_repository_records_document_current_location_and_history(tmp_path):
    database_path = tmp_path / "file-organizer.sqlite3"
    _migrate(database_path, tmp_path)
    source_location = STRUCTURED_DOCUMENT["source_ref"]
    archive_location = {
        "binding_id": "archive",
        "logical_uri": "business://archive/project-a/contracts/CT-001.docx",
        "storage_provider": "local",
        "object_key": "project-a/contracts/CT-001.docx",
    }

    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        document_id = repository.upsert_document(STRUCTURED_DOCUMENT)
        repository.record_location(document_id, source_location, current=True)
        repository.record_location(document_id, archive_location, current=True)
        uow.commit()

    with SqliteUnitOfWork(database_path) as uow:
        history = FileOrganizationRepository(uow.connection).locations_for(document_id)

    assert [entry["logical_uri"] for entry in history] == [
        source_location["logical_uri"],
        archive_location["logical_uri"],
    ]
    assert history[-1]["is_current"] is True
    assert history[0]["is_current"] is False


def test_repository_keeps_project_link_as_candidate_until_confirmed(tmp_path):
    database_path = tmp_path / "file-organizer.sqlite3"
    _migrate(database_path, tmp_path)

    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        project_id = repository.create_project("项目 A", "PRJ-001")
        document_id = repository.upsert_document(STRUCTURED_DOCUMENT)
        link_id = repository.link_document_to_project(
            document_id,
            project_id,
            "candidate",
            "project_code",
            "medium",
        )
        repository.confirm_project_link(link_id)
        candidates = repository.find_project_candidates({"project_code": "PRJ-001"})
        uow.commit()

    with sqlite3.connect(database_path) as connection:
        state = connection.execute(
            "SELECT link_state FROM document_project_links WHERE id = ?", (link_id,)
        ).fetchone()[0]

    assert state == "confirmed"
    assert candidates == [{"id": project_id, "name": "项目 A", "project_code": "PRJ-001"}]


def test_repository_records_idempotent_run_item_and_confirmation(tmp_path):
    database_path = tmp_path / "file-organizer.sqlite3"
    _migrate(database_path, tmp_path)
    target_location = {
        "binding_id": "archive",
        "logical_uri": "business://archive/project-a/contracts/CT-001.docx",
        "storage_provider": "local",
        "object_key": "project-a/contracts/CT-001.docx",
    }

    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        document_id = repository.upsert_document(STRUCTURED_DOCUMENT)
        run_id = repository.create_run("按项目整理")
        first_item_id = repository.record_item(run_id, document_id, {"status": "needs_confirmation"})
        second_item_id = repository.record_item(run_id, document_id, {"status": "needs_confirmation"})
        repository.confirm_item(first_item_id, target_location)
        repository.complete_archive(first_item_id, target_location)
        uow.commit()

    assert first_item_id == second_item_id
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT item_state, target_location_json FROM organization_items WHERE id = ?",
            (first_item_id,),
        ).fetchone()
    assert row[0] == "executed"
    assert "business://archive/project-a" in row[1]
