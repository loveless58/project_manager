import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from infrastructure.database.contracts import (
    BackupError,
    BackupManifest,
    DatabaseBusyError,
    MigrationChecksumError,
    MigrationExecutionError,
    SchemaState,
    SchemaStatus,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.connection import (
    SqliteConnectionOptions,
    open_sqlite_connection,
)
from infrastructure.database.sqlite.migration_runner import (
    MigrationRunResult,
    apply_pending_migrations,
    initialize_database,
)


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


@pytest.fixture
def fixture_catalog():
    return load_migration_catalog(FIXTURE_MIGRATIONS)


def _authorization(
    *, schema_version: int = 0, catalog_target_version: int = 2
) -> BackupManifest:
    return BackupManifest(
        format_version=1,
        schema_version=schema_version,
        catalog_target_version=catalog_target_version,
        sha256="0" * 64,
        size_bytes=1,
        created_at_utc="2026-07-24T00:00:00Z",
        sqlite_version=sqlite3.sqlite_version,
        integrity_check="ok",
    )


def _catalog(tmp_path: Path, *migrations: str):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for version, sql in enumerate(migrations, start=1):
        (directory / f"{version:04d}_migration_{version}.sql").write_text(
            sql,
            encoding="utf-8",
        )
    return load_migration_catalog(directory)


def _table_names(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        ]


def _applied_versions(database: Path) -> list[int]:
    with sqlite3.connect(database) as connection:
        return [
            row[0]
            for row in connection.execute(
                "SELECT version FROM _schema_migrations ORDER BY version"
            )
        ]


def test_initialize_database_is_idempotent(tmp_path):
    database = tmp_path / "state.sqlite3"
    expected = SchemaStatus(
        state=SchemaState.CURRENT,
        current_version=0,
        target_version=0,
        pending_versions=(),
        error_code=None,
    )

    assert initialize_database(database) == expected
    assert initialize_database(database) == expected
    assert _table_names(database) == ["_schema_migrations"]


def test_applies_fixture_migrations_in_order(tmp_path, fixture_catalog):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)

    result = apply_pending_migrations(
        database,
        fixture_catalog,
        backup_manifest=_authorization(),
    )

    assert result == MigrationRunResult(
        previous_version=0,
        current_version=2,
        applied_versions=(1, 2),
    )
    with sqlite3.connect(database) as connection:
        records = connection.execute(
            "SELECT version, name, checksum_sha256, applied_at_utc, execution_ms "
            "FROM _schema_migrations ORDER BY version"
        ).fetchall()
    assert [(row[0], row[1]) for row in records] == [
        (1, "create_fixture_parent"),
        (2, "create_fixture_entity"),
    ]
    assert [row[2] for row in records] == [item.checksum_sha256 for item in fixture_catalog]
    assert all(row[3].endswith("Z") and len(row[3]) == 20 for row in records)
    assert all(type(row[4]) is int and row[4] >= 0 for row in records)
    assert {"fixture_parent", "fixture_entity"}.issubset(_table_names(database))


def test_current_database_is_repeated_no_op_without_backup(tmp_path, fixture_catalog):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    apply_pending_migrations(
        database,
        fixture_catalog,
        backup_manifest=_authorization(),
    )

    assert apply_pending_migrations(
        database,
        fixture_catalog,
        backup_manifest=None,
    ) == MigrationRunResult(
        previous_version=2,
        current_version=2,
        applied_versions=(),
    )
    assert _applied_versions(database) == [1, 2]


def test_pending_migration_requires_backup_authorization(tmp_path, fixture_catalog):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)

    with pytest.raises(BackupError, match="backup authorization"):
        apply_pending_migrations(database, fixture_catalog, backup_manifest=None)

    assert _applied_versions(database) == []


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        (_authorization(schema_version=1), "schema version"),
        (_authorization(catalog_target_version=1), "target version"),
        (replace(_authorization(), integrity_check="failed"), "integrity"),
        (replace(_authorization(), sha256="A" * 64), "SHA-256"),
        (replace(_authorization(), sha256="0" * 63), "SHA-256"),
        (replace(_authorization(), sha256=("0" * 63) + "g"), "SHA-256"),
    ],
)
def test_rejects_invalid_backup_authorization(
    tmp_path, fixture_catalog, manifest, message
):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)

    with pytest.raises(BackupError, match=message):
        apply_pending_migrations(
            database,
            fixture_catalog,
            backup_manifest=manifest,
        )

    assert _applied_versions(database) == []


