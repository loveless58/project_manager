"""Consistent SQLite backups for a trusted local operations directory.

`output_path.parent` must be controlled by trusted local operators. Hard-link
no-overwrite publication protects cooperating processes. Python's standard
library cannot atomically compare a directory entry's identity and unlink it,
so same-privilege malicious path replacement is outside this module's
guarantees.
"""

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from weakref import WeakKeyDictionary

from ..contracts import (
    BackupError,
    BackupManifest,
    DatabaseError,
    MigrationInfo,
    SchemaState,
)
from .connection import SqliteConnectionOptions, open_sqlite_connection
from .schema import inspect_schema


_HASH_CHUNK_SIZE = 1024 * 1024
_LOWERCASE_SHA256 = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = frozenset(BackupManifest.__dataclass_fields__)

_FileIdentity = tuple[int, int]
_CatalogFingerprint = tuple[tuple[int, str, str], ...]


class _RemovalResult(Enum):
    REMOVED = "removed"
    ABSENT = "absent"
    NOT_OWNED = "not_owned"
    FAILED = "failed"


class _PublicationFailure(Exception):
    def __init__(self, removal_result: _RemovalResult) -> None:
        super().__init__()
        self.removal_result = removal_result


_TEMP_REMOVED = frozenset({_RemovalResult.REMOVED, _RemovalResult.ABSENT})
_OWNED_FINAL_ABSENT = frozenset(
    {*_TEMP_REMOVED, _RemovalResult.NOT_OWNED}
)


class _BackupCreationProvenance:
    __slots__ = ("__weakref__",)


class _VerifiedMigrationBackup:
    __slots__ = ("__weakref__",)


class _MigrationBackupAuthorization:
    __slots__ = ("__weakref__",)


@dataclass(frozen=True, slots=True)
class _BackupCreationRecord:
    source_path: Path
    source_identity: _FileIdentity
    backup_path: Path
    backup_identity: _FileIdentity
    manifest_path: Path
    manifest_identity: _FileIdentity
    catalog_fingerprint: _CatalogFingerprint


@dataclass(frozen=True, slots=True)
class _VerifiedMigrationBackupRecord:
    provenance: _BackupCreationRecord
    manifest: BackupManifest
    backup_sha256: str
    backup_size_bytes: int
    manifest_sha256: str
    manifest_size_bytes: int


@dataclass(frozen=True, slots=True)
class _MigrationBackupAuthorizationRecord:
    source_path: Path
    source_identity: _FileIdentity
    backup_path: Path
    backup_identity: _FileIdentity
    backup_sha256: str
    backup_size_bytes: int
    manifest_path: Path
    manifest_identity: _FileIdentity
    manifest_sha256: str
    manifest_size_bytes: int
    catalog_fingerprint: _CatalogFingerprint
    starting_schema_version: int
    catalog_target_version: int


_TOKEN_RECORD_LOCK = RLock()
_CREATION_RECORDS = WeakKeyDictionary()
_VERIFIED_RECORDS = WeakKeyDictionary()
_AUTHORIZATION_RECORDS = WeakKeyDictionary()


def _issue_creation_provenance(
    record: _BackupCreationRecord,
) -> _BackupCreationProvenance:
    token = _BackupCreationProvenance()
    with _TOKEN_RECORD_LOCK:
        _CREATION_RECORDS[token] = record
    return token


def _creation_record_for(token: object) -> _BackupCreationRecord:
    if type(token) is not _BackupCreationProvenance:
        raise ValueError
    with _TOKEN_RECORD_LOCK:
        try:
            return _CREATION_RECORDS[token]
        except KeyError:
            raise ValueError from None


def _issue_verified_backup(
    record: _VerifiedMigrationBackupRecord,
) -> _VerifiedMigrationBackup:
    token = _VerifiedMigrationBackup()
    with _TOKEN_RECORD_LOCK:
        _VERIFIED_RECORDS[token] = record
    return token


