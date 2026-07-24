import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from infrastructure.database.contracts import (
    BackupError,
    DatabaseBusyError,
    DatabaseConfigurationError,
    DatabaseIntegrityError,
    MigrationCatalogError,
    MigrationChecksumError,
    MigrationExecutionError,
    RestoreVerificationError,
    SchemaState,
    SchemaStatus,
    SchemaTooNewError,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.backup import (
    authorize_migration_backup,
    create_sqlite_backup,
    verify_backup_artifacts,
    verify_migration_backup,
)
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


def _invoke(*arguments: str):
    from infrastructure.database.cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        ["--config", "", *arguments],
        stdout=stdout,
        stderr=stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


def _json_invoke(*arguments: str):
    code, stdout, stderr = _invoke("--json", *arguments)
    payload = json.loads(stdout or stderr)
    return code, payload, stdout, stderr


def test_json_status_envelope_has_stable_schema_without_creating_database(tmp_path):
    database = tmp_path / "state.sqlite3"

    code, payload, stdout, stderr = _json_invoke(
        "--database", str(database), "status"
    )

    assert code == 2
    assert payload == {
        "schema_version": "database_cli.v1",
        "command": "status",
        "status": "uninitialized",
        "error_code": None,
        "details": {
            "current_version": 0,
            "target_version": 0,
            "pending_versions": [],
        },
    }
    assert stdout
    assert stderr == ""
    assert not database.exists()


@pytest.mark.parametrize("command", ["status", "check"])
def test_read_only_commands_do_not_create_missing_database(tmp_path, command):
    database = tmp_path / "missing.sqlite3"

    code, payload, _, _ = _json_invoke(
        "--database", str(database), command
    )

    assert code == 2
    assert payload["status"] == "uninitialized"
    assert not database.exists()


def test_status_human_output_is_logical_and_redacted(tmp_path):
    database = tmp_path / "private-state.sqlite3"
    initialize_database(database)

    code, stdout, stderr = _invoke("--database", str(database), "status")

    assert code == 0
    assert stderr == ""
    assert "status: current" in stdout
    assert "current_version: 0" in stdout
    assert str(database) not in stdout


def test_check_reports_current_initialized_database(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)

    code, payload, _, _ = _json_invoke(
        "--database", str(database), "check"
    )

    assert code == 0
    assert payload["status"] == "current"
    assert payload["details"] == {
        "current_version": 0,
        "target_version": 0,
        "pending_versions": [],
    }


@pytest.mark.parametrize(
    ("state", "error_code", "expected_exit"),
    [
        (SchemaState.PENDING, None, 2),
        (SchemaState.TOO_NEW, "DB.SCHEMA_TOO_NEW", 3),
        (SchemaState.TAMPERED, "DB.MIGRATION_CHECKSUM", 3),
        (SchemaState.CORRUPT, "DB.INTEGRITY", 5),
    ],
)
def test_status_maps_schema_states_to_stable_exit_categories(
    monkeypatch, tmp_path, state, error_code, expected_exit
):
    from infrastructure.database import cli

    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: SchemaStatus(
            state=state,
            current_version=1,
            target_version=2,
            pending_versions=(2,) if state is SchemaState.PENDING else (),
            error_code=error_code,
        ),
    )

    code, payload, _, _ = _json_invoke(
        "--database", str(tmp_path / "state.sqlite3"), "status"
    )

    assert code == expected_exit
    assert payload["status"] == state.value
    assert payload["error_code"] == error_code


