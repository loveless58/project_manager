import json
import subprocess
from pathlib import Path

import pytest

from integrations.pageindex.pageindex_client import PageIndexClient, PageIndexError
from platform_core.models import StructureIndexRequest

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

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
        provider_version = "failing-provider-v1"

        def check_environment(self):
            raise PageIndexError(provider_error)

        def index_pdf(self, source_path, **kwargs):
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
        EMPTY_SHA256,
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


def test_real_missing_pageindex_runtime_is_blocked_by_adapter_for_probe_and_index(
    tmp_path,
):
    """A real client runtime failure is unavailable, not a provider execution failure."""
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    source_path = tmp_path / "existing.pdf"
    source_path.touch()
    adapter = PageIndexStructureIndex(tmp_path / "missing-pageindex-runtime")
    request = StructureIndexRequest(
        "dv-1", EMPTY_SHA256, str(source_path), "application/pdf"
    )

    probe = adapter.probe()
    result = adapter.index(request)

    assert probe.status == "blocked"
    assert result.status == "blocked"
    assert result.error_code == "INDEX.PROVIDER_UNAVAILABLE"
    assert result.error == "PageIndex provider is unavailable."


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "not-a-document",
        {"doc_name": "report", "structure": {}},
        {"doc_name": "report", "structure": ["not-a-node"]},
        {"doc_name": "report", "structure": [{"nodes": "not-a-list"}]},
        {"doc_name": "report", "structure": [{"nodes": [{"nodes": [7]}]}]},
        {"doc_name": 7, "structure": []},
    ],
)
def test_invalid_pageindex_result_schema_is_stable_sanitized_and_maps_to_provider_failed(
    tmp_path, monkeypatch, payload
):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    client = _runtime_client(tmp_path)
    source_path = tmp_path / "Documents" / "private.pdf"
    source_path.parent.mkdir()
    source_path.touch()

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "", "")
        flag = "--pdf_path" if "--pdf_path" in command else "--md_path"
        staged_source = Path(command[command.index(flag) + 1])
        result_path = (
            Path(kwargs["cwd"])
            / "results"
            / f"{staged_source.stem}_structure.json"
        )
        result_path.parent.mkdir(parents=True)
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    raw = client.index_pdf(str(source_path))
    adapter = PageIndexStructureIndex(client.pageindex_dir)
    mapped = adapter.index(
        StructureIndexRequest("dv-1", EMPTY_SHA256, str(source_path), "application/pdf")
    )

    assert raw["status"] == "failed"
    assert raw["error_code"] == "PAGEINDEX.RESULT.INVALID_SCHEMA"
    assert raw["error"] == "PageIndex structure result has an invalid schema."
    assert mapped.status == "failed"
    assert mapped.error_code == "INDEX.PROVIDER_FAILED"
    _assert_sanitized(raw, "private", "not-a-document", "not-a-node", "not-a-list")
