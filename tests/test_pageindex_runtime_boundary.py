"""Regression tests for PageIndex external process and result-file boundaries."""

import builtins
import os
import subprocess
from pathlib import Path

import pytest

from integrations.pageindex.pageindex_client import PageIndexClient, PageIndexError


def _runtime_client(tmp_path: Path, *, corrupt_interpreter: bool = False) -> PageIndexClient:
    client = PageIndexClient(pageindex_dir=str(tmp_path))
    python_bin = Path(client.python_bin)
    python_bin.parent.mkdir(parents=True)
    if corrupt_interpreter:
        python_bin.write_bytes(b"not an executable Python runtime")
        if os.name != "nt":
            python_bin.chmod(0o755)
    else:
        python_bin.touch()
    Path(client.cli_script).touch()
    return client


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (
            subprocess.CompletedProcess(["python", "--version"], 2, "", ""),
            "PageIndex runtime probe failed.",
        ),
        (
            subprocess.TimeoutExpired(["python", "--version"], 1),
            "PageIndex runtime probe timed out.",
        ),
        (
            UnicodeDecodeError("utf-8", bytes([255]), 0, 1, "invalid"),
            "PageIndex runtime probe could not be started.",
        ),
    ],
)
def test_check_environment_normalizes_probe_outcomes(
    tmp_path, monkeypatch, outcome, expected
):
    client = _runtime_client(tmp_path)

    def run(*args, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(PageIndexError, match=expected):
        client.check_environment()


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (subprocess.TimeoutExpired(["python"], 1), "PageIndex execution timed out."),
        (
            UnicodeDecodeError("utf-8", bytes([255]), 0, 1, "invalid"),
            "PageIndex execution could not be started.",
        ),
    ],
)
def test_index_pdf_normalizes_process_communication_outcomes(
    tmp_path, monkeypatch, outcome, expected
):
    client = _runtime_client(tmp_path)
    source_path = tmp_path / "source.pdf"
    source_path.touch()

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.12", "")
        raise outcome

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(str(source_path))

    assert result["status"] == "failed"
    assert result["error"] == expected


def test_index_pdf_normalizes_structure_file_open_error(tmp_path, monkeypatch):
    client = _runtime_client(tmp_path)
    source_path = tmp_path / "source.pdf"
    source_path.touch()
    structure_path = tmp_path / "results" / "source_structure.json"
    structure_path.parent.mkdir()
    structure_path.write_text("{}", encoding="utf-8")
    original_open = builtins.open

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    def failing_open(path, *args, **kwargs):
        if Path(path) == structure_path:
            raise OSError("access denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(builtins, "open", failing_open)

    result = client.index_pdf(str(source_path))

    assert result["status"] == "failed"
    assert result["error"] == "PageIndex structure result could not be read."


def test_index_pdf_normalizes_invalid_utf8_structure_file(tmp_path, monkeypatch):
    client = _runtime_client(tmp_path)
    source_path = tmp_path / "source.pdf"
    source_path.touch()
    structure_path = tmp_path / "results" / "source_structure.json"
    structure_path.parent.mkdir()
    structure_path.write_bytes(bytes([255]))

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(str(source_path))

    assert result["status"] == "failed"
    assert result["error"] == "PageIndex structure result could not be decoded."


def test_check_environment_rejects_real_corrupt_interpreter_file(tmp_path):
    client = _runtime_client(tmp_path, corrupt_interpreter=True)

    with pytest.raises(PageIndexError, match="PageIndex runtime probe could not be started."):
        client.check_environment()
