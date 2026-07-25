from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from infrastructure.database.cli import EXIT_INCOMPATIBLE, main as database_cli
from platform_core.settings import SettingsError, load_app_settings


def _write_raw_config(tmp_path: Path, serialized: str) -> Path:
    config = tmp_path / "project-manager.local.json"
    config.write_text(serialized, encoding="utf-8")
    return config


@pytest.mark.parametrize(
    "serialized",
    [
        '{"deployment_mode":"central","deployment_mode":"local"}',
        (
            '{"database":{"provider":"sqlite","sqlite_path":"first.sqlite3",'
            '"sqlite_path":"second.sqlite3"}}'
        ),
        (
            '{"providers":{"structure_index":"pageindex",'
            '"structure_index":"disabled"}}'
        ),
        (
            '{"runtime_workspace":"first-runtime",'
            '"runtime_workspace":"second-runtime"}'
        ),
    ],
)
def test_json_duplicate_keys_fail_closed_at_every_object_depth(
    tmp_path: Path,
    serialized: str,
) -> None:
    config = _write_raw_config(tmp_path, serialized)

    with pytest.raises(SettingsError, match="duplicate"):
        load_app_settings(config_file=config, environ={})


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonstandard_json_constants_are_rejected_as_settings_errors(
    tmp_path: Path,
    constant: str,
) -> None:
    config = _write_raw_config(
        tmp_path,
        '{"runtime_workspace":' + constant + "}",
    )

    with pytest.raises(SettingsError, match="invalid JSON") as raised:
        load_app_settings(config_file=config, environ={})

    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "payload",
    [
        {"deployment_mode": ["local"]},
        {"business_root": {}},
        {"runtime_workspace": False},
        {"database": {"provider": 1}},
        {"database": {"sqlite_path": []}},
        {"database": {"dsn_env_var": {"name": "DATABASE_DSN"}}},
        {"providers": {"document_store": []}},
        {"providers": {"structure_index": 1}},
        {"providers": {"pageindex_dir": False}},
        {"providers": {"projection_writer": {}}},
        {"providers": {"projection_root": []}},
    ],
)
def test_json_leaf_type_errors_are_stable_settings_errors(
    tmp_path: Path,
    payload: object,
) -> None:
    config = _write_raw_config(tmp_path, json.dumps(payload))

    with pytest.raises(SettingsError, match="configuration") as raised:
        load_app_settings(config_file=config, environ={})

    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("environ", "explicit"),
    [
        ({"PROJECT_MANAGER_WORKSPACE_DIR": 7}, {}),
        ({"PROJECT_MANAGER_DEPLOYMENT_MODE": ["local"]}, {}),
        ({"PROJECT_MANAGER_DATABASE_DSN_ENV": False}, {}),
        ({}, {"runtime_workspace": []}),
        ({}, {"sqlite_path": {"path": "state.sqlite3"}}),
    ],
)
def test_non_string_environment_or_explicit_leaf_is_settings_error(
    environ: dict[str, object],
    explicit: dict[str, object],
) -> None:
    with pytest.raises(SettingsError):
        load_app_settings(config_file="", environ=environ, **explicit)


def test_cli_migrate_rejects_duplicate_sqlite_path_without_touching_default(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    intended = tmp_path / "intended.sqlite3"
    config = _write_raw_config(
        tmp_path,
        (
            "{"
            '"deployment_mode":"local",'
            f'"business_root":{json.dumps(str(tmp_path / "business"))},'
            f'"runtime_workspace":{json.dumps(str(runtime))},'
            '"database":{"provider":"sqlite",'
            f'"sqlite_path":{json.dumps(str(intended))},'
            '"sqlite_path":null},'
            '"providers":{"document_store":"local",'
            '"structure_index":"disabled",'
            '"projection_writer":"filesystem",'
            f'"projection_root":{json.dumps(str(runtime / "projections"))}'
            "}"
            "}"
        ),
    )
    stdout = io.StringIO()

    code = database_cli(
        [
            "--config",
            str(config),
            "--json",
            "migrate",
            "--backup-dir",
            str(runtime / "backups"),
        ],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert not intended.exists()
    assert not (runtime / "state" / "project_manager.sqlite3").exists()
    assert code == EXIT_INCOMPATIBLE
    assert json.loads(stdout.getvalue())["error_code"] == "DB.CONFIGURATION"
