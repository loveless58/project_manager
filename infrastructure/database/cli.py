"""Standalone, redacted operations CLI for the SQLite database kernel."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, TextIO

from platform_core.settings import SettingsError, load_app_settings

from .contracts import (
    DatabaseBusyError,
    DatabaseConfigurationError,
    DatabaseError,
    DatabaseIntegrityError,
    MigrationCatalogError,
    MigrationChecksumError,
    SchemaState,
    SchemaStatus,
    SchemaTooNewError,
    UnsupportedDatabaseProviderError,
)
from .migration_catalog import load_migration_catalog
from .sqlite.backup import create_sqlite_backup, verify_backup_artifacts
from .sqlite.migration_runner import apply_pending_migrations, initialize_database
from .sqlite.restore import verify_and_restore_sqlite
from .sqlite.schema import inspect_schema


EXIT_OK = 0
EXIT_ACTION_REQUIRED = 2
EXIT_INCOMPATIBLE = 3
EXIT_BUSY = 4
EXIT_OPERATION_FAILED = 5

_SCHEMA_VERSION = "database_cli.v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PRODUCTION_MIGRATIONS = _REPOSITORY_ROOT / "migrations" / "sqlite"
_BACKUP_SHA_PREFIX_LENGTH = 12

_INCOMPATIBLE_ERROR_CODES = frozenset(
    {
        "DB.CONFIGURATION",
        "DB.MIGRATION_CATALOG",
        "DB.MIGRATION_CHECKSUM",
        "DB.SCHEMA_TOO_NEW",
        "DB.UNSUPPORTED_PROVIDER",
    }
)
_SAFE_ERROR_MESSAGES = {
    "DB.CONFIGURATION": "database configuration is invalid",
    "DB.MIGRATION_CATALOG": "migration catalog is invalid",
    "DB.MIGRATION_CHECKSUM": "migration checksum is invalid",
    "DB.SCHEMA_TOO_NEW": "database schema is incompatible",
    "DB.MIGRATION_EXECUTION": "database migration failed",
    "DB.BUSY": "database is busy",
    "DB.INTEGRITY": "database integrity check failed",
    "DB.BACKUP": "database backup failed",
    "DB.RESTORE_VERIFICATION": "restore verification failed",
    "DB.UOW_STATE": "database operation failed",
    "DB.UNSUPPORTED_PROVIDER": "database provider is unsupported",
    "DB.OPERATION_FAILED": "database operation failed",
}


class _ArgumentError(Exception):
    pass


class _ParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status


class _SafeArgumentParser(argparse.ArgumentParser):
    def __init__(
        self,
        *args,
        stdout: TextIO,
        stderr: TextIO,
        **kwargs,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        super().__init__(*args, **kwargs)

    def print_help(self, file: TextIO | None = None) -> None:
        super().print_help(file=self._stdout if file is None else file)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        if message:
            destination = self._stdout if status == 0 else self._stderr
            destination.write(message)
        raise _ParserExit(status)

    def error(self, message: str) -> None:
        raise _ArgumentError from None


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    json_output = "--json" in arguments
    try:
        parsed = _build_parser(
            stdout=stdout,
            stderr=stderr,
        ).parse_args(arguments)
    except _ParserExit as result:
        return result.status
    except _ArgumentError:
        return _emit_error(
            "arguments",
            "DB.CONFIGURATION",
            "database arguments are invalid",
            json_output=json_output,
            stdout=stdout,
            stderr=stderr,
            exit_code=EXIT_INCOMPATIBLE,
        )

    command = parsed.command
    try:
        settings = _load_cli_settings(parsed)
        if settings.database.provider != "sqlite":
            raise UnsupportedDatabaseProviderError(
                "database provider is unsupported"
            )
        database_path = settings.database.sqlite_path
        if database_path is None:
            raise DatabaseConfigurationError(
                "database configuration is invalid"
            )

        catalog = _load_production_catalog()
        payload, exit_code = _dispatch(
            parsed,
            database_path=database_path,
            catalog=catalog,
        )
        _emit_payload(
            payload,
            json_output=parsed.json,
            stdout=stdout,
            stderr=stderr,
            error=False,
        )
        return exit_code
    except SettingsError:
        return _emit_error(
            command,
            "DB.CONFIGURATION",
            _SAFE_ERROR_MESSAGES["DB.CONFIGURATION"],
            json_output=parsed.json,
            stdout=stdout,
            stderr=stderr,
            exit_code=EXIT_INCOMPATIBLE,
        )
    except DatabaseError as error:
        return _emit_database_error(
            command,
            error,
            json_output=parsed.json,
            stdout=stdout,
            stderr=stderr,
        )
    except Exception:
        return _emit_error(
            command,
            "DB.OPERATION_FAILED",
            "database operation failed",
            json_output=parsed.json,
            stdout=stdout,
            stderr=stderr,
            exit_code=EXIT_OPERATION_FAILED,
        )


def _build_parser(*, stdout: TextIO, stderr: TextIO) -> argparse.ArgumentParser:
    parser_factory = partial(
        _SafeArgumentParser,
        stdout=stdout,
        stderr=stderr,
    )
    parser = parser_factory(add_help=True)
    parser.add_argument("--config")
    parser.add_argument("--database")
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=parser_factory,
    )
    subparsers.add_parser("status", add_help=True)

    migrate = subparsers.add_parser("migrate", add_help=True)
    migrate.add_argument("--backup-dir", required=True)

    subparsers.add_parser("check", add_help=True)

    backup = subparsers.add_parser("backup", add_help=True)
    backup.add_argument("--output", required=True)

    restore = subparsers.add_parser("verify-restore", add_help=True)
    restore.add_argument("--backup", required=True)
    restore.add_argument("--manifest", required=True)
    restore.add_argument("--target", required=True)
    return parser


def _load_cli_settings(parsed):
    try:
        return load_app_settings(
            config_file=parsed.config,
            sqlite_path=parsed.database,
        )
    except SettingsError:
        raise
    except (json.JSONDecodeError, UnicodeError, OSError):
        raise SettingsError("database configuration is invalid") from None


def _load_production_catalog():
    return load_migration_catalog(_PRODUCTION_MIGRATIONS)


def _dispatch(parsed, *, database_path: Path, catalog):
    if parsed.command in ("status", "check"):
        status = inspect_schema(database_path, catalog)
        return _status_payload(parsed.command, status), _exit_for_schema_status(status)
    if parsed.command == "migrate":
        return _migrate(database_path, Path(parsed.backup_dir), catalog)
    if parsed.command == "backup":
        return _backup(database_path, Path(parsed.output), catalog)
    if parsed.command == "verify-restore":
        return _verify_restore(
            database_path,
            Path(parsed.backup),
            Path(parsed.manifest),
            Path(parsed.target),
            catalog,
        )
    raise DatabaseConfigurationError("database arguments are invalid")


def _migrate(database_path: Path, backup_dir: Path, catalog):
    _require_node_local_path(backup_dir)
    initialize_database(database_path)
    status = inspect_schema(database_path, catalog)
    _raise_for_incompatible_schema(status)

    if status.state is SchemaState.CURRENT:
        details = {
            "previous_version": status.current_version,
            "current_version": status.current_version,
            "applied_count": 0,
            "backup_id": None,
        }
        return _payload("migrate", "current", None, details), EXIT_OK

    if status.state is not SchemaState.PENDING:
        raise DatabaseConfigurationError("database schema is incompatible")

    backup_path = _new_migration_backup_path(backup_dir)
    backup_result = create_sqlite_backup(database_path, backup_path, catalog)
    verified_manifest = verify_backup_artifacts(
        backup_result.backup_path,
        backup_result.manifest_path,
    )
    migration_result = apply_pending_migrations(
        database_path,
        catalog,
        backup_manifest=verified_manifest,
    )
    details = {
        "previous_version": migration_result.previous_version,
        "current_version": migration_result.current_version,
        "applied_count": len(migration_result.applied_versions),
        "backup_id": _backup_identifier(verified_manifest.sha256),
    }
    return _payload("migrate", "current", None, details), EXIT_OK


def _backup(database_path: Path, output_path: Path, catalog):
    _require_node_local_path(output_path)
    result = create_sqlite_backup(database_path, output_path, catalog)
    verified_manifest = verify_backup_artifacts(
        result.backup_path,
        result.manifest_path,
    )
    details = {
        "schema_version": verified_manifest.schema_version,
        "backup_id": _backup_identifier(verified_manifest.sha256),
        "size_bytes": verified_manifest.size_bytes,
    }
    return _payload("backup", "created", None, details), EXIT_OK


def _verify_restore(
    database_path: Path,
    backup_path: Path,
    manifest_path: Path,
    target_path: Path,
    catalog,
):
    _require_node_local_path(backup_path)
    _require_node_local_path(manifest_path)
    _require_node_local_path(target_path)
    result = verify_and_restore_sqlite(
        backup_path,
        manifest_path,
        target_path,
        active_database_path=database_path,
        catalog=catalog,
    )
    details = {
        "schema_version": result.schema_version,
        "backup_id": _backup_identifier(result.sha256),
        "size_bytes": result.size_bytes,
    }
    return _payload("verify-restore", "verified", None, details), EXIT_OK


def _status_payload(command: str, status: SchemaStatus) -> dict[str, Any]:
    details = {
        "current_version": status.current_version,
        "target_version": status.target_version,
        "pending_versions": list(status.pending_versions),
    }
    return _payload(command, status.state.value, status.error_code, details)


def _payload(
    command: str,
    status: str,
    error_code: str | None,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "command": command,
        "status": status,
        "error_code": error_code,
        "details": dict(details),
    }


def _exit_for_schema_status(status: SchemaStatus) -> int:
    if status.state is SchemaState.CURRENT:
        return EXIT_OK
    if status.state in (SchemaState.PENDING, SchemaState.UNINITIALIZED):
        return EXIT_ACTION_REQUIRED
    if status.state in (
        SchemaState.TOO_NEW,
        SchemaState.TAMPERED,
        SchemaState.INVALID_CATALOG,
    ):
        return EXIT_INCOMPATIBLE
    return EXIT_OPERATION_FAILED


def _raise_for_incompatible_schema(status: SchemaStatus) -> None:
    if status.state in (SchemaState.CURRENT, SchemaState.PENDING):
        return
    if status.state is SchemaState.TOO_NEW:
        raise SchemaTooNewError("database schema is incompatible")
    if status.state is SchemaState.TAMPERED:
        raise MigrationChecksumError("migration checksum is invalid")
    if status.state is SchemaState.INVALID_CATALOG:
        raise MigrationCatalogError("migration catalog is invalid")
    raise DatabaseIntegrityError(
        "database integrity check failed"
    )


def _new_migration_backup_path(backup_dir: Path) -> Path:
    timestamp = _utc_filename_timestamp()
    while True:
        token = secrets.token_hex(4)
        candidate = backup_dir / f"m-{timestamp}-{token}.db"
        if not os.path.lexists(candidate) and not os.path.lexists(
            Path(f"{candidate}.manifest.json")
        ):
            return candidate


def _utc_filename_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_identifier(sha256: str) -> str:
    return f"sha256:{sha256[:_BACKUP_SHA_PREFIX_LENGTH]}"


def _require_node_local_path(path: Path) -> None:
    raw = str(path).strip().lower()
    if raw.startswith(("\\\\", "//", "smb:", "nfs:", "afp:")):
        raise DatabaseConfigurationError("operations path must be node-local")


def _emit_database_error(
    command: str,
    error: DatabaseError,
    *,
    json_output: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    if isinstance(error, DatabaseBusyError):
        exit_code = EXIT_BUSY
    elif error.code in _INCOMPATIBLE_ERROR_CODES:
        exit_code = EXIT_INCOMPATIBLE
    else:
        exit_code = EXIT_OPERATION_FAILED
    message = _SAFE_ERROR_MESSAGES.get(error.code, "database operation failed")
    return _emit_error(
        command,
        error.code,
        message,
        json_output=json_output,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
    )


def _emit_error(
    command: str,
    error_code: str,
    message: str,
    *,
    json_output: bool,
    stdout: TextIO,
    stderr: TextIO,
    exit_code: int,
) -> int:
    payload = _payload(command, "error", error_code, {})
    _emit_payload(
        payload,
        json_output=json_output,
        stdout=stdout,
        stderr=stderr,
        error=True,
        message=message,
    )
    return exit_code


def _emit_payload(
    payload: Mapping[str, Any],
    *,
    json_output: bool,
    stdout: TextIO,
    stderr: TextIO,
    error: bool,
    message: str | None = None,
) -> None:
    if json_output:
        json.dump(payload, stdout, ensure_ascii=False, sort_keys=True)
        stdout.write("\n")
        return

    destination = stderr if error else stdout
    if error:
        destination.write(
            f"{payload['command']}: error [{payload['error_code']}] {message}\n"
        )
        return

    destination.write(f"{payload['command']}: {payload['status']}\n")
    if payload["error_code"] is not None:
        destination.write(f"error_code: {payload['error_code']}\n")
    for key, value in payload["details"].items():
        if isinstance(value, list):
            rendered = ",".join(str(item) for item in value)
            value = f"[{rendered}]"
        destination.write(f"{key}: {value}\n")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_ACTION_REQUIRED",
    "EXIT_BUSY",
    "EXIT_INCOMPATIBLE",
    "EXIT_OK",
    "EXIT_OPERATION_FAILED",
    "main",
]
