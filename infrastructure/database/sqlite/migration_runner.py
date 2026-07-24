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
    _inspect_schema_connection,
    initialize_schema_metadata,
    inspect_schema,
)


_INSERT_APPLIED_MIGRATION_SQL = (
    "INSERT INTO _schema_migrations "
    "(version, name, checksum_sha256, applied_at_utc, execution_ms) "
    "VALUES (?, ?, ?, ?, ?)"
)
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")


class _MigrationTransactionAuthorizer:
    def __init__(self) -> None:
        self._runner_begin_seen = False

    def __call__(
        self,
        action_code: int,
        argument_1: str | None,
        argument_2: str | None,
        database_name: str | None,
        trigger_name: str | None,
    ) -> int:
        if action_code == sqlite3.SQLITE_TRANSACTION:
            if argument_1 == "BEGIN" and not self._runner_begin_seen:
                self._runner_begin_seen = True
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action_code == sqlite3.SQLITE_SAVEPOINT:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


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
    _verify_catalog_payloads(catalog)
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
            authorizer = _MigrationTransactionAuthorizer()
            connection.set_authorizer(authorizer)
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + migration_sql)
            finally:
                connection.set_authorizer(None)
            execution_ms = max(0, round((perf_counter() - started) * 1000))

            locked_status = _inspect_schema_connection(connection, catalog)
            _raise_for_unusable_status(locked_status)
            if locked_status.current_version >= migration.version:
                connection.rollback()
                return False
            if (
                locked_status.state is not SchemaState.PENDING
                or locked_status.current_version != migration.version - 1
            ):
                raise DatabaseIntegrityError(
                    "database migration history is invalid"
                ) from None

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
                failure_status = _inspect_schema_connection(connection, catalog)
                _raise_for_unusable_status(failure_status)
                if failure_status.current_version >= migration.version:
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
                raise error from None
            raise MigrationExecutionError(
                f"migration {migration.version} failed"
            ) from None
    finally:
        connection.close()


def _read_verified_migration_sql(migration: MigrationInfo) -> str:
    try:
        payload = migration.path.read_bytes()
    except OSError:
        raise MigrationExecutionError(
            f"migration {migration.version} failed"
        ) from None

    if hashlib.sha256(payload).hexdigest() != migration.checksum_sha256:
        raise MigrationChecksumError(
            f"migration {migration.version} checksum mismatch"
        )
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        raise MigrationChecksumError(
            f"migration {migration.version} checksum mismatch"
        ) from None


def _verify_catalog_payloads(catalog: Sequence[MigrationInfo]) -> None:
    for migration in catalog:
        _read_verified_migration_sql(migration)


def _raise_for_unusable_status(status: SchemaStatus) -> None:
    if status.state in (SchemaState.CURRENT, SchemaState.PENDING):
        return
    if status.state is SchemaState.TAMPERED:
        raise MigrationChecksumError("migration checksum mismatch") from None
    if status.state is SchemaState.TOO_NEW:
        raise SchemaTooNewError(
            "database schema is newer than migration catalog"
        ) from None
    if status.state is SchemaState.INVALID_CATALOG:
        raise MigrationCatalogError("migration catalog is invalid") from None
    if status.state is SchemaState.UNINITIALIZED:
        raise DatabaseIntegrityError(
            "database schema metadata is not initialized"
        ) from None
    raise DatabaseIntegrityError("database integrity check failed") from None


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
