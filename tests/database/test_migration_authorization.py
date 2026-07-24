import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

import infrastructure.database.sqlite.migration_runner as migration_runner
from infrastructure.database.contracts import BackupError, BackupManifest, MigrationInfo
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite import backup as backup_module
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)
from tests.database.migration_authorization import create_migration_authorization


def _catalog(tmp_path: Path, sql: str) -> tuple[MigrationInfo, ...]:
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_create_authorized_item.sql").write_text(
        sql,
        encoding="utf-8",
    )
    return load_migration_catalog(directory)


def _applied_versions(database: Path) -> list[int]:
    with sqlite3.connect(database) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT version FROM _schema_migrations ORDER BY version"
            )
        ]


def _has_table(database: Path, name: str) -> bool:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone() is not None


def test_hand_constructed_manifest_cannot_authorize_migration(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE authorized_item(id INTEGER PRIMARY KEY);\n",
    )
    initialize_database(database)
    manifest = BackupManifest(
        format_version=1,
        schema_version=0,
        catalog_target_version=1,
        sha256="0" * 64,
        size_bytes=1,
        created_at_utc="2026-07-25T00:00:00Z",
        sqlite_version=sqlite3.sqlite_version,
        integrity_check="ok",
    )

    with pytest.raises(BackupError, match="authorization"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=manifest,
        )

    assert not _has_table(database, "authorized_item")
    assert _applied_versions(database) == []


def test_backup_authorization_for_database_a_cannot_migrate_database_b(
    tmp_path: Path,
) -> None:
    database_a = tmp_path / "a.sqlite3"
    database_b = tmp_path / "b.sqlite3"
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE authorized_item(id INTEGER PRIMARY KEY);\n",
    )
    initialize_database(database_a)
    initialize_database(database_b)
    authorization = create_migration_authorization(
        database_a,
        catalog,
        tmp_path / "a-backup.sqlite3",
    )

    with pytest.raises(BackupError, match="authorization"):
        apply_pending_migrations(
            database_b,
            catalog,
            backup_authorization=authorization,
        )

    assert not _has_table(database_b, "authorized_item")
    assert _applied_versions(database_b) == []


def test_replacing_database_path_after_authorization_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE authorized_item(id INTEGER PRIMARY KEY);\n",
    )
    initialize_database(database)
    authorization = create_migration_authorization(
        database,
        catalog,
        tmp_path / "backup.sqlite3",
    )
    original_identity = (database.stat().st_dev, database.stat().st_ino)
    initialize_database(replacement)
    os.replace(replacement, database)
    assert (database.stat().st_dev, database.stat().st_ino) != original_identity

    with pytest.raises(BackupError, match="authorization"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=authorization,
        )

    assert not _has_table(database, "authorized_item")
    assert _applied_versions(database) == []


def test_changed_backup_artifact_is_rejected_before_migration(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE authorized_item(id INTEGER PRIMARY KEY);\n",
    )
    initialize_database(database)
    result = backup_module.create_sqlite_backup(
        database,
        tmp_path / "backup.sqlite3",
        catalog,
    )
    verified = backup_module.verify_migration_backup(result)
    authorization = backup_module.authorize_migration_backup(
        database,
        verified,
        catalog,
    )
    with result.backup_path.open("ab") as stream:
        stream.write(b"changed after authorization")

    with pytest.raises(BackupError, match="authorization"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=authorization,
        )

    assert not _has_table(database, "authorized_item")
    assert _applied_versions(database) == []


def test_runner_revalidates_authorization_after_write_lock_before_sql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE authorized_item(id INTEGER PRIMARY KEY);\n",
    )
    initialize_database(database)
    authorization = create_migration_authorization(
        database,
        catalog,
        tmp_path / "backup.sqlite3",
    )
    real_open = migration_runner.open_sqlite_connection
    real_revalidate = migration_runner._revalidate_migration_authorization
    writer: dict[str, sqlite3.Connection] = {}
    observations: list[tuple[bool, bool]] = []

    def capture_writer(database_path, **kwargs):
        connection = real_open(database_path, **kwargs)
        if kwargs.get("create") is False:
            writer["connection"] = connection
        return connection

    def observe_revalidation(*args, **kwargs):
        connection = writer.get("connection")
        observations.append(
            (
                bool(connection and connection.in_transaction),
                _has_table(database, "authorized_item"),
            )
        )
        return real_revalidate(*args, **kwargs)

    monkeypatch.setattr(migration_runner, "open_sqlite_connection", capture_writer)
    monkeypatch.setattr(
        migration_runner,
        "_revalidate_migration_authorization",
        observe_revalidation,
    )

    apply_pending_migrations(
        database,
        catalog,
        backup_authorization=authorization,
    )

    assert observations[0] == (False, False)
    assert (True, False) in observations[1:]
    assert _has_table(database, "authorized_item")


def test_authorization_is_bound_to_catalog_identity(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    original_catalog = _catalog(
        tmp_path,
        "CREATE TABLE authorized_item(id INTEGER PRIMARY KEY);\n",
    )
    initialize_database(database)
    authorization = create_migration_authorization(
        database,
        original_catalog,
        tmp_path / "backup.sqlite3",
    )
    alternate_path = tmp_path / "alternate.sql"
    payload = b"CREATE TABLE different_item(id INTEGER PRIMARY KEY);\n"
    alternate_path.write_bytes(payload)
    alternate_catalog = (
        MigrationInfo(
            version=1,
            name="different_item",
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            path=alternate_path,
        ),
    )

    with pytest.raises(BackupError, match="authorization"):
        apply_pending_migrations(
            database,
            alternate_catalog,
            backup_authorization=authorization,
        )

    assert not _has_table(database, "different_item")
    assert _applied_versions(database) == []
