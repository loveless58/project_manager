import json
from pathlib import Path

import pytest


def test_explicit_missing_config_is_rejected_without_path_leak(tmp_path):
    from platform_core.settings import SettingsError, load_app_settings

    missing = tmp_path / "private" / "missing-settings.json"

    with pytest.raises(
        SettingsError,
        match="configuration file is not a readable regular file",
    ) as raised:
        load_app_settings(config_file=missing, environ={})

    assert str(missing) not in str(raised.value)
    assert raised.value.__cause__ is None


def test_explicit_config_directory_is_rejected_as_non_regular(tmp_path):
    from platform_core.settings import SettingsError, load_app_settings

    config_directory = tmp_path / "private-config"
    config_directory.mkdir()

    with pytest.raises(
        SettingsError,
        match="configuration file is not a readable regular file",
    ) as raised:
        load_app_settings(config_file=config_directory, environ={})

    assert str(config_directory) not in str(raised.value)
    assert raised.value.__cause__ is None


def test_explicit_unreadable_config_is_rejected_without_os_error(
    monkeypatch, tmp_path
):
    import platform_core.settings as settings_module
    from platform_core.settings import SettingsError, load_app_settings

    config = tmp_path / "private-settings.json"
    config.write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == config:
            raise PermissionError(f"permission denied: {config}")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(settings_module.Path, "read_text", read_text)

    with pytest.raises(
        SettingsError,
        match="configuration file is not a readable regular file",
    ) as raised:
        load_app_settings(config_file=config, environ={})

    assert str(config) not in str(raised.value)
    assert raised.value.__cause__ is None


def test_missing_autodiscovery_files_still_use_defaults(monkeypatch, tmp_path):
    import platform_core.settings as settings_module

    monkeypatch.setattr(
        settings_module,
        "DEFAULT_CONFIG_FILE",
        tmp_path / "missing-default.json",
    )
    monkeypatch.setattr(
        settings_module,
        "LEGACY_CONFIG_FILE",
        tmp_path / "missing-legacy.json",
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    settings = settings_module.load_app_settings(config_file=None, environ={})

    assert settings.deployment_mode == "local"
    assert settings.runtime_workspace == (tmp_path / "home" / ".project_manager")


def test_empty_config_argument_disables_autodiscovery(monkeypatch, tmp_path):
    import platform_core.settings as settings_module

    invalid_default = tmp_path / "project-manager.local.json"
    invalid_default.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_FILE", invalid_default)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    settings = settings_module.load_app_settings(config_file="", environ={})

    assert settings.deployment_mode == "local"


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
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
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


def test_explicit_sqlite_path_overrides_environment_and_json(tmp_path):
    config = tmp_path / "project-manager.local.json"
    config.write_text(
        json.dumps({"database": {"sqlite_path": str(tmp_path / "json.sqlite3")}}),
        encoding="utf-8",
    )
    from platform_core.settings import load_app_settings

    settings = load_app_settings(
        config_file=config,
        environ={"PROJECT_MANAGER_SQLITE_PATH": str(tmp_path / "env.sqlite3")},
        runtime_workspace=tmp_path / "runtime",
        business_root=tmp_path / "business",
        sqlite_path=tmp_path / "explicit.sqlite3",
    )

    assert settings.database.sqlite_path == (tmp_path / "explicit.sqlite3").resolve()


def test_explicit_sqlite_path_uses_existing_locality_validation(tmp_path):
    from platform_core.settings import SettingsError, load_app_settings

    business_root = tmp_path / "business"
    with pytest.raises(SettingsError, match="sqlite_path must not be inside business_root"):
        load_app_settings(
            config_file="",
            environ={},
            business_root=business_root,
            runtime_workspace=tmp_path / "runtime",
            sqlite_path=business_root / "state.sqlite3",
        )


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
