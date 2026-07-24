import sqlite3
from pathlib import Path

import pytest

from infrastructure.database.contracts import (
    AppliedMigration,
    DatabaseIntegrityError,
    MigrationInfo,
    SchemaState,
    SchemaStatus,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.connection import open_sqlite_connection
from infrastructure.database.sqlite.schema import (
    check_database_integrity,
    initialize_schema_metadata,
    inspect_schema,
    read_applied_migrations,
)


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


def _fixture_catalog():
    return load_migration_catalog(FIXTURE_MIGRATIONS)


def _insert_applied(
    database: Path,
    version: int,
    name: str,
    checksum: str,
    *,
    execution_ms: object = 10,
) -> None:
    connection = open_sqlite_connection(database)
    try:
        connection.execute(
            "INSERT INTO _schema_migrations "
            "(version, name, checksum_sha256, applied_at_utc, execution_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, name, checksum, "2026-07-24T00:00:00Z", execution_ms),
        )
    finally:
        connection.close()


def test_initialize_creates_exact_metadata_schema(tmp_path):
    database = tmp_path / "state.sqlite3"

    initialize_schema_metadata(database)

    connection = open_sqlite_connection(database)
    try:
        columns = [tuple(row) for row in connection.execute(
            "PRAGMA table_info(_schema_migrations)"
        )]
    finally:
        connection.close()
    assert columns == [
        (0, "version", "INTEGER", 0, None, 1),
        (1, "name", "TEXT", 1, None, 0),
        (2, "checksum_sha256", "TEXT", 1, None, 0),
        (3, "applied_at_utc", "TEXT", 1, None, 0),
        (4, "execution_ms", "INTEGER", 1, None, 0),
    ]


def test_read_applied_migrations_returns_rows_in_version_order(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    _insert_applied(database, 2, "second", "bbb", execution_ms=12)
    _insert_applied(database, 1, "first", "aaa", execution_ms=7)

    connection = open_sqlite_connection(database)
    try:
        applied = read_applied_migrations(connection)
    finally:
        connection.close()

    assert applied == (
        AppliedMigration(1, "first", "aaa", "2026-07-24T00:00:00Z", 7),
        AppliedMigration(2, "second", "bbb", "2026-07-24T00:00:00Z", 12),
    )


def test_read_applied_migrations_supports_plain_sqlite_connection(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO _schema_migrations "
        "(version, name, checksum_sha256, applied_at_utc, execution_ms) "
        "VALUES (?, ?, ?, ?, ?)",
        (1, "first", "aaa", "2026-07-24T00:00:00Z", 7),
    )
    connection.commit()
    try:
        applied = read_applied_migrations(connection)
    finally:
        connection.close()

    assert applied == (
        AppliedMigration(1, "first", "aaa", "2026-07-24T00:00:00Z", 7),
    )


def test_missing_database_is_uninitialized_without_creating_file(tmp_path):
    database = tmp_path / "missing.sqlite3"
    catalog = _fixture_catalog()

    status = inspect_schema(database, catalog)

    assert status == SchemaStatus(
        state=SchemaState.UNINITIALIZED,
        current_version=0,
        target_version=2,
        pending_versions=(1, 2),
        error_code=None,
    )
    assert not database.exists()


def test_database_without_metadata_is_uninitialized(tmp_path):
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.close()

    assert inspect_schema(database, _fixture_catalog()) == SchemaStatus(
        state=SchemaState.UNINITIALIZED,
        current_version=0,
        target_version=2,
        pending_versions=(1, 2),
        error_code=None,
    )


def test_initialized_empty_catalog_is_current(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)

    status = inspect_schema(database, ())

    assert status == SchemaStatus(
        state=SchemaState.CURRENT,
        current_version=0,
        target_version=0,
        pending_versions=(),
        error_code=None,
    )


def test_initialized_database_with_fixture_catalog_is_pending(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)

    assert inspect_schema(database, _fixture_catalog()) == SchemaStatus(
        state=SchemaState.PENDING,
        current_version=0,
        target_version=2,
        pending_versions=(1, 2),
        error_code=None,
    )


def test_matching_applied_prefix_reports_only_remaining_versions(tmp_path):
    database = tmp_path / "state.sqlite3"
    catalog = _fixture_catalog()
    initialize_schema_metadata(database)
    first = catalog[0]
    _insert_applied(database, first.version, first.name, first.checksum_sha256)

    assert inspect_schema(database, catalog) == SchemaStatus(
        state=SchemaState.PENDING,
        current_version=1,
        target_version=2,
        pending_versions=(2,),
        error_code=None,
    )


@pytest.mark.parametrize("tampered_field", ["name", "checksum"])
def test_applied_catalog_identity_tampering_is_reported(tmp_path, tampered_field):
    database = tmp_path / "state.sqlite3"
    catalog = _fixture_catalog()
    initialize_schema_metadata(database)
    first = catalog[0]
    name = "renamed" if tampered_field == "name" else first.name
    checksum = "0" * 64 if tampered_field == "checksum" else first.checksum_sha256
    _insert_applied(database, first.version, name, checksum)

    assert inspect_schema(database, catalog) == SchemaStatus(
        state=SchemaState.TAMPERED,
        current_version=1,
        target_version=2,
        pending_versions=(),
        error_code="DB.MIGRATION_CHECKSUM",
    )


def test_applied_version_above_catalog_target_is_too_new(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    _insert_applied(database, 3, "future", "future-checksum")

    assert inspect_schema(database, _fixture_catalog()) == SchemaStatus(
        state=SchemaState.TOO_NEW,
        current_version=3,
        target_version=2,
        pending_versions=(),
        error_code="DB.SCHEMA_TOO_NEW",
    )


def test_gapped_metadata_is_corrupt(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    _insert_applied(database, 1, "one", "aaa")
    _insert_applied(database, 3, "three", "ccc")
    catalog = tuple(
        MigrationInfo(version, name, checksum, Path(f"{version:04d}_{name}.sql"))
        for version, name, checksum in (
            (1, "one", "aaa"),
            (2, "two", "bbb"),
            (3, "three", "ccc"),
        )
    )

    assert inspect_schema(database, catalog) == SchemaStatus(
        state=SchemaState.CORRUPT,
        current_version=3,
        target_version=3,
        pending_versions=(),
        error_code="DB.INTEGRITY",
    )


def test_malformed_metadata_columns_are_corrupt(tmp_path):
    database = tmp_path / "state.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE _schema_migrations (version INTEGER PRIMARY KEY)")
    connection.close()

    status = inspect_schema(database, ())

    assert status.state is SchemaState.CORRUPT
    assert status.error_code == "DB.INTEGRITY"


def test_malformed_metadata_value_types_are_corrupt(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    _insert_applied(database, 1, "one", "aaa", execution_ms="not-an-integer")

    status = inspect_schema(
        database,
        (MigrationInfo(1, "one", "aaa", Path("0001_one.sql")),),
    )

    assert status.state is SchemaState.CORRUPT
    assert status.error_code == "DB.INTEGRITY"


def test_corrupt_database_bytes_are_reported_without_leaking_details(tmp_path):
    database = tmp_path / "state.sqlite3"
    database.write_bytes(b"this is not a sqlite database")

    status = inspect_schema(database, ())

    assert status == SchemaStatus(
        state=SchemaState.CORRUPT,
        current_version=0,
        target_version=0,
        pending_versions=(),
        error_code="DB.INTEGRITY",
    )


def test_integrity_check_accepts_valid_database(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    connection = open_sqlite_connection(database)
    try:
        check_database_integrity(connection)
    finally:
        connection.close()


class _FailingIntegrityConnection:
    def execute(self, sql):
        raise sqlite3.DatabaseError("database disk image is malformed")


def test_integrity_check_maps_corrupt_sqlite_error_to_safe_exception():
    with pytest.raises(DatabaseIntegrityError) as raised:
        check_database_integrity(_FailingIntegrityConnection())  # type: ignore[arg-type]

    assert str(raised.value) == "database integrity check failed"
