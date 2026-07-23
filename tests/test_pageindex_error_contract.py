import json
import subprocess
from pathlib import Path

import pytest

from integrations.pageindex.pageindex_client import PageIndexClient, PageIndexError
from platform_core.models import StructureIndexRequest


SENSITIVE_ENDPOINT = "http://192." + "168.50.4:9990/v1"


def _assert_sanitized(result, *secrets):
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    for secret in secrets:
        assert secret not in serialized


def _runtime_client(tmp_path: Path) -> PageIndexClient:
    pageindex_dir = tmp_path / "Users" / "alice" / "PageIndex-secret"
    client = PageIndexClient(str(pageindex_dir))
    Path(client.python_bin).parent.mkdir(parents=True)
    Path(client.python_bin).touch()
    Path(client.cli_script).touch()
    return client


@pytest.mark.parametrize(
    ("method", "source_name", "error_code", "error"),
    [
        (
            "index_pdf",
            "customer-secret.pdf",
            "PAGEINDEX.INPUT.PDF_NOT_FOUND",
            "PDF file not found.",
        ),
        (
            "index_md",
            "customer-secret.txt",
            "PAGEINDEX.INPUT.MARKDOWN_EXTENSION",
            "Markdown file must use a .md or .markdown extension.",
        ),
        (
            "index_md",
            "customer-secret.md",
            "PAGEINDEX.INPUT.MARKDOWN_NOT_FOUND",
            "Markdown file not found.",
        ),
    ],
)
def test_input_failures_are_stable_and_do_not_echo_source_path(
    tmp_path, method, source_name, error_code, error
):
    client = PageIndexClient(str(tmp_path / "Users" / "alice" / "PageIndex-secret"))
    source_path = str(tmp_path / "Users" / "alice" / "Documents" / source_name)

    result = getattr(client, method)(source_path)

    assert result["status"] == "failed"
    assert result["error_code"] == error_code
    assert result["error"] == error
    _assert_sanitized(result, source_path, "alice", "customer-secret")


def test_cli_failure_drops_stderr_paths_endpoints_and_return_code(tmp_path, monkeypatch):
    client = _runtime_client(tmp_path)
    source_path = tmp_path / "Users" / "alice" / "Documents" / "secret.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.touch()
    stderr = f"failed for {source_path} in {client.pageindex_dir} using {SENSITIVE_ENDPOINT}"

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python", "")
        return subprocess.CompletedProcess(command, 23, "", stderr)

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(str(source_path))

    assert result["error_code"] == "PAGEINDEX.EXECUTION.FAILED"
    assert result["error"] == "PageIndex execution failed."
    _assert_sanitized(
        result,
        str(source_path),
        client.pageindex_dir,
        "alice",
        stderr,
        SENSITIVE_ENDPOINT,
        "23",
    )


def test_missing_result_is_stable_and_does_not_echo_result_path(tmp_path, monkeypatch):
    client = _runtime_client(tmp_path)
    source_path = tmp_path / "Users" / "alice" / "Documents" / "secret.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.touch()

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = client.index_pdf(str(source_path))

    assert result["error_code"] == "PAGEINDEX.RESULT.MISSING"
    assert result["error"] == "PageIndex structure result was not generated."
    _assert_sanitized(result, str(source_path), client.pageindex_dir, "alice", "secret")


def test_external_boundary_error_is_normalized_without_exception_text(
    tmp_path, monkeypatch
):
    client = _runtime_client(tmp_path)
    source_path = tmp_path / "Users" / "alice" / "Documents" / "secret.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.touch()
    raw_error = f"cannot execute {client.python_bin}; endpoint={SENSITIVE_ENDPOINT}"

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python", "")
        raise OSError(raw_error)

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(str(source_path))

    assert result["error_code"] == "PAGEINDEX.EXECUTION.START_FAILED"
    assert result["error"] == "PageIndex execution could not be started."
    _assert_sanitized(result, raw_error, client.pageindex_dir, "alice", SENSITIVE_ENDPOINT)


def test_structure_index_never_forwards_provider_error_text(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    provider_error = (
        "provider failed for /Users/" + "alice/secret.pdf at " + SENSITIVE_ENDPOINT
    )

    class FailingClient:
        def check_environment(self):
            raise PageIndexError(provider_error)

        def index_pdf(self, source_path):
            return {
                "status": "failed",
                "error_code": "PAGEINDEX.EXECUTION.FAILED",
                "error": provider_error,
            }

    adapter = PageIndexStructureIndex(
        tmp_path,
        client_factory=lambda pageindex_dir: FailingClient(),
    )
    request = StructureIndexRequest(
        "dv-1",
        "hash-1",
        "/Users/" + "alice/secret.pdf",
        "application/pdf",
    )

    probe = adapter.probe()
    result = adapter.index(request)

    assert probe.reason == "PageIndex provider is unavailable."
    assert result.error_code == "INDEX.PROVIDER_FAILED"
    assert result.error == "PageIndex provider failed."
    _assert_sanitized(probe, provider_error, "alice", SENSITIVE_ENDPOINT)
    _assert_sanitized(result, provider_error, "alice", SENSITIVE_ENDPOINT)