def test_explicit_database_path_has_priority_over_config(monkeypatch, tmp_path):
    from infrastructure.database import cli

    configured = tmp_path / "configured.sqlite3"
    explicit = tmp_path / "explicit.sqlite3"
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps(
            {
                "business_root": str(tmp_path / "business"),
                "runtime_workspace": str(tmp_path / "runtime"),
                "database": {"provider": "sqlite", "sqlite_path": str(configured)},
            }
        ),
        encoding="utf-8",
    )
    seen = []

    def inspect(database_path, catalog):
        seen.append(database_path)
        return SchemaStatus(SchemaState.CURRENT, 0, 0)

    monkeypatch.setattr(cli, "inspect_schema", inspect)
    stdout = io.StringIO()
    code = cli.main(
        ["--config", str(config), "--database", str(explicit), "status"],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 0
    assert seen == [explicit.resolve()]
    assert str(configured) not in stdout.getvalue()
    assert str(explicit) not in stdout.getvalue()


def test_central_postgresql_is_explicitly_unsupported(tmp_path):
    from infrastructure.database.cli import main

    config = tmp_path / "central.json"
    config.write_text(
        json.dumps(
            {
                "deployment_mode": "central",
                "runtime_workspace": str(tmp_path / "runtime"),
                "database": {"provider": "postgresql"},
                "providers": {
                    "document_store": "disabled",
                    "projection_root": str(tmp_path / "projection"),
                },
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    code = main(
        ["--config", str(config), "--json", "status"],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    payload = json.loads(stdout.getvalue())
    assert code == 3
    assert payload["error_code"] == "DB.UNSUPPORTED_PROVIDER"
    assert payload["status"] == "error"
    assert "postgresql" not in json.dumps(payload).lower()


def test_settings_error_is_configuration_failure_without_path_leak(tmp_path):
    secret_path = tmp_path / "secret-business" / "state.sqlite3"
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps(
            {
                "business_root": str(tmp_path / "secret-business"),
                "runtime_workspace": str(tmp_path / "runtime"),
                "database": {"provider": "sqlite", "sqlite_path": str(secret_path)},
            }
        ),
        encoding="utf-8",
    )
    from infrastructure.database.cli import main

    stderr = io.StringIO()
    code = main(
        ["--config", str(config), "status"],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 3
    assert "DB.CONFIGURATION" in stderr.getvalue()
    assert str(secret_path) not in stderr.getvalue()
    assert str(config) not in stderr.getvalue()


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--database", "C:/private/secret.sqlite3", "unknown"],
        ["migrate"],
        ["backup"],
        ["verify-restore", "--backup", "secret.sqlite3"],
    ],
)
def test_argparse_errors_use_injected_stderr_and_are_stable(arguments):
    from infrastructure.database.cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(arguments, stdout=stdout, stderr=stderr)

    assert code == 3
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        "arguments: error [DB.CONFIGURATION] database arguments are invalid\n"
    )
    assert "secret" not in stderr.getvalue()
    assert "usage:" not in stderr.getvalue()


def test_json_argparse_error_uses_stable_envelope():
    from infrastructure.database.cli import main

    stdout = io.StringIO()

    code = main(
        ["--json", "backup"],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == 3
    assert json.loads(stdout.getvalue()) == {
        "schema_version": "database_cli.v1",
        "command": "arguments",
        "status": "error",
        "error_code": "DB.CONFIGURATION",
        "details": {},
    }


def test_migrate_without_pending_versions_creates_no_backup(monkeypatch, tmp_path):
    from infrastructure.database import cli

    database = tmp_path / "state.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    calls = []
    monkeypatch.setattr(
        cli,
        "create_sqlite_backup",
        lambda *args, **kwargs: calls.append("backup"),
    )

    code, payload, _, _ = _json_invoke(
        "--database",
        str(database),
        "migrate",
        "--backup-dir",
        str(backup_dir),
    )

    assert code == 0
    assert calls == []
    assert payload["status"] == "current"
    assert payload["details"] == {
        "previous_version": 0,
        "current_version": 0,
        "applied_count": 0,
        "backup_id": None,
    }


def test_migrate_create_verify_authorize_apply_provenance(
    monkeypatch, tmp_path
):
    from infrastructure.database import cli

    database = tmp_path / "state.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    catalog = load_migration_catalog(FIXTURE_MIGRATIONS)
    events = []
    captured = {}
    real_create = create_sqlite_backup
    real_verify = verify_migration_backup
    real_authorize = authorize_migration_backup
    real_apply = apply_pending_migrations

    monkeypatch.setattr(cli, "_load_production_catalog", lambda: catalog)

    def create(*args, **kwargs):
        events.append("create")
        result = real_create(*args, **kwargs)
        captured["created"] = result
        return result

    def verify(*args, **kwargs):
        events.append("verify")
        verified = real_verify(*args, **kwargs)
        captured["verified"] = verified
        return verified

    def authorize(*args, **kwargs):
        events.append("authorize")
        authorization = real_authorize(*args, **kwargs)
        captured["authorization"] = authorization
        return authorization

    def apply(*args, **kwargs):
        events.append("migrate")
        captured["runner_authorization"] = kwargs["backup_authorization"]
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(cli, "create_sqlite_backup", create)
    monkeypatch.setattr(cli, "verify_migration_backup", verify)
    monkeypatch.setattr(cli, "authorize_migration_backup", authorize)
    monkeypatch.setattr(cli, "apply_pending_migrations", apply)

    code, payload, stdout, stderr = _json_invoke(
        "--database",
        str(database),
        "migrate",
        "--backup-dir",
        str(backup_dir),
    )

    assert code == 0
    assert events == ["create", "verify", "authorize", "migrate"]
    assert captured["runner_authorization"] is captured["authorization"]
    assert captured["authorization"] is not captured["verified"]
    assert captured["verified"] is not captured["created"]
    assert payload["details"]["applied_count"] == 2
    assert payload["details"]["backup_id"].startswith("sha256:")
    assert len(payload["details"]["backup_id"]) == len("sha256:") + 12
    assert str(database) not in stdout + stderr
    assert str(backup_dir) not in stdout + stderr


def test_migrate_does_not_run_when_backup_verification_fails(monkeypatch, tmp_path):
    from infrastructure.database import cli

    database = tmp_path / "state.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    catalog = load_migration_catalog(FIXTURE_MIGRATIONS)
    monkeypatch.setattr(cli, "_load_production_catalog", lambda: catalog)
    monkeypatch.setattr(
        cli,
        "verify_migration_backup",
        lambda *args, **kwargs: (_ for _ in ()).throw(BackupError("safe failure")),
    )
    runner_calls = []
    monkeypatch.setattr(
        cli,
        "apply_pending_migrations",
        lambda *args, **kwargs: runner_calls.append(True),
    )

    code, payload, _, _ = _json_invoke(
        "--database",
        str(database),
        "migrate",
        "--backup-dir",
        str(backup_dir),
    )

    assert code == 5
    assert payload["error_code"] == "DB.BACKUP"
    assert runner_calls == []


@pytest.mark.parametrize(
    "value",
    [r"\\server\share", "smb://server/share", "nfs://server/share", "afp://server/share"],
)
def test_operations_paths_reject_obvious_network_locations(value):
    from infrastructure.database import cli

    with pytest.raises(DatabaseConfigurationError, match="node-local"):
        cli._require_node_local_path(Path(value))



def test_migration_backup_name_avoids_existing_candidate(monkeypatch, tmp_path):
    from infrastructure.database import cli

    database = tmp_path / "state.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    collision = backup_dir / "m-20260725T010203Z-collision.db"
    collision.write_bytes(b"do not overwrite")
    tokens = iter(["collision", "fresh"])
    monkeypatch.setattr(cli, "_utc_filename_timestamp", lambda: "20260725T010203Z")
    monkeypatch.setattr(cli.secrets, "token_hex", lambda size: next(tokens))

    candidate = cli._new_migration_backup_path(backup_dir)

    assert candidate == backup_dir / "m-20260725T010203Z-fresh.db"
    assert collision.read_bytes() == b"do not overwrite"


def test_backup_command_calls_create_and_verification_kernel(monkeypatch, tmp_path):
    from infrastructure.database import cli

    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    output = tmp_path / "snapshot.sqlite3"
    events = []
    real_create = create_sqlite_backup
    real_verify = verify_backup_artifacts

    def create(*args, **kwargs):
        events.append("create")
        return real_create(*args, **kwargs)

    def verify(*args, **kwargs):
        events.append("verify")
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(cli, "create_sqlite_backup", create)
    monkeypatch.setattr(cli, "verify_backup_artifacts", verify)

    code, payload, stdout, stderr = _json_invoke(
        "--database", str(database), "backup", "--output", str(output)
    )

    assert code == 0
    assert events == ["create", "verify"]
    assert payload["status"] == "created"
    assert payload["details"]["backup_id"].startswith("sha256:")
    assert str(database) not in stdout + stderr
    assert str(output) not in stdout + stderr


def test_verify_restore_calls_reviewed_kernel_and_reports_logical_result(
    monkeypatch, tmp_path
):
    from infrastructure.database import cli
    from infrastructure.database.sqlite.restore import RestoreResult

    paths = {
        "database": tmp_path / "active.sqlite3",
        "backup": tmp_path / "backup.sqlite3",
        "manifest": tmp_path / "backup.manifest.json",
        "target": tmp_path / "restored.sqlite3",
    }
    captured = {}

    def restore(backup, manifest, target, *, active_database_path, catalog):
        captured.update(
            backup=backup,
            manifest=manifest,
            target=target,
            active=active_database_path,
            catalog=catalog,
        )
        return RestoreResult(schema_version=3, sha256="a" * 64, size_bytes=500)

    monkeypatch.setattr(cli, "verify_and_restore_sqlite", restore)

    code, payload, stdout, stderr = _json_invoke(
        "--database",
        str(paths["database"]),
        "verify-restore",
        "--backup",
        str(paths["backup"]),
        "--manifest",
        str(paths["manifest"]),
        "--target",
        str(paths["target"]),
    )

    assert code == 0
    assert captured["active"] == paths["database"].resolve()
    assert payload["status"] == "verified"
    assert payload["details"] == {
        "schema_version": 3,
        "backup_id": "sha256:" + "a" * 12,
        "size_bytes": 500,
    }
    for path in paths.values():
        assert str(path) not in stdout + stderr


@pytest.mark.parametrize(
    ("error", "expected_exit"),
    [
        (MigrationCatalogError("safe"), 3),
        (MigrationChecksumError("safe"), 3),
        (SchemaTooNewError("safe"), 3),
        (DatabaseBusyError("safe"), 4),
        (MigrationExecutionError("safe"), 5),
        (DatabaseIntegrityError("safe"), 5),
        (BackupError("safe"), 5),
        (RestoreVerificationError("safe"), 5),
    ],
)
def test_known_database_errors_have_stable_exit_and_error_code(
    monkeypatch, tmp_path, error, expected_exit
):
    from infrastructure.database import cli

    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    code, payload, _, _ = _json_invoke(
        "--database", str(tmp_path / "state.sqlite3"), "status"
    )

    assert code == expected_exit
    assert payload["error_code"] == error.code
    assert payload["details"] == {}


def test_known_error_emits_only_public_message_and_never_cause(monkeypatch, tmp_path):
    from infrastructure.database import cli

    private = str(tmp_path / "private.sqlite3")
    try:
        raise RuntimeError(f"SELECT secret FROM data AT {private}")
    except RuntimeError as cause:
        public = BackupError("database backup failed")
        public.__cause__ = cause

    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: (_ for _ in ()).throw(public),
    )

    code, stdout, stderr = _invoke("--database", private, "status")

    assert code == 5
    assert "database backup failed" in stderr
    assert "SELECT" not in stdout + stderr
    assert "secret" not in stdout + stderr
    assert private not in stdout + stderr


def test_unexpected_error_is_sanitized_at_top_boundary(monkeypatch, tmp_path):
    from infrastructure.database import cli

    private = str(tmp_path / "private.sqlite3")
    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError(f"SELECT password FROM config AT {private}")
        ),
    )

    code, payload, stdout, stderr = _json_invoke(
        "--database", private, "status"
    )

    assert code == 5
    assert payload["error_code"] == "DB.OPERATION_FAILED"
    assert payload["status"] == "error"
    assert "password" not in stdout + stderr
    assert "SELECT" not in stdout + stderr
    assert private not in stdout + stderr


