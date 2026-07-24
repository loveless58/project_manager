"""Verified SQLite restores between trusted local operations directories.

Each parent must be a trusted local operations directory controlled by local
operators. Private staging and hard-link no-overwrite operations protect
cooperating processes. Python's standard library cannot atomically compare a
directory entry's identity and unlink it. It also cannot prevent
same-privilege malicious path replacement or in-place inode modification, so
those attacks are outside this module's guarantees.
"""

import hashlib
import os
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..contracts import (
    BackupError,
    BackupManifest,
    DatabaseError,
    MigrationInfo,
    RestoreVerificationError,
    SchemaState,
)
from .backup import load_backup_manifest
from .connection import SqliteConnectionOptions, open_sqlite_connection
from .schema import inspect_schema


_HASH_CHUNK_SIZE = 1024 * 1024


class _RemovalResult(Enum):
    REMOVED = "removed"
    ABSENT = "absent"
    NOT_OWNED = "not_owned"
    FAILED = "failed"


_REMOVED_OR_ABSENT = frozenset({_RemovalResult.REMOVED, _RemovalResult.ABSENT})
_OWNED_FINAL_ABSENT = frozenset(
    {*_REMOVED_OR_ABSENT, _RemovalResult.NOT_OWNED}
)


@dataclass(frozen=True, slots=True)
class RestoreResult:
    schema_version: int
    sha256: str
    size_bytes: int


