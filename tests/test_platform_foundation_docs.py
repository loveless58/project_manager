import importlib
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_example_config_matches_portable_foundation_contract():
    payload = json.loads(
        (PROJECT_DIR / "config" / "project-manager.example.json").read_text(encoding="utf-8")
    )
    assert payload["deployment_mode"] == "local"
    assert payload["database"]["provider"] == "sqlite"
    assert payload["providers"]["structure_index"] == "disabled"
    assert "password" not in json.dumps(payload).lower()
    assert "E:\\" not in json.dumps(payload)


def test_directory_contract_points_to_new_settings_source():
    payload = json.loads(
        (PROJECT_DIR / "governance" / "directory_contract.json").read_text(encoding="utf-8")
    )
    assert payload["version"] == "3.0.0"
    assert payload["config_source"] == "platform_core.settings.load_app_settings"
    assert "E:\\SynologyDrive" not in json.dumps(payload)
    assert "common.workspace_config" not in json.dumps(payload)


def test_local_secret_config_is_ignored():
    ignore_text = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "config/project-manager.local.json" in ignore_text
    assert "config/workspace.local.json" in ignore_text


def test_readme_documents_portable_deployment_and_rebuildable_views():
    readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")
    assert "Windows、macOS 和群晖挂载路径都通过本地配置提供" in readme
    assert "SQLite" in readme
    assert "PostgreSQL 17" in readme
    assert "异地执行节点" in readme
    assert "JSON / Markdown" in readme
    assert "投影视图" in readme
    assert "PROJECT_MANAGER_DATABASE_DSN" in readme


def test_legacy_workspace_prd_is_marked_as_superseded():
    prd = (
        PROJECT_DIR / "docs" / "prd" / "2026-07-03-workspace-config-cloud-drive.md"
    ).read_text(encoding="utf-8")
    assert "状态：已被" in prd
    assert "跨平台部署设计取代" in prd
    assert "不再是代码默认值" in prd


def test_directory_governance_uses_dynamic_platform_settings(monkeypatch, tmp_path):
    runtime_workspace = tmp_path / "runtime-workspace"
    monkeypatch.setenv("LOOP_PROJECT_BASE_DIR", str(runtime_workspace))

    import governance.validate as governance_validate

    governance_validate = importlib.reload(governance_validate)
    errors, warnings, findings = governance_validate.validate_dirs(verbose=False)

    assert errors == 0
    assert warnings == 0
    assert any(str(runtime_workspace) in finding["msg"] for finding in findings)
    source = (PROJECT_DIR / "governance" / "validate.py").read_text(encoding="utf-8")
    assert "load_app_settings" in source
    assert "from common.workspace_config import resolve_workspace_config" not in source
    assert "E:\\SynologyDrive" not in source


def test_tool_governance_matches_the_explicit_runtime_registry():
    import governance.validate as governance_validate

    governance_validate = importlib.reload(governance_validate)
    errors, warnings, findings = governance_validate.validate_tools(verbose=False)

    assert errors == 0, findings
    assert warnings == 0, findings
