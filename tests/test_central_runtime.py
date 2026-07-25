import ast
import json
import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _central_environment(runtime_workspace: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PROJECT_MANAGER_BUSINESS_ROOT", None)
    environment.update(
        {
            "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
            "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            "PROJECT_MANAGER_WORKSPACE_DIR": str(runtime_workspace),
            "PROJECT_MANAGER_DOCUMENT_STORE": "disabled",
            "PROJECT_MANAGER_DATABASE_DSN": "postgresql://user:secret@nas/project_manager",
        }
    )
    return environment


def test_central_cloudcc_runtime_needs_no_legacy_business_root(tmp_path):
    script = """
import json
from main import run

result = run("CloudCC 登录态检查", planner_mode="rule")
print(json.dumps(result, ensure_ascii=False))
"""

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=PROJECT_ROOT,
        env=_central_environment(tmp_path / "runtime"),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["status"] == "completed"
    assert result["metadata"]["deployment"]["deployment_mode"] == "central"
    assert result["metadata"]["adapters"]["document_store"] == "disabled"
    assert "postgresql://user:secret@nas/project_manager" not in json.dumps(result)
    assert "PROJECT_MANAGER_DATABASE_DSN" not in json.dumps(result)
    assert list((tmp_path / "runtime" / "logs").glob("project_manager_*.json"))


def test_legacy_project_management_fails_with_capability_error_without_business_root(tmp_path):
    script = """
from main import run

try:
    run("今天有哪些项目风险", planner_mode="rule")
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}")
    raise SystemExit(0)
raise SystemExit("expected legacy workspace capability error")
"""

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=PROJECT_ROOT,
        env=_central_environment(tmp_path / "runtime"),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "RuntimeCapabilityError" in completed.stdout
    assert "PROJECT_MANAGER_BUSINESS_ROOT" in completed.stdout


def test_runtime_adapters_preserve_three_positional_argument_compatibility():
    from app_bootstrap.composition import RuntimeAdapters

    first, second, third = object(), object(), object()

    runtime = RuntimeAdapters(first, second, third)

    assert runtime.document_store is first
    assert runtime.structure_index is second
    assert runtime.projection_writer is third
    assert runtime.retrieval_service is None
    assert runtime.interpretation_service is None
    assert runtime.archive_target_resolver is None


def test_central_data_cleaning_uses_explicit_workspace_without_business_root(tmp_path):
    data_workspace = tmp_path / "data-workspace"
    script = f"""
import json
from main import run

result = run(
    "扫描文件",
    planner_mode="rule",
    data_workspace_dir={str(data_workspace)!r},
)
print(json.dumps(result, ensure_ascii=False))
"""

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=PROJECT_ROOT,
        env=_central_environment(tmp_path / "runtime"),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["status"] == "completed"
    assert result["metadata"]["active_skill"] == "data_cleaning_file_organization"
    assert result["metadata"]["data_workspace_dir"] == str(data_workspace)
    assert result["rounds"][0]["action"] == "scan_raw_files"
    assert result["rounds"][0]["status"] == "success"
    observation = ast.literal_eval(result["rounds"][0]["observation"])
    assert observation["source"] == str(data_workspace / "00-原始文件（待处理）")


def test_central_data_cleaning_without_explicit_workspace_uses_runtime_workspace(tmp_path):
    script = """
import json
from main import run

result = run("扫描文件", planner_mode="rule")
print(json.dumps(result, ensure_ascii=False))
"""

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=PROJECT_ROOT,
        env=_central_environment(tmp_path / "runtime"),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    expected = tmp_path / "runtime" / "数据清洗工作台"
    assert result["status"] == "completed"
    assert result["metadata"]["data_workspace_dir"] == str(expected)
    observation = ast.literal_eval(result["rounds"][0]["observation"])
    assert observation["source"] == str(expected / "00-原始文件（待处理）")
    assert "PROJECT_MANAGER_BUSINESS_ROOT" not in completed.stdout