def _verified_record_for(token: object) -> _VerifiedMigrationBackupRecord:
    if type(token) is not _VerifiedMigrationBackup:
        raise ValueError
    with _TOKEN_RECORD_LOCK:
        try:
            return _VERIFIED_RECORDS[token]
        except KeyError:
            raise ValueError from None


def _issue_migration_authorization(
    record: _MigrationBackupAuthorizationRecord,
) -> _MigrationBackupAuthorization:
    token = _MigrationBackupAuthorization()
    with _TOKEN_RECORD_LOCK:
        _AUTHORIZATION_RECORDS[token] = record
    return token


def _authorization_record_for(
    token: object,
) -> _MigrationBackupAuthorizationRecord:
    if type(token) is not _MigrationBackupAuthorization:
        raise ValueError
    with _TOKEN_RECORD_LOCK:
        try:
            return _AUTHORIZATION_RECORDS[token]
        except KeyError:
            raise ValueError from None


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_path: Path
    manifest_path: Path
    manifest: BackupManifest
    _provenance: _BackupCreationProvenance | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def create_sqlite_backup(
    database_path: Path,
    output_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> BackupResult:
    """Create a verified backup beneath a trusted `output_path.parent`.

    The parent must not permit untrusted processes to rename or replace its
    entries while publication or compensation is running.
    """

    try:
        return _create_sqlite_backup(
            database_path,
            output_path,
            catalog,
            options=options,
        )
    except BackupError:
        raise
    except (DatabaseError, OSError, sqlite3.DatabaseError, ValueError):
        raise BackupError("database backup failed") from None


def load_backup_manifest(path: Path) -> BackupManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
            raise ValueError
        manifest = BackupManifest(**payload)
        _validate_manifest(manifest)
        return manifest
    except BackupError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise BackupError("backup manifest is invalid") from None


def verify_backup_artifacts(
    backup_path: Path,
    manifest_path: Path,
) -> BackupManifest:
    manifest = load_backup_manifest(manifest_path)
    try:
        if backup_path.stat().st_size != manifest.size_bytes:
            raise BackupError("backup verification failed")
        if _sha256_file(backup_path) != manifest.sha256:
            raise BackupError("backup verification failed")
    except BackupError:
        raise
    except OSError:
        raise BackupError("backup verification failed") from None
    return manifest


def verify_migration_backup(
    backup_result: BackupResult,
) -> _VerifiedMigrationBackup:
    """Reload and verify artifacts from a backup created in this process.

    A public manifest is evidence about backup bytes, not migration authority.
    Migration verification additionally requires sealed source/artifact
    provenance attached by ``create_sqlite_backup``.
    """

    try:
        if type(backup_result) is not BackupResult:
            raise ValueError
        provenance = _creation_record_for(backup_result._provenance)

        backup_path, backup_identity = _resolved_path_identity(
            backup_result.backup_path
        )
        manifest_path, manifest_identity = _resolved_path_identity(
            backup_result.manifest_path
        )
        if (
            backup_path != provenance.backup_path
            or backup_identity != provenance.backup_identity
            or manifest_path != provenance.manifest_path
            or manifest_identity != provenance.manifest_identity
        ):
            raise ValueError

        manifest = verify_backup_artifacts(backup_path, manifest_path)
        if manifest != backup_result.manifest:
            raise ValueError
        backup_size = backup_path.stat().st_size
        manifest_size = manifest_path.stat().st_size
        backup_sha256 = _sha256_file(backup_path)
        manifest_sha256 = _sha256_file(manifest_path)
        if backup_size != manifest.size_bytes or backup_sha256 != manifest.sha256:
            raise ValueError

        return _issue_verified_backup(
            _VerifiedMigrationBackupRecord(
                provenance=provenance,
                manifest=manifest,
                backup_sha256=backup_sha256,
                backup_size_bytes=backup_size,
                manifest_sha256=manifest_sha256,
                manifest_size_bytes=manifest_size,
            )
        )
    except (BackupError, OSError, ValueError):
        raise BackupError("migration backup verification failed") from None


def authorize_migration_backup(
    database_path: Path,
    verified_backup: object,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> _MigrationBackupAuthorization:
    """Bind a verified backup to one live database instance and catalog."""

    try:
        verified_record = _verified_record_for(verified_backup)
        provenance = verified_record.provenance
        source_path, source_identity = _resolved_path_identity(database_path)
        if (
            source_path != provenance.source_path
            or source_identity != provenance.source_identity
            or _catalog_fingerprint(catalog) != provenance.catalog_fingerprint
        ):
            raise ValueError

        status = inspect_schema(database_path, catalog, options=options)
        if (
            status.state not in (SchemaState.CURRENT, SchemaState.PENDING)
            or status.current_version != verified_record.manifest.schema_version
            or status.target_version
            != verified_record.manifest.catalog_target_version
        ):
            raise ValueError

        source_path_after, source_identity_after = _resolved_path_identity(
            database_path
        )
        if (
            source_path_after != source_path
            or source_identity_after != source_identity
        ):
            raise ValueError

        return _issue_migration_authorization(
            _MigrationBackupAuthorizationRecord(
                source_path=source_path,
                source_identity=source_identity,
                backup_path=provenance.backup_path,
                backup_identity=provenance.backup_identity,
                backup_sha256=verified_record.backup_sha256,
                backup_size_bytes=verified_record.backup_size_bytes,
                manifest_path=provenance.manifest_path,
                manifest_identity=provenance.manifest_identity,
                manifest_sha256=verified_record.manifest_sha256,
                manifest_size_bytes=verified_record.manifest_size_bytes,
                catalog_fingerprint=provenance.catalog_fingerprint,
                starting_schema_version=verified_record.manifest.schema_version,
                catalog_target_version=verified_record.manifest.catalog_target_version,
            )
        )
    except (BackupError, DatabaseError, OSError, ValueError):
        raise BackupError("backup authorization is invalid") from None


def _revalidate_migration_authorization(
    authorization: object,
    database_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    starting_schema_version: int,
    catalog_target_version: int,
) -> None:
    try:
        authorization_record = _authorization_record_for(authorization)
        if (
            authorization_record.starting_schema_version != starting_schema_version
            or authorization_record.catalog_target_version != catalog_target_version
            or authorization_record.catalog_fingerprint
            != _catalog_fingerprint(catalog)
        ):
            raise ValueError

        source_path, source_identity = _resolved_path_identity(database_path)
        backup_path, backup_identity = _resolved_path_identity(
            authorization_record.backup_path
        )
        manifest_path, manifest_identity = _resolved_path_identity(
            authorization_record.manifest_path
        )
        if (
            source_path != authorization_record.source_path
            or source_identity != authorization_record.source_identity
            or backup_path != authorization_record.backup_path
            or backup_identity != authorization_record.backup_identity
            or manifest_path != authorization_record.manifest_path
            or manifest_identity != authorization_record.manifest_identity
            or backup_path.stat().st_size != authorization_record.backup_size_bytes
            or manifest_path.stat().st_size != authorization_record.manifest_size_bytes
            or _sha256_file(backup_path) != authorization_record.backup_sha256
            or _sha256_file(manifest_path) != authorization_record.manifest_sha256
        ):
            raise ValueError

        manifest = verify_backup_artifacts(backup_path, manifest_path)
        if (
            manifest.schema_version != authorization_record.starting_schema_version
            or manifest.catalog_target_version
            != authorization_record.catalog_target_version
        ):
            raise ValueError
    except (BackupError, OSError, ValueError):
        raise BackupError("backup authorization is invalid") from None


def _create_sqlite_backup(
    database_path: Path,
    output_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions,
) -> BackupResult:
    source_path, source_identity = _resolved_path_identity(database_path)
    source_status = inspect_schema(database_path, catalog, options=options)
    _require_backup_eligible(source_status.state)
    _require_valid_foreign_keys(database_path, options=options)

    manifest_path = Path(f"{output_path}.manifest.json")
    staging_dir: Path | None = None
    backup_temp: Path | None = None
    manifest_temp: Path | None = None
    staging_identity: tuple[int, int] | None = None
    backup_identity: tuple[int, int] | None = None
    manifest_identity: tuple[int, int] | None = None

    try:
        staging_dir, staging_identity = _create_staging_directory(
            output_path.parent,
            output_path.name,
        )
        backup_temp, backup_identity = _create_temp_file(
            staging_dir,
            output_path.name,
        )
        manifest_temp, manifest_identity = _create_temp_file(
            staging_dir,
            manifest_path.name,
        )
        _copy_sqlite_snapshot(database_path, backup_temp, options=options)

        snapshot_status = inspect_schema(backup_temp, catalog, options=options)
        _require_backup_eligible(snapshot_status.state)
        _require_valid_foreign_keys(backup_temp, options=options)
        source_path_after, source_identity_after = _resolved_path_identity(
            database_path
        )
        if (
            source_path_after != source_path
            or source_identity_after != source_identity
        ):
            raise BackupError("database backup source changed")

        manifest = BackupManifest(
            format_version=1,
            schema_version=snapshot_status.current_version,
            catalog_target_version=snapshot_status.target_version,
            sha256=_sha256_file(backup_temp),
            size_bytes=backup_temp.stat().st_size,
            created_at_utc=_utc_timestamp(),
            sqlite_version=sqlite3.sqlite_version,
            integrity_check="ok",
        )
        _write_manifest(manifest_temp, manifest)

        if _path_identity(backup_temp) != backup_identity:
            raise BackupError("backup temporary file ownership changed")
        try:
            _link_no_overwrite(backup_temp, output_path, backup_identity)
        except _PublicationFailure as error:
            if error.removal_result is _RemovalResult.FAILED:
                raise BackupError("backup publication cleanup failed") from None
            raise BackupError("backup publication failed") from None

        backup_temp_result = _remove_owned_path(backup_temp, backup_identity)
        if backup_temp_result in _TEMP_REMOVED:
            backup_temp = None
        else:
            backup_compensation = _remove_owned_path(
                output_path,
                backup_identity,
            )
            if backup_compensation not in _OWNED_FINAL_ABSENT:
                raise BackupError("backup publication cleanup failed")
            raise BackupError("backup temporary file cleanup failed")

        try:
            if _path_identity(manifest_temp) != manifest_identity:
                raise BackupError("backup publication failed")
            try:
                _link_no_overwrite(
                    manifest_temp,
                    manifest_path,
                    manifest_identity,
                )
            except _PublicationFailure as error:
                if error.removal_result is _RemovalResult.FAILED:
                    raise BackupError(
                        "backup publication cleanup failed"
                    ) from None
                raise BackupError("backup publication failed") from None

            manifest_temp_result = _remove_owned_path(
                manifest_temp,
                manifest_identity,
            )
            if manifest_temp_result in _TEMP_REMOVED:
                manifest_temp = None
            else:
                raise BackupError("backup temporary file cleanup failed")

            staging_result = _remove_owned_directory(
                staging_dir,
                staging_identity,
            )
            if staging_result in _TEMP_REMOVED:
                staging_dir = None
            else:
                raise BackupError("backup temporary file cleanup failed")

            if (
                _path_identity(output_path) != backup_identity
                or _path_identity(manifest_path) != manifest_identity
            ):
                raise BackupError("backup publication failed")
        except (BackupError, OSError) as error:
            compensation_succeeded = _remove_published_pair(
                output_path,
                backup_identity,
                manifest_path,
                manifest_identity,
            )
            if not compensation_succeeded:
                raise BackupError("backup publication cleanup failed") from None
            if isinstance(error, BackupError):
                raise
            raise BackupError("backup publication failed") from None
        return BackupResult(
            output_path,
            manifest_path,
            manifest,
            _provenance=_issue_creation_provenance(
                _BackupCreationRecord(
                    source_path=source_path,
                    source_identity=source_identity,
                    backup_path=output_path.resolve(strict=True),
                    backup_identity=backup_identity,
                    manifest_path=manifest_path.resolve(strict=True),
                    manifest_identity=manifest_identity,
                    catalog_fingerprint=_catalog_fingerprint(catalog),
                )
            ),
        )
    finally:
        if backup_temp is not None and backup_identity is not None:
            _remove_owned_path(backup_temp, backup_identity)
        if manifest_temp is not None and manifest_identity is not None:
            _remove_owned_path(
                manifest_temp,
                manifest_identity,
            )
        if staging_dir is not None and staging_identity is not None:
            _remove_owned_directory(staging_dir, staging_identity)


def _copy_sqlite_snapshot(
    database_path: Path,
    destination_path: Path,
    *,
    options: SqliteConnectionOptions,
) -> None:
    source = open_sqlite_connection(database_path, options=options, create=False)
    try:
        destination = sqlite3.connect(
            destination_path,
            timeout=options.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _require_backup_eligible(state: SchemaState) -> None:
    if state not in (SchemaState.CURRENT, SchemaState.PENDING):
        raise BackupError("database schema is not eligible for backup")


def _require_valid_foreign_keys(
    database_path: Path,
    *,
    options: SqliteConnectionOptions,
) -> None:
    connection = open_sqlite_connection(database_path, options=options, create=False)
    try:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise BackupError("database backup validation failed")
    finally:
        connection.close()


def _create_staging_directory(
    parent: Path,
    final_name: str,
) -> tuple[Path, tuple[int, int]]:
    path = Path(
        tempfile.mkdtemp(
            prefix=f".{final_name}.backup-staging.",
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


def _link_no_overwrite(
    source: Path,
    destination: Path,
    identity: tuple[int, int],
) -> None:
    try:
        os.link(source, destination)
    except OSError:
        removal_result = _remove_owned_path(destination, identity)
        raise _PublicationFailure(removal_result) from None


def _write_manifest(path: Path, manifest: BackupManifest) -> None:
    payload = json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_fingerprint(
    catalog: Sequence[MigrationInfo],
) -> _CatalogFingerprint:
    return tuple(
        (item.version, item.name, item.checksum_sha256)
        for item in catalog
    )


def _resolved_path_identity(path: Path) -> tuple[Path, _FileIdentity]:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise OSError
    return resolved, _path_identity(resolved)


def _validate_manifest(manifest: BackupManifest) -> None:
    if type(manifest.format_version) is not int or manifest.format_version != 1:
        raise ValueError
    if type(manifest.schema_version) is not int or manifest.schema_version < 0:
        raise ValueError
    if (
        type(manifest.catalog_target_version) is not int
        or manifest.catalog_target_version < 0
    ):
        raise ValueError
    if (
        type(manifest.sha256) is not str
        or _LOWERCASE_SHA256.fullmatch(manifest.sha256) is None
    ):
        raise ValueError
    if type(manifest.size_bytes) is not int or manifest.size_bytes < 0:
        raise ValueError
    if type(manifest.created_at_utc) is not str:
        raise ValueError
    datetime.strptime(manifest.created_at_utc, "%Y-%m-%dT%H:%M:%SZ")
    if type(manifest.sqlite_version) is not str or not manifest.sqlite_version:
        raise ValueError
    if manifest.integrity_check != "ok":
        raise ValueError


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


def _remove_published_pair(
    backup_path: Path,
    backup_identity: tuple[int, int],
    manifest_path: Path,
    manifest_identity: tuple[int, int],
) -> bool:
    manifest_result = _remove_owned_path(
        manifest_path,
        manifest_identity,
    )
    if manifest_result is _RemovalResult.FAILED:
        return False
    backup_result = _remove_owned_path(
        backup_path,
        backup_identity,
    )
    return backup_result in _OWNED_FINAL_ABSENT


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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "BackupResult",
    "authorize_migration_backup",
    "create_sqlite_backup",
    "load_backup_manifest",
    "verify_backup_artifacts",
    "verify_migration_backup",
]
