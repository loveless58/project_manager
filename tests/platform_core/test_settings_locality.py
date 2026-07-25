from pathlib import Path

import pytest


def _environment(tmp_path: Path, mode: str) -> dict[str, str]:
    environment = {
        "PROJECT_MANAGER_DEPLOYMENT_MODE": mode,
        "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
        "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
        "PROJECT_MANAGER_PROJECTION_ROOT": str(tmp_path / "projection"),
    }
    if mode == "central":
        environment["PROJECT_MANAGER_DATABASE_PROVIDER"] = "postgresql"
    return environment


@pytest.mark.parametrize("mode", ["local", "central"])
@pytest.mark.parametrize("field_name", ["runtime_workspace", "projection_root"])
@pytest.mark.parametrize("placement", ["equal", "descendant"])
def test_settings_reject_runtime_artifacts_in_business_root(
    tmp_path, mode, field_name, placement
):
    from platform_core.settings import SettingsError, load_app_settings

    environment = _environment(tmp_path, mode)
    business_root = tmp_path / "business"
    candidate = business_root if placement == "equal" else business_root / "runtime"
    environment[
        "PROJECT_MANAGER_WORKSPACE_DIR"
        if field_name == "runtime_workspace"
        else "PROJECT_MANAGER_PROJECTION_ROOT"
    ] = str(candidate)

    with pytest.raises(
        SettingsError,
        match=rf"{field_name} must not be inside business_root",
    ):
        load_app_settings(config_file="", environ=environment)


@pytest.mark.parametrize("mode", ["local", "central"])
@pytest.mark.parametrize(
    ("field_name", "environment_name", "network_path"),
    [
        (
            "runtime_workspace",
            "PROJECT_MANAGER_WORKSPACE_DIR",
            r"\\server\share\runtime",  # repo-hygiene: allow=synthetic-path
        ),
        (
            "projection_root",
            "PROJECT_MANAGER_PROJECTION_ROOT",
            "smb://server/share/projections",
        ),
        (
            "projection_root",
            "PROJECT_MANAGER_PROJECTION_ROOT",
            "nfs://server/export/projections",
        ),
        (
            "runtime_workspace",
            "PROJECT_MANAGER_WORKSPACE_DIR",
            "afp://server/share/runtime",
        ),
    ],
)
def test_settings_reject_obvious_network_runtime_locations(
    tmp_path, mode, field_name, environment_name, network_path
):
    from platform_core.settings import SettingsError, load_app_settings

    environment = _environment(tmp_path, mode)
    environment[environment_name] = network_path

    with pytest.raises(SettingsError, match=rf"{field_name} must be node-local"):
        load_app_settings(config_file="", environ=environment)


@pytest.mark.parametrize("mode", ["local", "central"])
def test_settings_accept_node_local_runtime_and_projection_siblings(tmp_path, mode):
    from platform_core.settings import load_app_settings

    environment = _environment(tmp_path, mode)

    settings = load_app_settings(config_file="", environ=environment)

    assert settings.runtime_workspace == (tmp_path / "runtime").resolve()
    assert settings.providers.projection_root == (tmp_path / "projection").resolve()