def test_sql_failure_rolls_back_current_schema_and_record(tmp_path):
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE transient_item(id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table(id) VALUES (1);\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)

    with pytest.raises(MigrationExecutionError, match="migration 1 failed"):
        apply_pending_migrations(
            database,
            catalog,
            backup_manifest=_authorization(catalog_target_version=1),
        )

    assert "transient_item" not in _table_names(database)
    assert _applied_versions(database) == []


def test_metadata_failure_rolls_back_schema_and_record(tmp_path, fixture_catalog):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    authorization = _authorization()

    def fail_before_record(connection, migration):
        raise RuntimeError("injected metadata failure")

    with pytest.raises(MigrationExecutionError, match="migration 1 failed"):
        apply_pending_migrations(
            database,
            fixture_catalog,
            backup_manifest=authorization,
            before_record_insert=fail_before_record,
        )

    assert "fixture_parent" not in _table_names(database)
    assert _applied_versions(database) == []


def test_later_sql_failure_preserves_earlier_committed_migration(tmp_path):
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE retained_item(id INTEGER PRIMARY KEY);\n",
        "CREATE TABLE rolled_back_item(id INTEGER PRIMARY KEY);\n"
        "INSERT INTO missing_table(id) VALUES (1);\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)

    with pytest.raises(MigrationExecutionError, match="migration 2 failed"):
        apply_pending_migrations(
            database,
            catalog,
            backup_manifest=_authorization(),
        )

    assert "retained_item" in _table_names(database)
    assert "rolled_back_item" not in _table_names(database)
    assert _applied_versions(database) == [1]


def test_trigger_body_executes_without_splitting_migration_sql(tmp_path):
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE trigger_item(id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE trigger_audit(item_id INTEGER NOT NULL);\n"
        "CREATE TRIGGER audit_item AFTER INSERT ON trigger_item BEGIN\n"
        "  INSERT INTO trigger_audit(item_id) VALUES (NEW.id);\n"
        "END;\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    apply_pending_migrations(
        database,
        catalog,
        backup_manifest=_authorization(catalog_target_version=1),
    )

    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO trigger_item(id) VALUES (42)")
        audit_rows = connection.execute(
            "SELECT item_id FROM trigger_audit"
        ).fetchall()
    assert audit_rows == [(42,)]


def test_checksum_tampering_is_rejected_before_no_op(tmp_path):
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    migration = migration_dir / "0001_create_item.sql"
    migration.write_text(
        "CREATE TABLE checksum_item(id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    original_catalog = load_migration_catalog(migration_dir)
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    apply_pending_migrations(
        database,
        original_catalog,
        backup_manifest=_authorization(catalog_target_version=1),
    )
    migration.write_text(
        "CREATE TABLE checksum_item(id INTEGER PRIMARY KEY, value TEXT);\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationChecksumError, match="checksum"):
        apply_pending_migrations(
            database,
            load_migration_catalog(migration_dir),
            backup_manifest=None,
        )

    assert _applied_versions(database) == [1]


def test_competing_runner_rechecks_state_after_write_lock(tmp_path):
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE concurrent_item(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    authorization = _authorization(catalog_target_version=1)
    first_has_lock = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def hold_first_lock(connection, migration):
        first_has_lock.set()
        assert release_first.wait(timeout=5)

    def run_second():
        second_started.set()
        return apply_pending_migrations(
            database,
            catalog,
            backup_manifest=authorization,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            apply_pending_migrations,
            database,
            catalog,
            backup_manifest=authorization,
            before_record_insert=hold_first_lock,
        )
        assert first_has_lock.wait(timeout=5)
        second = executor.submit(run_second)
        assert second_started.wait(timeout=5)
        release_first.set()
        results = (first.result(timeout=5), second.result(timeout=5))

    assert sorted(result.applied_versions for result in results) == [(), (1,)]
    applied_result = next(result for result in results if result.applied_versions)
    no_op_result = next(result for result in results if not result.applied_versions)
    assert applied_result.previous_version == 0
    assert no_op_result.previous_version in (0, 1)
    assert all(result.current_version == 1 for result in results)
    assert _applied_versions(database) == [1]


def test_busy_timeout_maps_competing_writer_to_database_busy(tmp_path):
    catalog = _catalog(
        tmp_path,
        "CREATE TABLE busy_item(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    locking_connection = open_sqlite_connection(database)
    locking_connection.execute("BEGIN IMMEDIATE")

    try:
        with pytest.raises(DatabaseBusyError, match="database is busy"):
            apply_pending_migrations(
                database,
                catalog,
                backup_manifest=_authorization(catalog_target_version=1),
                options=SqliteConnectionOptions(busy_timeout_ms=1),
            )
    finally:
        locking_connection.rollback()
        locking_connection.close()

    assert _applied_versions(database) == []
