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
    @unittest.skipUnless(sys.platform == "win32", "Windows-only default paths")
    def test_defaults_to_synology_workspace(self):
        with patch.dict(os.environ, {}, clear=True):
            from common.workspace_config import resolve_workspace_config

            config = resolve_workspace_config(config_file="")

        self.assertEqual(config.business_root, Path(r"E:\SynologyDrive"))
        self.assertEqual(config.runtime_workspace, Path(r"E:\SynologyDrive\_project_manager_workspace"))
        self.assertEqual(config.project_files_dir, config.business_root / "项目文件")
        self.assertEqual(config.data_cleaning_workspace, config.runtime_workspace / "数据清洗工作台")
        self.assertEqual(config.opportunity_dir, config.runtime_workspace / "新机会与线索")
        self.assertEqual(config.logs_dir, config.runtime_workspace / "logs")
        self.assertEqual(config.state_dir, config.runtime_workspace / "state")
        self.assertFalse(str(config.project_files_dir).startswith(str(config.runtime_workspace)))

    @unittest.skipUnless(sys.platform == "win32", "Windows-only default paths")
    def test_environment_overrides_business_root_and_workspace(self):
        env = {
            "PROJECT_MANAGER_BUSINESS_ROOT": r"D:\BusinessRoot",
            "PROJECT_MANAGER_WORKSPACE_DIR": r"D:\BusinessRoot\.pm_workspace",
        }
        with patch.dict(os.environ, env, clear=True):
            from common.workspace_config import resolve_workspace_config

            config = resolve_workspace_config(config_file="")

        self.assertEqual(config.business_root, Path(r"D:\BusinessRoot"))
        self.assertEqual(config.runtime_workspace, Path(r"D:\BusinessRoot\.pm_workspace"))
        self.assertEqual(config.project_files_dir, Path(r"D:\BusinessRoot\项目文件"))

    @unittest.skipUnless(sys.platform == "win32", "Windows-only default paths")
    def test_explicit_arguments_override_environment(self):
        env = {
            "PROJECT_MANAGER_BUSINESS_ROOT": r"D:\EnvRoot",
            "PROJECT_MANAGER_WORKSPACE_DIR": r"D:\EnvWorkspace",
        }
        with patch.dict(os.environ, env, clear=True):
            from common.workspace_config import resolve_workspace_config

            config = resolve_workspace_config(
                business_root=r"E:\ExplicitRoot",
                runtime_workspace=r"E:\ExplicitWorkspace",
                config_file="",
            )

        self.assertEqual(config.business_root, Path(r"E:\ExplicitRoot"))
        self.assertEqual(config.runtime_workspace, Path(r"E:\ExplicitWorkspace"))
        self.assertEqual(config.project_files_dir, Path(r"E:\ExplicitRoot\项目文件"))

    @unittest.skipUnless(sys.platform == "win32", "Windows-only default paths")
    def test_repo_local_config_is_used_when_environment_is_absent(self):
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "workspace.local.json"
            config_path.write_text(
                json.dumps(
                    {
                        "business_root": r"F:\ConfiguredBusiness",
                        "runtime_workspace": r"F:\ConfiguredBusiness\_workspace",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                from common.workspace_config import resolve_workspace_config

                config = resolve_workspace_config(config_file=str(config_path))

        self.assertEqual(config.business_root, Path(r"F:\ConfiguredBusiness"))
        self.assertEqual(config.runtime_workspace, Path(r"F:\ConfiguredBusiness\_workspace"))
        self.assertEqual(config.project_files_dir, Path(r"F:\ConfiguredBusiness\项目文件"))

    def test_paths_expand_and_are_absolute(self):
        with patch.dict(os.environ, {}, clear=True):
            from common.workspace_config import resolve_workspace_config

            config = resolve_workspace_config(
                business_root="~/BusinessRoot",
                runtime_workspace="~/BusinessRoot/workspace",
                config_file="",
            )

        self.assertTrue(config.business_root.is_absolute())
        self.assertTrue(config.runtime_workspace.is_absolute())

    @unittest.skipUnless(sys.platform == "win32", "Windows-only default paths")
    def test_data_cleaning_default_scan_source_is_business_root(self):
        env = {
            "PROJECT_MANAGER_BUSINESS_ROOT": r"D:\BusinessRoot",
            "PROJECT_MANAGER_WORKSPACE_DIR": r"D:\BusinessRoot\_workspace",
        }
        with patch.dict(os.environ, env, clear=True):
            from tools.data_cleaning_tools import DataCleaningTools

            tools = DataCleaningTools()

        self.assertEqual(tools.raw_dir, r"D:\BusinessRoot")
        self.assertEqual(tools.workspace_dir, r"D:\BusinessRoot\_workspace\数据清洗工作台")

    def test_explicit_data_cleaning_workspace_keeps_isolated_raw_dir(self):
        with tempfile.TemporaryDirectory() as td:
            from tools.data_cleaning_tools import DataCleaningTools

            tools = DataCleaningTools(workspace_dir=td)

            self.assertEqual(tools.raw_dir, os.path.join(td, "00-原始文件（待处理）"))


if __name__ == "__main__":
    unittest.main()
