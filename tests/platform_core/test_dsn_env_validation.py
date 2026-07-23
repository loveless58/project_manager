import json

import pytest


@pytest.mark.parametrize("source", ["environment", "json"])
def test_database_dsn_env_var_rejects_non_environment_variable_name(tmp_path, source):
    from platform_core.settings import SettingsError, load_app_settings

    invalid_dsn_value = "postgresql://invalid-sentinel"
    config_file = ""
    environ = {
        "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
        "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
        "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
    }
    if source == "environment":
        environ["PROJECT_MANAGER_DATABASE_DSN_ENV"] = invalid_dsn_value
    else:
        config_file = tmp_path / "project-manager.local.json"
        config_file.write_text(
            json.dumps(
                {
                    "deployment_mode": "central",
                    "runtime_workspace": str(tmp_path / "runtime"),
                    "database": {
                        "provider": "postgresql",
                        "dsn_env_var": invalid_dsn_value,
                    },
                }
            ),
            encoding="utf-8",
        )
        environ = {}

    with pytest.raises(
        SettingsError,
        match="database_dsn_env_var must be a valid environment variable name",
    ):
        load_app_settings(config_file=config_file, environ=environ)
