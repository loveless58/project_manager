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
    assert settings.runtime_workspace == tmp_path / "ProjectManagerData" / ".project_manager"
    assert "E:\\" not in str(settings.business_root)


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
