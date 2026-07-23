import json
from pathlib import Path

import pytest


def test_portable_local_defaults_do_not_contain_windows_drive(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from platform_core.settings import load_app_settings

    settings = load_app_settings(config_file="", environ={})

    assert settings.deployment_mode == "local"
    assert settings.database.provider == "sqlite"
    assert settings.business_root == tmp_path / "ProjectManagerData"
    assert settings.runtime_workspace == tmp_path / ".project_manager"
    assert settings.database.sqlite_path == (
        tmp_path / ".project_manager" / "state" / "project_manager.sqlite3"
    )
    assert not settings.runtime_workspace.is_relative_to(settings.business_root)
    assert "E:\\" not in str(settings.business_root)


def test_business_root_may_be_sync_directory_while_defaults_stay_node_local(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "node-home"))
    from platform_core.settings import load_app_settings

    settings = load_app_settings(
        config_file="",
        environ={"PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "shared-business")},
    )

    assert settings.business_root == (tmp_path / "shared-business").resolve()
    assert settings.runtime_workspace == (tmp_path / "node-home" / ".project_manager").resolve()
    assert settings.database.sqlite_path == (
        tmp_path / "node-home" / ".project_manager" / "state" / "project_manager.sqlite3"
    ).resolve()


@pytest.mark.parametrize(
    ("sqlite_path", "message"),
    [
        ("inside", "sqlite_path must not be inside business_root"),
        (r"\\server\share\project_manager.sqlite3", "sqlite_path must be node-local"),
    ],
)
def test_local_sqlite_rejects_business_or_unc_paths(tmp_path, sqlite_path, message):
    from platform_core.settings import SettingsError, load_app_settings

    business_root = tmp_path / "business"
    configured_sqlite = (
        business_root / "runtime" / "project_manager.sqlite3"
        if sqlite_path == "inside"
        else sqlite_path
    )

    with pytest.raises(SettingsError, match=message):
        load_app_settings(
            config_file="",
            environ={
                "PROJECT_MANAGER_BUSINESS_ROOT": str(business_root),
                "PROJECT_MANAGER_SQLITE_PATH": str(configured_sqlite),
            },
        )


def test_central_postgresql_does_not_apply_sqlite_locality_rules(tmp_path):
    from platform_core.settings import load_app_settings

    business_root = tmp_path / "business"
    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
            "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            "PROJECT_MANAGER_BUSINESS_ROOT": str(business_root),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(business_root / "runtime"),
            "PROJECT_MANAGER_SQLITE_PATH": str(business_root / "ignored.sqlite3"),
        },
    )

    assert settings.database.provider == "postgresql"
    assert settings.database.sqlite_path == (business_root / "ignored.sqlite3").resolve()


def test_environment_overrides_json_and_defaults(tmp_path):
    config_file = tmp_path / "project-manager.local.json"
    config_file.write_text(
        json.dumps(
            {
                "deployment_mode": "local",
                "business_root": str(tmp_path / "json-business"),
                "runtime_workspace": str(tmp_path / "json-runtime"),
            }
        ),
        encoding="utf-8",
    )
    from platform_core.settings import load_app_settings

    settings = load_app_settings(
        config_file=config_file,
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "env-business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "env-runtime"),
        },
    )

    assert settings.business_root == (tmp_path / "env-business").resolve()
    assert settings.runtime_workspace == (tmp_path / "env-runtime").resolve()


def test_central_profile_selects_postgresql_without_reading_secret(tmp_path):
    from platform_core.settings import load_app_settings

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            "PROJECT_MANAGER_DATABASE_DSN_ENV": "PM_DATABASE_DSN",
            "PM_DATABASE_DSN": "postgresql://secret-user:secret-pass@nas/project_manager",
        },
    )

    assert settings.database.provider == "postgresql"
    assert settings.database.dsn_env_var == "PM_DATABASE_DSN"
    assert "secret-pass" not in repr(settings)
    assert "secret-pass" not in json.dumps(settings.redacted_summary())


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        (
            {
                "PROJECT_MANAGER_DEPLOYMENT_MODE": "local",
                "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            },
            "local deployment requires sqlite",
        ),
        (
            {
                "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
                "PROJECT_MANAGER_DATABASE_PROVIDER": "sqlite",
            },
            "central deployment requires postgresql",
        ),
        (
            {"PROJECT_MANAGER_STRUCTURE_INDEX": "pageindex"},
            "pageindex_dir is required",
        ),
    ],
)
def test_invalid_settings_fail_with_stable_message(environ, message):
    from platform_core.settings import SettingsError, load_app_settings

    with pytest.raises(SettingsError, match=message):
        load_app_settings(config_file="", environ=environ)
