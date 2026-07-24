import hashlib
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ..contracts import (
    BackupError,
    BackupManifest,
    DatabaseIntegrityError,
    MigrationCatalogError,
    MigrationChecksumError,
    MigrationExecutionError,
    MigrationInfo,
    SchemaState,
    SchemaStatus,
    SchemaTooNewError,
)
from ..migration_catalog import catalog_target_version
from .connection import (
    SqliteConnectionOptions,
    _raise_mapped_sqlite_error,
    open_sqlite_connection,
)
from .schema import (
    initialize_schema_metadata,
    inspect_schema,
    read_applied_migrations,
)


_INSERT_APPLIED_MIGRATION_SQL = (
    "INSERT INTO _schema_migrations "
    "(version, name, checksum_sha256, applied_at_utc, execution_ms) "
    "VALUES (?, ?, ?, ?, ?)"
)
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class MigrationRunResult:
    previous_version: int
    current_version: int
    applied_versions: tuple[int, ...]


def initialize_database(
    database_path: Path,
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> SchemaStatus:
    initialize_schema_metadata(database_path, options=options)
    return inspect_schema(database_path, (), options=options)


def apply_pending_migrations(
    database_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    backup_manifest: BackupManifest | None,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
    before_record_insert: (
        Callable[[sqlite3.Connection, MigrationInfo], None] | None
    ) = None,
) -> MigrationRunResult:
    initial_status = inspect_schema(database_path, catalog, options=options)
    _raise_for_unusable_status(initial_status)
    previous_version = initial_status.current_version

    if initial_status.state is SchemaState.CURRENT:
        return MigrationRunResult(
            previous_version=previous_version,
            current_version=previous_version,
            applied_versions=(),
        )

    target_version = catalog_target_version(catalog)
    _validate_backup_authorization(
        backup_manifest,
        current_version=previous_version,
        target_version=target_version,
    )

    applied_versions: list[int] = []
    for migration in catalog:
        if migration.version <= previous_version:
            continue
        applied = _apply_one_migration(
            database_path,
            catalog,
            migration,
            options=options,
            before_record_insert=before_record_insert,
        )
        if applied:
            applied_versions.append(migration.version)

    final_status = inspect_schema(database_path, catalog, options=options)
    _raise_for_unusable_status(final_status)
    if final_status.state is not SchemaState.CURRENT:
        raise DatabaseIntegrityError("database migration state is incomplete")

    return MigrationRunResult(
        previous_version=previous_version,
        current_version=final_status.current_version,
        applied_versions=tuple(applied_versions),
    )


def _apply_one_migration(
    database_path: Path,
    catalog: Sequence[MigrationInfo],
    migration: MigrationInfo,
    *,
    options: SqliteConnectionOptions,
    before_record_insert: (
        Callable[[sqlite3.Connection, MigrationInfo], None] | None
    ),
) -> bool:
    migration_sql = _read_verified_migration_sql(migration)
    connection = open_sqlite_connection(
        database_path,
        options=options,
        create=False,
    )
    try:
        if connection.in_transaction:
            raise MigrationExecutionError(
                f"migration {migration.version} failed"
            )

        started = perf_counter()
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + migration_sql)
            execution_ms = max(0, round((perf_counter() - started) * 1000))

            locked_version = _validated_current_version(connection, catalog)
            if locked_version >= migration.version:
                connection.rollback()
                return False
            if locked_version != migration.version - 1:
                raise DatabaseIntegrityError("database migration history is invalid")

            if before_record_insert is not None:
                before_record_insert(connection, migration)

            connection.execute(
                _INSERT_APPLIED_MIGRATION_SQL,
                (
                    migration.version,
                    migration.name,
                    migration.checksum_sha256,
                    _utc_timestamp(),
                    execution_ms,
                ),
            )
            connection.commit()
            return True
        except Exception as error:
            if connection.in_transaction:
                connection.rollback()

            if isinstance(error, sqlite3.DatabaseError):
                current_version = _validated_current_version(connection, catalog)
                if current_version >= migration.version:
                    return False
                try:
                    _raise_mapped_sqlite_error(error)
                except sqlite3.DatabaseError:
                    pass

            if isinstance(
                error,
                (
                    DatabaseIntegrityError,
                    MigrationCatalogError,
                    MigrationChecksumError,
                    SchemaTooNewError,
                ),
            ):
                raise
            raise MigrationExecutionError(
                f"migration {migration.version} failed"
            ) from error
    finally:
        connection.close()


def _read_verified_migration_sql(migration: MigrationInfo) -> str:
    try:
        payload = migration.path.read_bytes()
    except OSError as error:
        raise MigrationExecutionError(
            f"migration {migration.version} failed"
        ) from error

    if hashlib.sha256(payload).hexdigest() != migration.checksum_sha256:
        raise MigrationChecksumError(
            f"migration {migration.version} checksum mismatch"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MigrationChecksumError(
            f"migration {migration.version} checksum mismatch"
        ) from error


def _validated_current_version(
    connection: sqlite3.Connection,
    catalog: Sequence[MigrationInfo],
) -> int:
    applied = read_applied_migrations(connection)
    if tuple(item.version for item in applied) != tuple(range(1, len(applied) + 1)):
        raise DatabaseIntegrityError("database migration history is invalid")

    catalog_by_version = {item.version: item for item in catalog}
    for record in applied:
        expected = catalog_by_version.get(record.version)
        if expected is None:
            raise SchemaTooNewError("database schema is newer than migration catalog")
        if (
            record.name != expected.name
            or record.checksum_sha256 != expected.checksum_sha256
        ):
            raise MigrationChecksumError("migration checksum mismatch")

    return applied[-1].version if applied else 0


def _raise_for_unusable_status(status: SchemaStatus) -> None:
    if status.state in (SchemaState.CURRENT, SchemaState.PENDING):
        return
    if status.state is SchemaState.TAMPERED:
        raise MigrationChecksumError("migration checksum mismatch")
    if status.state is SchemaState.TOO_NEW:
        raise SchemaTooNewError("database schema is newer than migration catalog")
    if status.state is SchemaState.INVALID_CATALOG:
        raise MigrationCatalogError("migration catalog is invalid")
    if status.state is SchemaState.UNINITIALIZED:
        raise DatabaseIntegrityError("database schema metadata is not initialized")
    raise DatabaseIntegrityError("database integrity check failed")


def _validate_backup_authorization(
    backup_manifest: BackupManifest | None,
    *,
    current_version: int,
    target_version: int,
) -> None:
    if backup_manifest is None:
        raise BackupError("backup authorization is required")
    if backup_manifest.schema_version != current_version:
        raise BackupError("backup schema version does not match database")
    if backup_manifest.catalog_target_version != target_version:
        raise BackupError("backup target version does not match migration catalog")
    if backup_manifest.integrity_check != "ok":
        raise BackupError("backup integrity check is not ok")
    if _LOWERCASE_SHA256.fullmatch(backup_manifest.sha256) is None:
        raise BackupError("backup SHA-256 is invalid")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "MigrationRunResult",
    "apply_pending_migrations",
    "initialize_database",
]