def verify_and_restore_sqlite(
    backup_path: Path,
    manifest_path: Path,
    target_path: Path,
    *,
    active_database_path: Path,
    catalog: Sequence[MigrationInfo],
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> RestoreResult:
    """Verify and publish between trusted backup and target parents.

    Both `backup_path.parent` and `target_path.parent` must be trusted local
    operations directories. The source is identity-bound before SQLite opens.
    The target must be a new path distinct from the active database. This
    operation never switches application settings or replaces the active
    database.
    """

    try:
        return _verify_and_restore_sqlite(
            backup_path,
            manifest_path,
            target_path,
            active_database_path=active_database_path,
            catalog=catalog,
            options=options,
        )
    except RestoreVerificationError:
        raise
    except (BackupError, DatabaseError, OSError, sqlite3.DatabaseError, ValueError):
        raise RestoreVerificationError("restore verification failed") from None


def _verify_and_restore_sqlite(
    backup_path: Path,
    manifest_path: Path,
    target_path: Path,
    *,
    active_database_path: Path,
    catalog: Sequence[MigrationInfo],
    options: SqliteConnectionOptions,
) -> RestoreResult:
    raw_target_exists = os.path.lexists(target_path)
    resolved_target = target_path.resolve()
    resolved_active = active_database_path.resolve()
    if resolved_target == resolved_active:
        raise RestoreVerificationError("target must be a new path")
    if raw_target_exists or os.path.lexists(resolved_target):
        raise RestoreVerificationError("target must not exist")

    resolved_backup = backup_path.resolve()
    manifest = _load_restore_manifest(manifest_path)
    source_staging_dir: Path | None = None
    source_staging_identity: tuple[int, int] | None = None
    bound_backup: Path | None = None
    bound_backup_identity: tuple[int, int] | None = None
    target_staging_dir: Path | None = None
    target_staging_identity: tuple[int, int] | None = None
    temporary_target: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    published = False

    try:
        source_staging_dir, source_staging_identity = _create_staging_directory(
            resolved_backup.parent,
            resolved_backup.name,
            purpose="source",
        )
        bound_backup, bound_backup_identity = _bind_backup_source(
            resolved_backup,
            source_staging_dir,
        )
        _verify_bound_backup(
            bound_backup,
            bound_backup_identity,
            manifest,
        )
        target_staging_dir, target_staging_identity = _create_staging_directory(
            resolved_target.parent,
            resolved_target.name,
            purpose="target",
        )
        temporary_target, temporary_identity = _create_temp_file(
            target_staging_dir,
            resolved_target.name,
        )
        _materialize_sqlite_backup(
            bound_backup,
            temporary_target,
            manifest,
            bound_backup_identity,
            options=options,
        )
        schema_version = _validate_restored_database(
            temporary_target,
            manifest,
            catalog,
            options=options,
        )
        result = RestoreResult(
            schema_version=schema_version,
            sha256=_sha256_file(temporary_target),
            size_bytes=temporary_target.stat().st_size,
        )

        if _path_identity(temporary_target) != temporary_identity:
            raise RestoreVerificationError("restore temporary file ownership changed")
        try:
            os.link(temporary_target, resolved_target)
        except OSError:
            removal_result = _remove_owned_path(
                resolved_target,
                temporary_identity,
            )
            if removal_result is _RemovalResult.FAILED:
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                ) from None
            raise RestoreVerificationError("restore publication failed") from None
        published = True

        temporary_result = _remove_owned_path(
            temporary_target,
            temporary_identity,
        )
        if temporary_result in _REMOVED_OR_ABSENT:
            temporary_target = None
        else:
            if not _compensate_published_target(
                resolved_target,
                temporary_identity,
            ):
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                )
            published = False
            raise RestoreVerificationError(
                "restore temporary file cleanup failed"
            )

        staging_result = _remove_owned_directory(
            target_staging_dir,
            target_staging_identity,
        )
        if staging_result in _REMOVED_OR_ABSENT:
            target_staging_dir = None
        else:
            if not _compensate_published_target(
                resolved_target,
                temporary_identity,
            ):
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                )
            published = False
            raise RestoreVerificationError(
                "restore temporary file cleanup failed"
            )

        if _path_identity(resolved_target) != temporary_identity:
            if not _compensate_published_target(
                resolved_target,
                temporary_identity,
            ):
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                )
            published = False
            raise RestoreVerificationError("restore publication failed")

        bound_result = _remove_owned_path(
            bound_backup,
            bound_backup_identity,
        )
        if bound_result in _REMOVED_OR_ABSENT:
            bound_backup = None
        else:
            if not _compensate_published_target(
                resolved_target,
                temporary_identity,
            ):
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                )
            published = False
            raise RestoreVerificationError("restore temporary file cleanup failed")

        source_staging_result = _remove_owned_directory(
            source_staging_dir,
            source_staging_identity,
        )
        if source_staging_result in _REMOVED_OR_ABSENT:
            source_staging_dir = None
        else:
            if not _compensate_published_target(
                resolved_target,
                temporary_identity,
            ):
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                )
            published = False
            raise RestoreVerificationError("restore temporary file cleanup failed")
        return result
    except (
        RestoreVerificationError,
        DatabaseError,
        OSError,
        sqlite3.DatabaseError,
        ValueError,
    ):
        if published and temporary_identity is not None:
            if not _compensate_published_target(
                resolved_target,
                temporary_identity,
            ):
                raise RestoreVerificationError(
                    "restore publication cleanup failed"
                ) from None
        raise
    finally:
        cleanup_failed = False
        if temporary_target is not None and temporary_identity is not None:
            result = _remove_owned_path(temporary_target, temporary_identity)
            if result is _RemovalResult.FAILED:
                result = _remove_owned_path(temporary_target, temporary_identity)
            cleanup_failed = cleanup_failed or result is _RemovalResult.FAILED
        if target_staging_dir is not None and target_staging_identity is not None:
            result = _remove_owned_directory(
                target_staging_dir,
                target_staging_identity,
            )
            if result is _RemovalResult.FAILED:
                result = _remove_owned_directory(
                    target_staging_dir,
                    target_staging_identity,
                )
            cleanup_failed = cleanup_failed or result is _RemovalResult.FAILED
        if bound_backup is not None and bound_backup_identity is not None:
            result = _remove_owned_path(bound_backup, bound_backup_identity)
            if result is _RemovalResult.FAILED:
                result = _remove_owned_path(bound_backup, bound_backup_identity)
            cleanup_failed = cleanup_failed or result is _RemovalResult.FAILED
        if source_staging_dir is not None and source_staging_identity is not None:
            result = _remove_owned_directory(
                source_staging_dir,
                source_staging_identity,
            )
            if result is _RemovalResult.FAILED:
                result = _remove_owned_directory(
                    source_staging_dir,
                    source_staging_identity,
                )
            cleanup_failed = cleanup_failed or result is _RemovalResult.FAILED
        if cleanup_failed:
            raise RestoreVerificationError("restore temporary file cleanup failed") from None


