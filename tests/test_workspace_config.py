#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TEST_DIR)
sys.path.insert(0, PROJECT_DIR)


class TestWorkspaceConfig(unittest.TestCase):
    def test_defaults_to_portable_home_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(Path, "home", classmethod(lambda cls: Path(td))):
                with patch.dict(os.environ, {}, clear=True):
                    from common.workspace_config import resolve_workspace_config

                    config = resolve_workspace_config(config_file="")

        self.assertEqual(config.business_root, Path(td) / "ProjectManagerData")
        self.assertEqual(config.runtime_workspace, Path(td) / "ProjectManagerData" / ".project_manager")
        self.assertEqual(config.project_files_dir, config.business_root / "项目文件")
        self.assertEqual(config.data_cleaning_workspace, config.runtime_workspace / "数据清洗工作台")
        self.assertEqual(config.opportunity_dir, config.runtime_workspace / "新机会与线索")
        self.assertEqual(config.logs_dir, config.runtime_workspace / "logs")
        self.assertEqual(config.state_dir, config.runtime_workspace / "state")
        self.assertFalse(str(config.project_files_dir).startswith(str(config.runtime_workspace)))

    def test_environment_overrides_business_root_and_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            business_root = Path(td) / "business"
            runtime_workspace = Path(td) / "runtime"
            env = {
                "PROJECT_MANAGER_BUSINESS_ROOT": str(business_root),
                "PROJECT_MANAGER_WORKSPACE_DIR": str(runtime_workspace),
                "HOME": td,
                "USERPROFILE": td,
            }
            with patch.dict(os.environ, env, clear=True):
                from common.workspace_config import resolve_workspace_config

                config = resolve_workspace_config(config_file="")

        self.assertEqual(config.business_root, business_root.resolve())
        self.assertEqual(config.runtime_workspace, runtime_workspace.resolve())
        self.assertEqual(config.project_files_dir, business_root.resolve() / "项目文件")

    def test_explicit_arguments_override_environment(self):
        with tempfile.TemporaryDirectory() as td:
            env = {
                "PROJECT_MANAGER_BUSINESS_ROOT": str(Path(td) / "env-business"),
                "PROJECT_MANAGER_WORKSPACE_DIR": str(Path(td) / "env-runtime"),
                "HOME": td,
                "USERPROFILE": td,
            }
            business_root = Path(td) / "explicit-business"
            runtime_workspace = Path(td) / "explicit-runtime"
            with patch.dict(os.environ, env, clear=True):
                from common.workspace_config import resolve_workspace_config

                config = resolve_workspace_config(
                    business_root=business_root,
                    runtime_workspace=runtime_workspace,
                    config_file="",
                )

        self.assertEqual(config.business_root, business_root.resolve())
        self.assertEqual(config.runtime_workspace, runtime_workspace.resolve())
        self.assertEqual(config.project_files_dir, business_root.resolve() / "项目文件")

    def test_repo_local_config_is_used_when_environment_is_absent(self):
        with tempfile.TemporaryDirectory() as td:
            business_root = Path(td) / "configured-business"
            runtime_workspace = Path(td) / "configured-runtime"
            config_path = Path(td) / "workspace.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "business_root": str(business_root),
                        "runtime_workspace": str(runtime_workspace),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HOME": td, "USERPROFILE": td}, clear=True):
                from common.workspace_config import resolve_workspace_config

                config = resolve_workspace_config(config_file=str(config_path))

        self.assertEqual(config.business_root, business_root.resolve())
        self.assertEqual(config.runtime_workspace, runtime_workspace.resolve())
        self.assertEqual(config.project_files_dir, business_root.resolve() / "项目文件")

    def test_paths_expand_and_are_absolute(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"HOME": td, "USERPROFILE": td}, clear=True):
                from common.workspace_config import resolve_workspace_config

                config = resolve_workspace_config(
                    business_root="~/BusinessRoot",
                    runtime_workspace="~/BusinessRoot/workspace",
                    config_file="",
                )

        self.assertTrue(config.business_root.is_absolute())
        self.assertTrue(config.runtime_workspace.is_absolute())

    def test_data_cleaning_default_scan_source_is_business_root(self):
        with tempfile.TemporaryDirectory() as td:
            business_root = Path(td) / "business"
            runtime_workspace = Path(td) / "runtime"
            env = {
                "PROJECT_MANAGER_BUSINESS_ROOT": str(business_root),
                "PROJECT_MANAGER_WORKSPACE_DIR": str(runtime_workspace),
                "HOME": td,
                "USERPROFILE": td,
            }
            with patch.dict(os.environ, env, clear=True):
                from tools.data_cleaning_tools import DataCleaningTools

                tools = DataCleaningTools()

        self.assertEqual(tools.raw_dir, str(business_root.resolve()))
        self.assertEqual(tools.workspace_dir, str(runtime_workspace.resolve() / "数据清洗工作台"))

    def test_explicit_data_cleaning_workspace_keeps_isolated_raw_dir(self):
        with tempfile.TemporaryDirectory() as td:
            from tools.data_cleaning_tools import DataCleaningTools

            tools = DataCleaningTools(workspace_dir=td)

            self.assertEqual(tools.raw_dir, os.path.join(td, "00-原始文件（待处理）"))


if __name__ == "__main__":
    unittest.main()
