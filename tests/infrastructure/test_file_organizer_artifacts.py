from __future__ import annotations

from pathlib import Path

import pytest

from contracts.artifact_reference import build_artifact_reference
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.migration_runner import apply_pending_migrations, initialize_database
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from infrastructure.file_organizer.repository import FileOrganizationRepository
from tests.database.migration_authorization import create_migration_authorization


DOCUMENT = {
    "schema_version": "structured_document.v1",
    "source_ref": {
        "binding_id": "source",
        "logical_uri": "business://source/invoice.pdf",
        "storage_provider": "local",
        "object_key": "invoice.pdf",
    },
    "content_hash": "a" * 64,
    "media_type": "application/pdf",
    "status": "success",
    "parser": "native_pdf",
    "text": "发票正文",
    "pages": [],
    "tables": [],
    "fields": {},
}


def _migrate(database_path: Path, tmp_path: Path) -> None:
    catalog = load_migration_catalog(Path("migrations/sqlite"))
    initialize_database(database_path)
    apply_pending_migrations(
        database_path,
        catalog,
        backup_authorization=create_migration_authorization(
            database_path, catalog, tmp_path / "backup.sqlite3"
        ),
    )


def _artifact(kind: str, digest: str = "b" * 64) -> dict[str, object]:
    suffix = "document.json" if kind == "document_json" else "review.md"
    return build_artifact_reference(
        kind=kind,
        logical_uri=f"projection://runs/run-1/documents/doc-1/{suffix}",
        sha256=digest,
        size_bytes=123,
    )


def test_repository_records_document_and_review_artifact_refs(tmp_path: Path) -> None:
    database_path = tmp_path / "file-organizer.sqlite3"
    _migrate(database_path, tmp_path)

    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        document_id = repository.upsert_document(DOCUMENT)
        run_id = repository.create_run("解析，不归档")
        repository.record_artifact(run_id, document_id, _artifact("document_json"))
        repository.record_artifact(run_id, document_id, _artifact("review_markdown"))
        artifacts = repository.artifacts_for_run(run_id)
        uow.commit()

    assert [item["artifact_kind"] for item in artifacts] == [
        "document_json",
        "review_markdown",
    ]
    assert all("physical_path" not in item for item in artifacts)


def test_record_artifact_is_idempotent_only_for_same_uri_and_hash(tmp_path: Path) -> None:
    database_path = tmp_path / "file-organizer.sqlite3"
    _migrate(database_path, tmp_path)

    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        document_id = repository.upsert_document(DOCUMENT)
        run_id = repository.create_run("解析，不归档")
        first = repository.record_artifact(run_id, document_id, _artifact("document_json"))
        second = repository.record_artifact(run_id, document_id, _artifact("document_json"))
        with pytest.raises(ValueError, match="artifact conflict"):
            repository.record_artifact(
                run_id, document_id, _artifact("document_json", "c" * 64)
            )

    assert first == second