def _load_restore_manifest(
    manifest_path: Path,
) -> BackupManifest:
    try:
        manifest = load_backup_manifest(manifest_path)
    except BackupError:
        raise RestoreVerificationError("backup manifest is invalid") from None

    return manifest


def _verify_backup_bytes(
    backup_path: Path,
    manifest: BackupManifest,
) -> None:
    try:
        if backup_path.stat().st_size != manifest.size_bytes:
            raise RestoreVerificationError("backup verification failed")
        if _sha256_file(backup_path) != manifest.sha256:
            raise RestoreVerificationError("backup verification failed")
    except RestoreVerificationError:
        raise
    except OSError:
        raise RestoreVerificationError("backup verification failed") from None


def _verify_bound_backup(
    backup_path: Path,
    identity: tuple[int, int],
    manifest: BackupManifest,
) -> None:
    try:
        if _path_identity(backup_path) != identity:
            raise RestoreVerificationError("backup source identity changed")
        _verify_backup_bytes(backup_path, manifest)
        if _path_identity(backup_path) != identity:
            raise RestoreVerificationError("backup source identity changed")
    except RestoreVerificationError:
        raise
    except OSError:
        raise RestoreVerificationError("backup verification failed") from None


def _materialize_sqlite_backup(
    backup_path: Path,
    destination_path: Path,
    manifest: BackupManifest,
    backup_identity: tuple[int, int],
    *,
    options: SqliteConnectionOptions,
) -> None:
    source = _open_backup_read_only(backup_path, options=options)
    try:
        _verify_bound_backup(backup_path, backup_identity, manifest)
        destination = _open_restore_destination(
            destination_path,
            timeout=options.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        try:
            source.backup(destination)
        finally:
            destination.close()
        _verify_bound_backup(backup_path, backup_identity, manifest)
    finally:
        source.close()


def _open_restore_destination(
    database_path: Path,
    **connect_options: object,
) -> sqlite3.Connection:
    return sqlite3.connect(database_path, **connect_options)


def _open_backup_read_only(
    backup_path: Path,
    *,
    options: SqliteConnectionOptions,
) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{backup_path.resolve().as_uri()}?mode=ro&immutable=1",
        timeout=options.busy_timeout_ms / 1_000,
        isolation_level=None,
        uri=True,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {options.busy_timeout_ms}")
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.DatabaseError:
        connection.close()
        raise


def _validate_restored_database(
    database_path: Path,
    manifest: BackupManifest,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions,
) -> int:
    status = inspect_schema(database_path, catalog, options=options)
    if (
        status.state not in (SchemaState.CURRENT, SchemaState.PENDING)
        or status.current_version != manifest.schema_version
        or status.target_version != manifest.catalog_target_version
    ):
        raise RestoreVerificationError("restore schema validation failed")

    connection = open_sqlite_connection(
        database_path,
        options=options,
        create=False,
    )
    try:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RestoreVerificationError("restore database validation failed")
    finally:
        connection.close()
    return status.current_version


def _create_staging_directory(
    parent: Path,
    final_name: str,
    *,
    purpose: str,
) -> tuple[Path, tuple[int, int]]:
    path = Path(
        tempfile.mkdtemp(
            prefix=f".{final_name}.restore-staging.{purpose}.",
            dir=parent,
        )
    )
    try:
        identity = _path_identity(path)
    except OSError:
        _remove_private_empty_directory_without_identity(path)
        raise
    try:
        path.chmod(0o700)
    except OSError:
        _remove_owned_directory(path, identity)
        raise
    return path, identity


def _bind_backup_source(
    backup_path: Path,
    staging_dir: Path,
) -> tuple[Path, tuple[int, int]]:
    bound_path = staging_dir / "verified-source.sqlite3"
    try:
        source_identity = _path_identity(backup_path)
    except OSError:
        raise RestoreVerificationError("backup source binding failed") from None

    try:
        os.link(backup_path, bound_path)
    except OSError:
        cleanup_result = _remove_owned_path(bound_path, source_identity)
        if cleanup_result is _RemovalResult.FAILED:
            raise RestoreVerificationError(
                "backup source binding cleanup failed"
            ) from None
        raise RestoreVerificationError("backup source binding failed") from None

    try:
        identity = _path_identity(bound_path)
    except OSError:
        cleanup_result = _remove_owned_path(bound_path, source_identity)
        if cleanup_result is _RemovalResult.FAILED:
            raise RestoreVerificationError(
                "backup source binding cleanup failed"
            ) from None
        raise RestoreVerificationError("backup source binding failed") from None
    return bound_path, identity


def _create_temp_file(
    parent: Path,
    final_name: str,
) -> tuple[Path, tuple[int, int]]:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{final_name}.",
        suffix=".tmp",
        dir=parent,
    )
    path = Path(name)
    try:
        stat_result = os.fstat(descriptor)
    except OSError:
        try:
            os.close(descriptor)
        finally:
            _remove_private_temp_without_identity(path)
        raise

    identity = _identity_from_stat(stat_result)
    try:
        os.close(descriptor)
    except OSError:
        _remove_owned_path(path, identity)
        raise
    return path, identity


