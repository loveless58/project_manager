import sqlite3
from collections.abc import Sequence
from pathlib import Path

from ..contracts import (
    AppliedMigration,
    DatabaseBusyError,
    DatabaseIntegrityError,
    MigrationInfo,
    SchemaState,
    SchemaStatus,
)
from ..migration_catalog import catalog_target_version
from .connection import (
    SqliteConnectionOptions,
    _raise_mapped_sqlite_error,
    open_sqlite_connection,
)


_METADATA_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    execution_ms INTEGER NOT NULL
)"""

_METADATA_COLUMNS = (
    ("version", "INTEGER", 0, 1),
    ("name", "TEXT", 1, 0),
    ("checksum_sha256", "TEXT", 1, 0),
    ("applied_at_utc", "TEXT", 1, 0),
    ("execution_ms", "INTEGER", 1, 0),
)


def initialize_schema_metadata(
    database_path: Path,
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> None:
    connection = open_sqlite_connection(database_path, options=options, create=True)
    try:
        connection.execute(_METADATA_TABLE_SQL)
    except sqlite3.DatabaseError as error:
        _raise_mapped_sqlite_error(error)
    finally:
        connection.close()


def read_applied_migrations(
    connection: sqlite3.Connection,
) -> tuple[AppliedMigration, ...]:
    try:
        rows = connection.execute(
            "SELECT version, name, checksum_sha256, applied_at_utc, execution_ms "
            "FROM _schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.DatabaseError as error:
        _raise_mapped_sqlite_error(error)

    return tuple(
        AppliedMigration(*row)
        for row in rows
    )


def _inspect_schema_connection(
    connection: sqlite3.Connection,
    catalog: Sequence[MigrationInfo],
) -> SchemaStatus:
    target_version = catalog_target_version(catalog)
    all_catalog_versions = tuple(item.version for item in catalog)
    check_database_integrity(connection)
    if not _metadata_table_exists(connection):
        return SchemaStatus(
            state=SchemaState.UNINITIALIZED,
            current_version=0,
            target_version=target_version,
            pending_versions=all_catalog_versions,
            error_code=None,
        )
    if not _metadata_columns_are_valid(connection):
        return _corrupt_status(0, target_version)

    applied = read_applied_migrations(connection)
    return _status_from_applied_migrations(applied, catalog)


def inspect_schema(
    database_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> SchemaStatus:
    target_version = catalog_target_version(catalog)
    all_catalog_versions = tuple(item.version for item in catalog)
    if not database_path.is_file():
        return SchemaStatus(
            state=SchemaState.UNINITIALIZED,
            current_version=0,
            target_version=target_version,
            pending_versions=all_catalog_versions,
            error_code=None,
        )

    try:
        connection = open_sqlite_connection(
            database_path,
            options=options,
            create=False,
        )
        try:
            return _inspect_schema_connection(connection, catalog)
        finally:
            connection.close()
    except DatabaseBusyError:
        raise
    except DatabaseIntegrityError:
        return _corrupt_status(0, target_version)


def _status_from_applied_migrations(
    applied: tuple[AppliedMigration, ...],
    catalog: Sequence[MigrationInfo],
) -> SchemaStatus:
    target_version = catalog_target_version(catalog)
    if not _applied_values_are_valid(applied):
        current_version = _highest_integer_version(applied)
        return _corrupt_status(current_version, target_version)

    current_version = applied[-1].version if applied else 0
    if current_version > target_version:
        return SchemaStatus(
            state=SchemaState.TOO_NEW,
            current_version=current_version,
            target_version=target_version,
            pending_versions=(),
            error_code="DB.SCHEMA_TOO_NEW",
        )

    if tuple(item.version for item in applied) != tuple(
        range(1, current_version + 1)
    ):
        return _corrupt_status(current_version, target_version)

    catalog_by_version = {item.version: item for item in catalog}
    for item in applied:
        expected = catalog_by_version.get(item.version)
        if (
            expected is None
            or item.name != expected.name
            or item.checksum_sha256 != expected.checksum_sha256
        ):
            return SchemaStatus(
                state=SchemaState.TAMPERED,
                current_version=current_version,
                target_version=target_version,
                pending_versions=(),
                error_code="DB.MIGRATION_CHECKSUM",
            )

    pending_versions = tuple(
        item.version for item in catalog if item.version > current_version
    )
    return SchemaStatus(
        state=SchemaState.PENDING if pending_versions else SchemaState.CURRENT,
        current_version=current_version,
        target_version=target_version,
        pending_versions=pending_versions,
        error_code=None,
    )


def check_database_integrity(connection: sqlite3.Connection) -> None:
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as error:
        error_code = getattr(error, "sqlite_errorcode", None)
        primary_code = error_code & 0xFF if isinstance(error_code, int) else None
        if primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise DatabaseBusyError("database is busy") from None
        raise DatabaseIntegrityError("database integrity check failed") from None

    if row is None or row[0] != "ok":
        raise DatabaseIntegrityError("database integrity check failed")


def _metadata_table_exists(connection: sqlite3.Connection) -> bool:
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
            ("_schema_migrations",),
        ).fetchone()
    except sqlite3.DatabaseError as error:
        _raise_mapped_sqlite_error(error)
    return row is not None


def _metadata_columns_are_valid(connection: sqlite3.Connection) -> bool:
    try:
        rows = connection.execute("PRAGMA table_info(_schema_migrations)").fetchall()
    except sqlite3.DatabaseError as error:
        _raise_mapped_sqlite_error(error)
    actual = tuple((row[1], row[2], row[3], row[5]) for row in rows)
    return actual == _METADATA_COLUMNS


def _applied_values_are_valid(applied: tuple[AppliedMigration, ...]) -> bool:
    return all(
        type(item.version) is int
        and item.version > 0
        and type(item.name) is str
        and type(item.checksum_sha256) is str
        and type(item.applied_at_utc) is str
        and type(item.execution_ms) is int
        and item.execution_ms >= 0
        for item in applied
    )


def _highest_integer_version(applied: tuple[AppliedMigration, ...]) -> int:
    versions = [item.version for item in applied if type(item.version) is int]
    return max(versions, default=0)


def _corrupt_status(current_version: int, target_version: int) -> SchemaStatus:
    return SchemaStatus(
        state=SchemaState.CORRUPT,
        current_version=current_version,
        target_version=target_version,
        pending_versions=(),
        error_code="DB.INTEGRITY",
    )


__all__ = [
    "check_database_integrity",
    "initialize_schema_metadata",
    "inspect_schema",
    "read_applied_migrations",
]
