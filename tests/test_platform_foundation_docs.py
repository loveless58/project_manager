from copy import deepcopy
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
    assert payload["business_root_default"] == "${HOME}/ProjectManagerData"
    assert payload["runtime_workspace_default"] == "${HOME}/.project_manager"
    assert payload["base_dir_default"] == payload["runtime_workspace_default"]
    assert payload["compatibility_facade"] == (
        "common.workspace_config.resolve_workspace_config"
    )
    assert "E:\\SynologyDrive" not in json.dumps(payload)
    assert "compatibility facade" in payload["description"]
    assert all(
        "缺失仅记录为 info，不阻塞命令执行" in spec["description"]
        for spec in payload["required_dirs"].values()
    )


def test_local_secret_config_is_ignored():
    ignore_text = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "config/project-manager.local.json" in ignore_text
    assert "config/workspace.local.json" in ignore_text


def test_readme_separates_current_foundation_from_planned_central_architecture():
    readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")
    assert "## 当前阶段已实现" in readme
    assert "## 规划中：中心化生产部署（Phase 2+）" in readme
    assert "PostgreSQL Repository、连接/连接池、迁移、Unit of Work" in readme
    assert "权威 SQL 状态持久化、控制平面 API、异地执行节点的注册、领取和提交协议均尚未实现" in readme
    assert "当前版本不能据此部署生产 central 环境" in readme
    assert "Windows、macOS 和群晖挂载路径都通过本地配置提供" in readme
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


def test_tool_reference_uses_app_settings_and_marks_legacy_facade():
    reference = (PROJECT_DIR / "references" / "project_tools.md").read_text(
        encoding="utf-8"
    )

    assert "AppSettings.business_root" in reference
    assert "AppSettings.runtime_workspace" in reference
    assert "common.workspace_config" in reference
    assert "兼容门面" in reference
    assert "~/.project_manager" in reference
    assert "production default workspace" not in reference
    assert "~/Desktop/工作文件" not in reference
    assert "E:\\SynologyDrive" not in reference


def test_main_docstring_describes_current_settings_and_node_local_runtime():
    import ast

    source = (PROJECT_DIR / "main.py").read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(source)) or ""

    assert "AppSettings" in docstring
    assert "节点本地" in docstring
    assert "Kimi Work" not in docstring
    assert "PythonRun" not in docstring


def test_runtime_artifact_policy_documents_locality_and_mapped_drive_limit():
    policy = (
        PROJECT_DIR / "docs" / "operations" / "2026-07-05-runtime-artifact-policy.md"
    ).read_text(encoding="utf-8")

    assert "runtime_workspace" in policy
    assert "projection_root" in policy
    assert "business_root" in policy
    assert "UNC" in policy
    assert "SMB" in policy and "NFS" in policy and "AFP" in policy
    assert "映射盘" in policy
    assert "无法仅凭盘符" in policy


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


def test_tool_governance_rejects_parameter_and_required_drift():
    import governance.validate as governance_validate
    import main

    governance_validate = importlib.reload(governance_validate)
    contract = governance_validate.load_contract("project_schema.json")
    drifted_contract = deepcopy(contract)
    drifted_contract["tools"]["scan_projects"]["params"] = {
        "unexpected": {"type": "string"}
    }
    drifted_contract["tools"]["read_project_record"]["required"] = []

    errors, warnings, findings = governance_validate.validate_tool_contract(
        drifted_contract,
        main._build_registry(),
        verbose=False,
    )

    assert warnings == 0
    assert errors >= 2
    finding_ids = {finding["id"] for finding in findings}
    assert "TOOL-PARAMS-scan_projects" in finding_ids
    assert "TOOL-REQUIRED-read_project_record" in finding_ids


def test_tool_contract_does_not_claim_to_compare_descriptions():
    payload = json.loads(
        (PROJECT_DIR / "governance" / "project_schema.json").read_text(encoding="utf-8")
    )
    description_rule = next(
        rule for rule in payload["validation_rules"] if rule["id"] == "TOOL-003"
    )
    assert "不自动比较 description" in description_rule["check"]


def test_bid_files_reference_uses_app_settings_and_node_local_runtime():
    reference = (PROJECT_DIR / "references" / "bid_files_integration.md").read_text(
        encoding="utf-8"
    )

    assert "load_app_settings" in reference
    assert "business_root" in reference
    assert "runtime_workspace" in reference
    assert "projection_root" in reference
    assert "不得等于或位于 `business_root` 下" in reference
    assert "~/Desktop/" not in reference
    assert 'expanduser("~/Desktop' not in reference


def test_historical_platform_plan_corrects_all_copyable_runtime_defaults():
    plan = (
        PROJECT_DIR / "docs" / "superpowers" / "plans"
        / "2026-07-23-platform-foundation.md"
    ).read_text(encoding="utf-8")

    assert "## 纠正说明（以当前实现为准）" in plan
    assert 'default_runtime = Path.home() / ".project_manager"' in plan
    assert 'resolved_business_root / ".project_manager"' not in plan
    assert '"runtime_workspace": "~/.project_manager"' in plan
    assert '"sqlite_path": "~/.project_manager/state/project_manager.sqlite3"' in plan
    assert '"projection_root": "~/.project_manager/projections"' in plan
    assert '"base_dir_default": "${HOME}/.project_manager"' in plan
    assert "ProjectManagerData/.project_manager" not in plan
