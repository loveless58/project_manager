import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import infrastructure.database.sqlite.migration_runner as migration_runner
from infrastructure.database.contracts import BackupError, MigrationExecutionError
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.backup import (
    authorize_migration_backup,
    create_sqlite_backup,
    load_backup_manifest,
    verify_migration_backup,
)
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)
from infrastructure.database.sqlite.restore import verify_and_restore_sqlite
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork


def _catalog(tmp_path: Path, *migrations: str):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for version, sql in enumerate(migrations, start=1):
        (directory / f"{version:04d}_change_{version}.sql").write_text(
            sql,
            encoding="utf-8",
        )
    return load_migration_catalog(directory)


def _prepare_authorization(database: Path, catalog, backup_path: Path):
    backup = create_sqlite_backup(database, backup_path, catalog)
    verified = verify_migration_backup(backup)
    authorization = authorize_migration_backup(database, verified, catalog)
    return backup, authorization


def _history(database: Path) -> list[int]:
    with sqlite3.connect(database) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT version FROM _schema_migrations ORDER BY version"
            )
        ]


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }


def _business_values(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT value FROM business_data ORDER BY id"
            )
        ]


def _initialize_business_database(database: Path) -> None:
    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE business_data("
            "id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO business_data(value) VALUES ('before-backup')"
        )


def test_generation_witness_rejects_commit_after_backup_before_first_lock(
    tmp_path: Path,
) -> None:
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE first_schema_change(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    _initialize_business_database(database)
    backup, authorization = _prepare_authorization(
        database,
        catalog,
        tmp_path / "initial.sqlite3",
    )

    # A second connection commits without replacing the database inode or
    # changing migration history. The pre-lock backup is now stale.
    with sqlite3.connect(database) as writer:
        writer.execute(
            "INSERT INTO business_data(value) VALUES ('after-backup')"
        )

    with pytest.raises(BackupError, match="backup authorization"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=authorization,
        )

    assert _business_values(database) == ["before-backup", "after-backup"]
    assert "first_schema_change" not in _tables(database)
    assert _history(database) == []
    assert load_backup_manifest(backup.manifest_path).schema_version == 0


def test_managed_writer_waits_for_entire_multi_migration_maintenance_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE first_schema_change(id INTEGER PRIMARY KEY);\n",
        "CREATE TABLE second_schema_change(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    _initialize_business_database(database)
    _, authorization = _prepare_authorization(
        database,
        catalog,
        tmp_path / "initial.sqlite3",
    )
    between_migrations = threading.Event()
    release_gap = threading.Event()
    writer_attempted = threading.Event()
    writer_entered = threading.Event()
    original_apply_one = migration_runner._apply_one_migration

    def pause_after_first(database_path, inspected_catalog, migration, **kwargs):
        applied = original_apply_one(
            database_path,
            inspected_catalog,
            migration,
            **kwargs,
        )
        if migration.version == 1:
            between_migrations.set()
            assert release_gap.wait(timeout=5)
        return applied

    def managed_write() -> None:
        writer_attempted.set()
        with SqliteUnitOfWork(database, mode="write") as uow:
            writer_entered.set()
            uow.connection.execute(
                "INSERT INTO business_data(value) VALUES ('managed-gap-write')"
            )
            uow.commit()

    monkeypatch.setattr(
        migration_runner,
        "_apply_one_migration",
        pause_after_first,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        migration_future = executor.submit(
            apply_pending_migrations,
            database,
            catalog,
            backup_authorization=authorization,
        )
        assert between_migrations.wait(timeout=5)
        writer_future = executor.submit(managed_write)
        assert writer_attempted.wait(timeout=5)
        writer_was_blocked = not writer_entered.wait(timeout=0.25)
        release_gap.set()
        migration_result = migration_future.result(timeout=5)
        writer_future.result(timeout=5)

    assert writer_was_blocked
    assert migration_result.applied_versions == (1, 2)
    assert writer_entered.is_set()
    assert _business_values(database) == [
        "before-backup",
        "managed-gap-write",
    ]


def test_raw_cross_migration_commit_is_in_latest_locked_recovery_point(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE retained_schema(id INTEGER PRIMARY KEY);\n",
        "CREATE TABLE rolled_back_schema(id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table(id) VALUES (1);\n",
    )
    database = tmp_path / "state.sqlite3"
    _initialize_business_database(database)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _, authorization = _prepare_authorization(
        database,
        catalog,
        backup_dir / "initial.sqlite3",
    )
    original_apply_one = migration_runner._apply_one_migration
    injected = False

    def inject_raw_writer(database_path, inspected_catalog, migration, **kwargs):
        nonlocal injected
        applied = original_apply_one(
            database_path,
            inspected_catalog,
            migration,
            **kwargs,
        )
        if applied and migration.version == 1 and not injected:
            injected = True
            # Deliberately unmanaged/raw SQLite: it ignores the cooperative
            # maintenance lock and commits in the per-migration window.
            with sqlite3.connect(database) as writer:
                writer.execute(
                    "INSERT INTO business_data(value) "
                    "VALUES ('raw-cross-migration')"
                )
        return applied

    monkeypatch.setattr(
        migration_runner,
        "_apply_one_migration",
        inject_raw_writer,
    )

    with pytest.raises(MigrationExecutionError, match="migration 2 failed"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=authorization,
        )

    assert injected
    assert _history(database) == [1]
    assert _business_values(database) == [
        "before-backup",
        "raw-cross-migration",
    ]
    recovery_backups = list(
        backup_dir.glob("initial.before-v0002.*.sqlite3")
    )
    assert len(recovery_backups) == 1
    recovery_backup = recovery_backups[0]
    recovery_manifest = Path(f"{recovery_backup}.manifest.json")
    manifest = load_backup_manifest(recovery_manifest)
    assert manifest.schema_version == 1

    restored = tmp_path / "restored.sqlite3"
    verify_and_restore_sqlite(
        recovery_backup,
        recovery_manifest,
        restored,
        active_database_path=database,
        catalog=catalog,
    )
    assert _history(restored) == [1]
    assert "retained_schema" in _tables(restored)
    assert "rolled_back_schema" not in _tables(restored)
    assert _business_values(restored) == [
        "before-backup",
        "raw-cross-migration",
    ]


def test_refresh_failure_stops_before_next_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE retained_schema(id INTEGER PRIMARY KEY);\n",
        "CREATE TABLE forbidden_after_refresh_failure(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    _initialize_business_database(database)
    _, authorization = _prepare_authorization(
        database,
        catalog,
        tmp_path / "initial.sqlite3",
    )
    refresh_calls: list[int] = []

    def fail_refresh(*args, migration_version: int, **kwargs):
        refresh_calls.append(migration_version)
        raise BackupError("migration recovery backup refresh failed")

    monkeypatch.setattr(
        migration_runner,
        "_refresh_migration_backup_under_lock",
        fail_refresh,
        raising=False,
    )

    with pytest.raises(BackupError, match="refresh failed"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=authorization,
        )

    assert refresh_calls == [2]
    assert _history(database) == [1]
    assert "retained_schema" in _tables(database)
    assert "forbidden_after_refresh_failure" not in _tables(database)
