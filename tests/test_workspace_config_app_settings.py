import json
import os
from pathlib import Path
from unittest.mock import patch


def test_explicit_app_settings_are_not_overridden_by_environment_or_config(tmp_path):
    from common.workspace_config import resolve_workspace_config
    from platform_core.settings import load_app_settings

    injected_business_root = tmp_path / "injected-business"
    injected_runtime_workspace = tmp_path / "injected-runtime"
    injected_settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(injected_business_root),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(injected_runtime_workspace),
        },
    )
    config_file = tmp_path / "conflicting.json"
    config_file.write_text(
        json.dumps(
            {
                "business_root": str(tmp_path / "config-business"),
                "runtime_workspace": str(tmp_path / "config-runtime"),
            }
        ),
        encoding="utf-8",
    )

    with patch.dict(
        os.environ,
        {
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "environment-business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "environment-runtime"),
        },
        clear=False,
    ):
        config = resolve_workspace_config(
            config_file=config_file,
            app_settings=injected_settings,
        )

    assert config.business_root == injected_business_root.resolve()
    assert config.runtime_workspace == injected_runtime_workspace.resolve()
    assert config.project_files_dir == injected_business_root.resolve() / "项目文件"
    assert config.data_cleaning_workspace == injected_runtime_workspace.resolve() / "数据清洗工作台"
    assert config.opportunity_dir == injected_runtime_workspace.resolve() / "新机会与线索"
