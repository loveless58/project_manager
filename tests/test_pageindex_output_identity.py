"""Regression tests for PageIndex operation and output identity."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from integrations.pageindex.pageindex_client import PageIndexClient
from integrations.pageindex.structure_index import PageIndexStructureIndex
from platform_core.models import StructureIndexRequest


def _install_fake_runtime(pageindex_dir: Path) -> PageIndexClient:
    client = PageIndexClient(str(pageindex_dir))
    Path(client.python_bin).parent.mkdir(parents=True)
    Path(client.python_bin).touch()
    Path(client.cli_script).touch()
    return client


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(path: Path, document_version_id: str) -> StructureIndexRequest:
    return StructureIndexRequest(
        document_version_id=document_version_id,
        content_hash=_content_hash(path),
        source_path=str(path),
        media_type="application/pdf",
    )


def _fake_cli_runner(*, actual_cwds=None, rendezvous=None):
    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.12", "")

        cwd = Path(kwargs["cwd"])
        if actual_cwds is not None:
            actual_cwds.append(cwd)
        if rendezvous is not None:
            rendezvous.wait(timeout=5)

        flag = "--pdf_path" if "--pdf_path" in command else "--md_path"
        staged_source = Path(command[command.index(flag) + 1])
        marker = staged_source.read_bytes().decode("utf-8")
        output = cwd / "results" / f"{staged_source.stem}_structure.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "doc_name": staged_source.name,
                    "structure": [{"title": marker, "nodes": []}],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_return_zero_without_new_output_never_reuses_preexisting_result(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    source = tmp_path / "incoming" / "report.pdf"
    source.parent.mkdir()
    source.write_bytes(b"new-input")

    stale_result = pageindex_dir / "results" / "report_structure.json"
    stale_result.parent.mkdir()
    stale_result.write_text(
        json.dumps(
            {
                "doc_name": "report.pdf",
                "structure": [{"title": "stale-secret", "nodes": []}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RESULT.MISSING"
    assert "stale-secret" not in json.dumps(result)


def test_same_basename_different_content_gets_distinct_bound_artifacts(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    _install_fake_runtime(pageindex_dir)
    workspace_root = tmp_path / "runtime" / "pageindex"
    first = tmp_path / "first" / "report.pdf"
    second = tmp_path / "second" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"alpha-document")
    second.write_bytes(b"beta-document")
    monkeypatch.setattr(subprocess, "run", _fake_cli_runner())
    adapter = PageIndexStructureIndex(
        pageindex_dir,
        workspace_root=workspace_root,
    )

    first_result = adapter.index(_request(first, "dv-alpha"))
    second_result = adapter.index(_request(second, "dv-beta"))

    assert first_result.status == second_result.status == "success"
    assert first_result.external_ref != second_result.external_ref
    assert first_result.external_ref.startswith("pageindex://")
    assert second_result.external_ref.startswith("pageindex://")
    assert first_result.structure[0]["title"] == "alpha-document"
    assert second_result.structure[0]["title"] == "beta-document"

    artifacts = sorted((workspace_root / "artifacts").glob("*.json"))
    assert len(artifacts) == 2
    envelopes = [json.loads(path.read_text(encoding="utf-8")) for path in artifacts]
    assert {item["content_hash"] for item in envelopes} == {
        _content_hash(first),
        _content_hash(second),
    }
    assert {item["source_sha256"] for item in envelopes} == {
        _content_hash(first),
        _content_hash(second),
    }


def test_concurrent_same_basename_operations_use_private_workspaces(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    _install_fake_runtime(pageindex_dir)
    workspace_root = tmp_path / "runtime" / "pageindex"
    first = tmp_path / "first" / "report.pdf"
    second = tmp_path / "second" / "report.pdf"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"concurrent-alpha")
    second.write_bytes(b"concurrent-beta")
    actual_cwds = []
    rendezvous = threading.Barrier(2)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_cli_runner(actual_cwds=actual_cwds, rendezvous=rendezvous),
    )
    adapter = PageIndexStructureIndex(
        pageindex_dir,
        workspace_root=workspace_root,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(adapter.index, _request(first, "dv-concurrent-alpha"))
        second_future = pool.submit(adapter.index, _request(second, "dv-concurrent-beta"))
        first_result = first_future.result(timeout=10)
        second_result = second_future.result(timeout=10)

    assert first_result.status == second_result.status == "success"
    assert first_result.structure[0]["title"] == "concurrent-alpha"
    assert second_result.structure[0]["title"] == "concurrent-beta"
    assert len(actual_cwds) == 2
    assert actual_cwds[0] != actual_cwds[1]
    assert all(path != pageindex_dir for path in actual_cwds)


def test_operation_identity_includes_document_hash_and_provider_version(tmp_path):
    class IdentityEchoClient:
        def __init__(self, provider_version):
            self.provider_version = provider_version

        def index_pdf(self, source_path, **kwargs):
            return {
                "status": "success",
                "engine": "pageindex",
                "operation_id": kwargs.get("operation_id", "legacy-operation"),
                "structure": [],
                "structure_json_path": "/shared/results/report_structure.json",
            }

    source = tmp_path / "report.pdf"
    source.write_bytes(b"identity-input")
    base_request = _request(source, "dv-one")

    def external_ref(request, provider_version):
        adapter = PageIndexStructureIndex(
            tmp_path,
            client_factory=lambda pageindex_dir: IdentityEchoClient(provider_version),
        )
        return adapter.index(request).external_ref

    baseline = external_ref(base_request, "provider-v1")
    changed_document = external_ref(
        StructureIndexRequest(
            "dv-two",
            base_request.content_hash,
            base_request.source_path,
            base_request.media_type,
        ),
        "provider-v1",
    )
    changed_hash = external_ref(
        StructureIndexRequest(
            base_request.document_version_id,
            "f" * 64,
            base_request.source_path,
            base_request.media_type,
        ),
        "provider-v1",
    )
    changed_provider = external_ref(base_request, "provider-v2")

    assert baseline.startswith("pageindex://")
    assert len({baseline, changed_document, changed_hash, changed_provider}) == 4


def test_content_hash_mismatch_is_stable_and_sanitized(tmp_path, monkeypatch):
    pageindex_dir = tmp_path / "Users" / "alice" / "PageIndex-secret"
    _install_fake_runtime(pageindex_dir)
    source = tmp_path / "Users" / "alice" / "Documents" / "customer-secret.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"actual-private-content")
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime-secret"),
    )

    result = client.index_pdf(
        str(source),
        operation_id="secret-operation-id",
        expected_content_hash="0" * 64,
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.INPUT.HASH_MISMATCH"
    assert calls == []
    for secret in (
        str(source),
        str(pageindex_dir),
        "alice",
        "customer-secret",
        "secret-operation-id",
        "0" * 64,
        "actual-private-content",
    ):
        assert secret not in serialized
