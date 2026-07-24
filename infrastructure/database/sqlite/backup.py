import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_path: Path
    manifest_path: Path
    manifest: BackupManifest


def create_sqlite_backup(
    database_path: Path,
    output_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
) -> BackupResult:
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


def _create_sqlite_backup(
    database_path: Path,
    output_path: Path,
    catalog: Sequence[MigrationInfo],
    *,
    options: SqliteConnectionOptions,
) -> BackupResult:
    source_status = inspect_schema(database_path, catalog, options=options)
    _require_backup_eligible(source_status.state)

    manifest_path = Path(f"{output_path}.manifest.json")
    backup_temp: Path | None = None
    manifest_temp: Path | None = None
    backup_published = False
    backup_identity: tuple[int, int] | None = None

    try:
        backup_temp = _create_temp_file(output_path.parent, output_path.name)
        manifest_temp = _create_temp_file(output_path.parent, manifest_path.name)
        _copy_sqlite_snapshot(database_path, backup_temp, options=options)

        snapshot_status = inspect_schema(backup_temp, catalog, options=options)
        _require_backup_eligible(snapshot_status.state)
        _require_valid_foreign_keys(backup_temp, options=options)

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

        os.link(backup_temp, output_path)
        backup_published = True
        backup_stat = output_path.stat()
        backup_identity = (backup_stat.st_dev, backup_stat.st_ino)
        _remove_owned_path(backup_temp)
        backup_temp = None

        try:
            os.link(manifest_temp, manifest_path)
        except OSError:
            if backup_published and backup_identity is not None:
                _remove_published_path(output_path, backup_identity)
            raise BackupError("backup publication failed") from None

        _remove_owned_path(manifest_temp)
        manifest_temp = None
        return BackupResult(output_path, manifest_path, manifest)
    except BackupError:
        raise
    except OSError:
        if backup_published and backup_identity is not None:
            _remove_published_path(output_path, backup_identity)
        raise BackupError("backup publication failed") from None
    finally:
        _remove_owned_path(backup_temp)
        _remove_owned_path(manifest_temp)


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


def _create_temp_file(parent: Path, final_name: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{final_name}.",
        suffix=".tmp",
        dir=parent,
    )
    os.close(descriptor)
    return Path(name)


def _write_manifest(path: Path, manifest: BackupManifest) -> None:
    payload = json.dumps(asdict(manifest), ensure_ascii=False, sort_keys=True) + "\n"
    path.write_text(payload, encoding="utf-8", newline="\n")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


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


def _remove_published_path(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.stat()
        if (current.st_dev, current.st_ino) == identity:
            path.unlink()
    except OSError:
        pass


def _remove_owned_path(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "BackupResult",
    "create_sqlite_backup",
    "load_backup_manifest",
    "verify_backup_artifacts",
]
