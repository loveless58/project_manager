import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _run_pageindex_slow_tests(environment):
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-B",
            "-m",
            "pytest",
            "tests/test_pageindex_client.py",
            "-q",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_enabling_pageindex_slow_tests_without_configuration_fails():
    environment = os.environ.copy()
    environment["PAGEINDEX_RUN_SLOW_TESTS"] = "1"
    for name in (
        "PAGEINDEX_TEST_DIR",
        "PAGEINDEX_TEST_PDF",
        "PAGEINDEX_TEST_CACHE",
        "PAGEINDEX_TEST_SCAN_PDF",
    ):
        environment.pop(name, None)

    completed = _run_pageindex_slow_tests(environment)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    for variable in (
        "PAGEINDEX_TEST_DIR",
        "PAGEINDEX_TEST_PDF",
        "PAGEINDEX_TEST_CACHE",
        "PAGEINDEX_TEST_SCAN_PDF",
    ):
        assert variable in output


def test_enabling_pageindex_slow_tests_with_wrong_path_types_fails_clearly():
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_root = Path(temporary_directory)
        file_path = temporary_root / "not-a-directory"
        file_path.write_text("fixture", encoding="utf-8")
        directory_path = temporary_root / "not-a-file"
        directory_path.mkdir()

        environment = os.environ.copy()
        environment["PAGEINDEX_RUN_SLOW_TESTS"] = "1"
        environment["PAGEINDEX_TEST_DIR"] = str(file_path)
        environment["PAGEINDEX_TEST_PDF"] = str(directory_path)
        environment["PAGEINDEX_TEST_CACHE"] = str(directory_path)
        environment["PAGEINDEX_TEST_SCAN_PDF"] = str(directory_path)

        completed = _run_pageindex_slow_tests(environment)

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    for variable in (
        "PAGEINDEX_TEST_DIR",
        "PAGEINDEX_TEST_PDF",
        "PAGEINDEX_TEST_CACHE",
        "PAGEINDEX_TEST_SCAN_PDF",
    ):
        assert variable in output
    assert "existing directory" in output
    assert "existing file" in output