def test_migrate_maps_corrupt_schema_to_integrity_failure(monkeypatch, tmp_path):
    from infrastructure.database import cli

    database = tmp_path / "state.sqlite3"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: SchemaStatus(
            state=SchemaState.CORRUPT,
            current_version=0,
            target_version=0,
            error_code="DB.INTEGRITY",
        ),
    )

    code, payload, _, _ = _json_invoke(
        "--database",
        str(database),
        "migrate",
        "--backup-dir",
        str(backup_dir),
    )

    assert code == 5
    assert payload["error_code"] == "DB.INTEGRITY"


def test_human_schema_failure_includes_stable_error_code(monkeypatch, tmp_path):
    from infrastructure.database import cli

    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: SchemaStatus(
            SchemaState.CORRUPT,
            0,
            0,
            error_code="DB.INTEGRITY",
        ),
    )

    code, stdout, stderr = _invoke(
        "--database", str(tmp_path / "state.sqlite3"), "check"
    )

    assert code == 5
    assert "error_code: DB.INTEGRITY" in stdout
    assert stderr == ""


def test_module_entrypoint_smoke_is_read_only_and_returns_action_required(tmp_path):
    database = tmp_path / "smoke-state.sqlite3"
    environment = os.environ.copy()
    environment.pop("PROJECT_MANAGER_SQLITE_PATH", None)
    environment.pop("PROJECT_MANAGER_DATABASE_PROVIDER", None)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "infrastructure.database.cli",
            "--config",
            "",
            "--database",
            str(database),
            "--json",
            "status",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["schema_version"] == "database_cli.v1"
    assert result.stderr == ""
    assert not database.exists()


