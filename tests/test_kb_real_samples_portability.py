import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPOSITORY_ROOT / "tests" / "test_kb_real_samples.py"


def _load_tool_module():
    specification = importlib.util.spec_from_file_location("kb_real_samples", TOOL_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_real_sample_manifest_contains_only_relative_paths():
    tool = _load_tool_module()

    assert all(not Path(relative_path).is_absolute() for relative_path, _ in tool.SAMPLES)


def test_real_sample_tool_does_not_mutate_sys_path():
    assert "sys.path" not in TOOL_PATH.read_text(encoding="utf-8")


def test_real_sample_tool_requires_explicit_sample_root():
    environment = os.environ.copy()
    environment.pop("PROJECT_MANAGER_REAL_SAMPLE_ROOT", None)

    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", "-m", "tests.test_kb_real_samples"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode != 0
    assert "PROJECT_MANAGER_REAL_SAMPLE_ROOT" in (completed.stdout + completed.stderr)


def test_real_sample_tool_fails_when_manifest_files_are_missing():
    with tempfile.TemporaryDirectory() as empty_root:
        environment = os.environ.copy()
        environment["PROJECT_MANAGER_REAL_SAMPLE_ROOT"] = empty_root

        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "tests.test_kb_real_samples"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    assert completed.returncode != 0
    assert "Missing sample files" in (completed.stdout + completed.stderr)