def _compensate_published_target(
    path: Path,
    identity: tuple[int, int],
) -> bool:
    return _remove_owned_path(path, identity) in _OWNED_FINAL_ABSENT


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _identity_from_stat(stat_result: os.stat_result) -> tuple[int, int]:
    return (stat_result.st_dev, stat_result.st_ino)


def _path_identity(path: Path) -> tuple[int, int]:
    return _identity_from_stat(path.stat())


def _remove_owned_path(
    path: Path,
    identity: tuple[int, int] | None,
) -> _RemovalResult:
    if identity is None:
        return _RemovalResult.FAILED
    try:
        current_identity = _path_identity(path)
    except FileNotFoundError:
        return _RemovalResult.ABSENT
    except OSError:
        return _RemovalResult.FAILED
    if current_identity != identity:
        return _RemovalResult.NOT_OWNED
    try:
        path.unlink()
    except FileNotFoundError:
        return _RemovalResult.ABSENT
    except OSError:
        return _RemovalResult.FAILED
    return _RemovalResult.REMOVED


def _remove_owned_directory(
    path: Path,
    identity: tuple[int, int] | None,
) -> _RemovalResult:
    if identity is None:
        return _RemovalResult.FAILED
    try:
        current_identity = _path_identity(path)
    except FileNotFoundError:
        return _RemovalResult.ABSENT
    except OSError:
        return _RemovalResult.FAILED
    if current_identity != identity:
        return _RemovalResult.NOT_OWNED
    try:
        path.rmdir()
    except FileNotFoundError:
        return _RemovalResult.ABSENT
    except OSError:
        return _RemovalResult.FAILED
    return _RemovalResult.REMOVED


def _remove_private_temp_without_identity(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _remove_private_empty_directory_without_identity(path: Path) -> bool:
    try:
        path.rmdir()
    except OSError:
        return False
    return True


__all__ = ["RestoreResult", "verify_and_restore_sqlite"]