@pytest.mark.parametrize(
    ("payload", "json_output"),
    [
        (b'{"database": SECRET', True),
        (b"\xffPRIVATE-CONFIG", False),
    ],
)
def test_configuration_decode_failures_are_incompatible_and_redacted(
    tmp_path, payload, json_output
):
    from infrastructure.database.cli import main

    config = tmp_path / "private-settings.json"
    config.write_bytes(payload)
    stdout = io.StringIO()
    stderr = io.StringIO()
    arguments = ["--config", str(config)]
    if json_output:
        arguments.append("--json")
    arguments.append("status")

    code = main(arguments, stdout=stdout, stderr=stderr)

    rendered = stdout.getvalue() + stderr.getvalue()
    assert code == 3
    assert "DB.CONFIGURATION" in rendered
    assert str(config) not in rendered
    assert "SECRET" not in rendered
    assert "PRIVATE-CONFIG" not in rendered
    assert "Expecting" not in rendered


def test_explicit_missing_config_stops_migrate_before_creating_artifacts(tmp_path):
    from infrastructure.database.cli import main

    operation_root = tmp_path / "must-not-be-created"
    config = operation_root / "private-missing-settings.json"
    database = operation_root / "state" / "state.sqlite3"
    backup_dir = operation_root / "backups"
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        [
            "--config",
            str(config),
            "--database",
            str(database),
            "--json",
            "migrate",
            "--backup-dir",
            str(backup_dir),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    payload = json.loads(stdout.getvalue())
    assert code == 3
    assert payload["error_code"] == "DB.CONFIGURATION"
    assert not operation_root.exists()
    assert str(config) not in stdout.getvalue() + stderr.getvalue()
    assert str(database) not in stdout.getvalue() + stderr.getvalue()
    assert str(backup_dir) not in stdout.getvalue() + stderr.getvalue()


def test_configuration_directory_read_failure_is_incompatible_and_redacted(tmp_path):
    from infrastructure.database.cli import main

    config_directory = tmp_path / "private-config-directory"
    config_directory.mkdir()
    stdout = io.StringIO()

    code = main(
        ["--config", str(config_directory), "--json", "status"],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    payload = json.loads(stdout.getvalue())
    assert code == 3
    assert payload["error_code"] == "DB.CONFIGURATION"
    assert str(config_directory) not in stdout.getvalue()


def test_configuration_permission_failure_is_sanitized(monkeypatch, tmp_path):
    from infrastructure.database import cli

    config = tmp_path / "private-config.json"
    monkeypatch.setattr(
        cli,
        "load_app_settings",
        lambda **kwargs: (_ for _ in ()).throw(
            PermissionError(f"permission denied: {config}")
        ),
    )
    stderr = io.StringIO()

    code = cli.main(
        ["--config", str(config), "status"],
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert code == 3
    assert "DB.CONFIGURATION" in stderr.getvalue()
    assert str(config) not in stderr.getvalue()
    assert "permission denied" not in stderr.getvalue()


def test_database_os_error_after_settings_is_operation_failure(monkeypatch, tmp_path):
    from infrastructure.database import cli

    database = tmp_path / "private-state.sqlite3"
    monkeypatch.setattr(
        cli,
        "inspect_schema",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError(f"database read failed: {database}")
        ),
    )

    code, payload, stdout, stderr = _json_invoke(
        "--database", str(database), "status"
    )

    assert code == 5
    assert payload["error_code"] == "DB.OPERATION_FAILED"
    assert str(database) not in stdout + stderr


@pytest.mark.parametrize(
    ("arguments", "expected_fragment"),
    [
        (["--help"], "{status,migrate,check,backup,verify-restore}"),
        (["migrate", "--help"], "--backup-dir"),
    ],
)
def test_help_uses_injected_stdout_without_terminating_main(
    capsys, arguments, expected_fragment
):
    from infrastructure.database.cli import main

    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(arguments, stdout=stdout, stderr=stderr)

    captured = capsys.readouterr()
    assert code == 0
    assert "usage:" in stdout.getvalue()
    assert expected_fragment in stdout.getvalue()
    assert stderr.getvalue() == ""
    assert captured.out == ""
    assert captured.err == ""


def test_module_help_returns_zero_and_writes_only_stdout(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)

    result = subprocess.run(
        [sys.executable, "-m", "infrastructure.database.cli", "migrate", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert "--backup-dir" in result.stdout
    assert result.stderr == ""
