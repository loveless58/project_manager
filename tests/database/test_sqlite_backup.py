import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import infrastructure.database.sqlite.backup as backup_module
from infrastructure.database.contracts import (
    BackupError,
    MigrationInfo,
    SchemaState,
    SchemaStatus,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.backup import (
    create_sqlite_backup,
    load_backup_manifest,
    verify_backup_artifacts,
)
from infrastructure.database.sqlite.schema import initialize_schema_metadata


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


@pytest.fixture
def catalog() -> tuple[MigrationInfo, ...]:
    return load_migration_catalog(FIXTURE_MIGRATIONS)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "source.sqlite3"
    initialize_schema_metadata(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE snapshot_value (value TEXT NOT NULL)")
        connection.execute("INSERT INTO snapshot_value VALUES ('committed')")
    return path


@pytest.fixture
def backup_result(database: Path, tmp_path: Path):
    return create_sqlite_backup(database, tmp_path / "backup.sqlite3", ())


def _manifest_path(backup_path: Path) -> Path:
    return Path(f"{backup_path}.manifest.json")


def _insert_applied(
    database: Path,
    version: int,
    name: str,
    checksum: str,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO _schema_migrations "
            "(version, name, checksum_sha256, applied_at_utc, execution_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, name, checksum, "2026-07-24T00:00:00Z", 1),
        )


def _assert_no_backup_artifacts(output_path: Path) -> None:
    assert not output_path.exists()
    assert not _manifest_path(output_path).exists()
    assert list(output_path.parent.iterdir()) == []


def test_backup_uses_wal_consistent_sqlite_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite3"
    initialize_schema_metadata(database)
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE wal_value (value TEXT NOT NULL)")
        writer.execute("INSERT INTO wal_value VALUES ('in-wal')")
        writer.commit()
        assert Path(f"{database}-wal").is_file()

        result = create_sqlite_backup(database, tmp_path / "backup.sqlite3", ())
    finally:
        writer.close()

    with sqlite3.connect(result.backup_path) as connection:
        assert connection.execute("SELECT value FROM wal_value").fetchone()[0] == "in-wal"


def test_missing_source_is_refused_without_creating_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(tmp_path / "missing.sqlite3", output, ())

    _assert_no_backup_artifacts(output)


def test_pending_schema_is_allowed(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    result = create_sqlite_backup(
        database,
        tmp_path / "pending-backup.sqlite3",
        catalog,
    )

    assert result.manifest.schema_version == 0
    assert result.manifest.catalog_target_version == 2


def test_too_new_schema_is_refused(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    _insert_applied(database, 3, "future", "future-checksum")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, catalog)

    _assert_no_backup_artifacts(output)


def test_tampered_schema_is_refused(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    first = catalog[0]
    _insert_applied(database, first.version, first.name, "0" * 64)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, catalog)

    _assert_no_backup_artifacts(output)


def test_invalid_catalog_status_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        backup_module,
        "inspect_schema",
        lambda *args, **kwargs: SchemaStatus(
            state=SchemaState.INVALID_CATALOG,
            current_version=0,
            target_version=0,
            error_code="DB.MIGRATION_CATALOG",
        ),
    )
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_corrupt_database_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a sqlite database")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_foreign_key_failure_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite3"
    initialize_schema_metadata(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL,"
            "FOREIGN KEY (parent_id) REFERENCES parent(id));"
            "INSERT INTO child VALUES (1, 999);"
        )
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_preexisting_backup_is_never_overwritten(
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    output.write_bytes(b"existing backup")

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert output.read_bytes() == b"existing backup"
    assert not _manifest_path(output).exists()
    assert [path.name for path in output_dir.iterdir()] == ["backup.sqlite3"]


def test_preexisting_manifest_is_preserved_and_new_backup_is_compensated(
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    manifest_path = _manifest_path(output)
    manifest_path.write_text("existing manifest", encoding="utf-8")

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert not output.exists()
    assert manifest_path.read_text(encoding="utf-8") == "existing manifest"
    assert [path.name for path in output_dir.iterdir()] == [
        "backup.sqlite3.manifest.json",
    ]


def test_backup_manifest_fields_hash_size_and_verification(
    backup_result,
) -> None:
    backup_payload = backup_result.backup_path.read_bytes()
    manifest = backup_result.manifest

    assert manifest.format_version == 1
    assert manifest.schema_version == 0
    assert manifest.catalog_target_version == 0
    assert manifest.sha256 == hashlib.sha256(backup_payload).hexdigest()
    assert manifest.size_bytes == len(backup_payload)
    assert manifest.created_at_utc.endswith("Z")
    assert manifest.sqlite_version == sqlite3.sqlite_version
    assert manifest.integrity_check == "ok"
    assert load_backup_manifest(backup_result.manifest_path) == manifest
    assert verify_backup_artifacts(
        backup_result.backup_path,
        backup_result.manifest_path,
    ) == manifest
    assert backup_result.manifest_path.read_bytes().endswith(b"\n")


def test_backup_manifest_contains_no_physical_source_identity(
    backup_result,
    database: Path,
) -> None:
    payload = backup_result.manifest_path.read_text(encoding="utf-8")
    assert str(database) not in payload
    assert set(json.loads(payload)) == {
        "format_version",
        "schema_version",
        "catalog_target_version",
        "sha256",
        "size_bytes",
        "created_at_utc",
        "sqlite_version",
        "integrity_check",
    }


def test_verify_backup_artifacts_rejects_changed_backup(backup_result) -> None:
    with backup_result.backup_path.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(BackupError):
        verify_backup_artifacts(
            backup_result.backup_path,
            backup_result.manifest_path,
        )


def test_load_backup_manifest_rejects_invalid_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "invalid.manifest.json"
    manifest_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(BackupError):
        load_backup_manifest(manifest_path)


def test_injected_second_publication_failure_cleans_owned_files(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_link = os.link
    calls = 0

    def fail_manifest_link(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication failure")
        real_link(source, destination)

    monkeypatch.setattr(backup_module.os, "link", fail_manifest_link)

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(database) not in str(raised.value)
    assert calls == 2
    _assert_no_backup_artifacts(output)
