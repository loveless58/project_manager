import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
import unittest

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPOSITORY_ROOT / "tests" / "test_kb_real_samples.py"
SYNTHETIC_MANIFEST = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "synthetic_kb_sample_manifest.json"
)


def _load_tool_module():
    specification = importlib.util.spec_from_file_location("kb_real_samples", TOOL_PATH)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_tracked_manifest_is_explicitly_synthetic_and_portable():
    tool = _load_tool_module()

    manifest = tool.load_sample_manifest(manifest_path=SYNTHETIC_MANIFEST)

    assert manifest.fixture_kind == "synthetic"
    assert manifest.contains_real_business_data is False
    assert manifest.samples
    for sample in manifest.samples:
        assert not PurePosixPath(sample.relative_path).is_absolute()
        windows_path = PureWindowsPath(sample.relative_path)
        assert not windows_path.is_absolute()
        assert not windows_path.drive
        assert "\\" not in sample.relative_path


def _manifest_with_relative_path(tmp_path, relative_path):
    payload = json.loads(SYNTHETIC_MANIFEST.read_text(encoding="utf-8"))
    payload["samples"][0]["relative_path"] = relative_path
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_manifest_rejects_windows_drive_relative_path(tmp_path):
    tool = _load_tool_module()
    manifest = _manifest_with_relative_path(
        tmp_path,
        r"C:synthetic\ProjectAlpha\tender-notice.docx",
    )

    with pytest.raises(tool.SampleManifestError, match="must be relative"):
        tool.load_sample_manifest(manifest_path=manifest)


def test_manifest_normalizes_portable_path_separators(tmp_path):
    tool = _load_tool_module()
    manifest = _manifest_with_relative_path(
        tmp_path,
        r"synthetic\ProjectAlpha\tender-notice.docx",
    )

    loaded = tool.load_sample_manifest(manifest_path=manifest)

    assert loaded.samples[0].relative_path == (
        "synthetic/ProjectAlpha/tender-notice.docx"
    )


def test_real_sample_tool_does_not_mutate_sys_path():
    assert "sys.path" not in TOOL_PATH.read_text(encoding="utf-8")


def test_local_business_manifest_is_ignored_by_exact_path():
    ignore_lines = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "tests/fixtures/kb_real_samples.local.json" in ignore_lines
    assert "tests/fixtures/*.json" not in ignore_lines


def test_missing_local_manifest_raises_skip():
    tool = _load_tool_module()

    with tempfile.TemporaryDirectory() as temporary_directory:
        missing_manifest = Path(temporary_directory) / "missing.local.json"
        with pytest.raises(unittest.SkipTest, match="local sample manifest"):
            tool.load_sample_manifest(manifest_path=missing_manifest)


def test_sample_tool_reports_skip_when_local_manifest_is_missing():
    with tempfile.TemporaryDirectory() as temporary_directory:
        environment = os.environ.copy()
        environment["PROJECT_MANAGER_REAL_SAMPLE_MANIFEST"] = str(
            Path(temporary_directory) / "missing.local.json"
        )
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

    assert completed.returncode == 0
    assert "SKIP" in (completed.stdout + completed.stderr)


def test_real_sample_tool_fails_when_manifest_files_are_missing():
    with tempfile.TemporaryDirectory() as empty_root:
        environment = os.environ.copy()
        environment["PROJECT_MANAGER_REAL_SAMPLE_ROOT"] = empty_root
        environment["PROJECT_MANAGER_REAL_SAMPLE_MANIFEST"] = str(SYNTHETIC_MANIFEST)

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


def test_real_sample_tool_requires_root_when_manifest_is_configured():
    environment = os.environ.copy()
    environment["PROJECT_MANAGER_REAL_SAMPLE_MANIFEST"] = str(SYNTHETIC_MANIFEST)
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
    assert "PROJECT_MANAGER_REAL_SAMPLE_ROOT" in (
        completed.stdout + completed.stderr
    )
