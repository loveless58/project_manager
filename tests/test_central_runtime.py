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
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "RuntimeCapabilityError" in completed.stdout
    assert "PROJECT_MANAGER_BUSINESS_ROOT" in completed.stdout
