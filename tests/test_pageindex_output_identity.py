"""Regression tests for PageIndex operation and output identity."""

from __future__ import annotations

import hashlib
import json
import os
import time
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from integrations.pageindex.pageindex_client import (
    PageIndexClient,
    build_operation_identity,
)
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
                "provider_version": self.provider_version,
                "content_hash": kwargs.get("expected_content_hash"),
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


def test_invalid_expected_hash_is_rejected_before_subprocess(tmp_path, monkeypatch):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"content")
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = client.index_pdf(
        str(source),
        document_version_id="dv-invalid-hash",
        expected_content_hash="not-a-sha256",
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.INPUT.HASH_INVALID"
    assert calls == []


def test_forged_operation_id_is_rejected_before_subprocess(tmp_path, monkeypatch):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"identity-bound-content")
    content_hash = _content_hash(source)
    expected_operation_id = build_operation_identity(
        "dv-authoritative",
        content_hash,
        client.provider_version,
    )
    forged_operation_id = "0" * 64
    assert forged_operation_id != expected_operation_id
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = client.index_pdf(
        str(source),
        operation_id=forged_operation_id,
        document_version_id="dv-authoritative",
        expected_content_hash=content_hash,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.INPUT.IDENTITY_MISMATCH"
    assert calls == []


def test_provider_version_fingerprints_core_provider_files(tmp_path):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    core_module = pageindex_dir / "page_index.py"
    core_module.write_text("PROVIDER_REVISION = 1\n", encoding="utf-8")
    first = client.provider_version

    core_module.write_text("PROVIDER_REVISION = 2\n", encoding="utf-8")
    second = client.provider_version

    assert first != second


def test_provider_manifest_failure_blocks_before_subprocess_and_artifact(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime"),
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"provider-version-unavailable")
    subprocess_calls = []

    def unavailable_manifest():
        raise OSError("provider manifest cannot be read")

    monkeypatch.setattr(client, "_provider_manifest_digest", unavailable_manifest)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess_calls.append((args, kwargs)),
    )

    result = client.index_pdf(str(source))

    assert client.provider_version == ""
    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RUNTIME.VERSION_UNAVAILABLE"
    assert subprocess_calls == []
    assert not (Path(client.workspace_root) / "artifacts").exists()


def test_provider_change_during_execution_discards_result(tmp_path, monkeypatch):
    pageindex_dir = tmp_path / "pageindex"
    _install_fake_runtime(pageindex_dir)
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime"),
    )
    core_module = pageindex_dir / "page_index.py"
    core_module.write_text("PROVIDER_REVISION = 1\n", encoding="utf-8")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"provider-pinned-content")
    content_hash = _content_hash(source)
    pinned_provider_version = client.provider_version
    operation_id = build_operation_identity(
        "dv-provider-pinned",
        content_hash,
        pinned_provider_version,
    )

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.12", "")
        staged_source = Path(command[command.index("--pdf_path") + 1])
        output = (
            Path(kwargs["cwd"])
            / "results"
            / f"{staged_source.stem}_structure.json"
        )
        output.parent.mkdir(parents=True)
        output.write_text(
            json.dumps(
                {
                    "doc_name": staged_source.name,
                    "structure": [{"title": "fresh", "nodes": []}],
                }
            ),
            encoding="utf-8",
        )
        core_module.write_text("PROVIDER_REVISION = 2\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(
        str(source),
        operation_id=operation_id,
        document_version_id="dv-provider-pinned",
        expected_content_hash=content_hash,
        expected_provider_version=pinned_provider_version,
    )

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RUNTIME.VERSION_CHANGED"
    assert not list((Path(client.workspace_root) / "artifacts").glob("*.json"))


def test_indexed_pdf_keeps_private_source_for_page_content_helper(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"persisted-pdf-snapshot")
    monkeypatch.setattr(subprocess, "run", _fake_cli_runner())

    result = client.index_pdf(str(source))

    assert result["status"] == "success"
    source_artifact = Path(result["source_artifact_path"])
    assert source_artifact.name.startswith("source-")
    assert source_artifact.is_file()
    assert source_artifact.read_bytes() == source.read_bytes()

    observed = []
    monkeypatch.setattr(
        client,
        "get_page_content",
        lambda pdf_path, pages: observed.append((pdf_path, pages))
        or [{"page": 1, "content": "ok"}],
    )

    pages = client.get_page_content_from_index(result, "1")

    portable_result = dict(result)
    portable_result.pop("source_artifact_path")
    portable_pages = client.get_page_content_from_index(portable_result, "1")
    assert pages == [{"page": 1, "content": "ok"}]
    assert portable_pages == [{"page": 1, "content": "ok"}]
    assert observed == [
        (str(source_artifact), "1"),
        (str(source_artifact), "1"),
    ]


@pytest.mark.parametrize("replace_published_pdf", [False, True])
def test_json_publish_failure_only_removes_operation_owned_pdf(
    tmp_path, monkeypatch, replace_published_pdf
):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime"),
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"operation-owned-source")
    monkeypatch.setattr(subprocess, "run", _fake_cli_runner())
    real_replace = os.replace
    published_pdf = []

    def fail_json_publish(source_path, target_path):
        target_path = Path(target_path)
        if target_path.suffix == ".pdf":
            real_replace(source_path, target_path)
            published_pdf.append(target_path)
            if replace_published_pdf:
                replacement = target_path.parent / "replacement.tmp"
                replacement.write_bytes(b"replacement-not-owned-by-operation")
                real_replace(replacement, target_path)
            return
        if target_path.suffix == ".json":
            assert published_pdf
            raise OSError("injected JSON artifact publication failure")
        real_replace(source_path, target_path)

    monkeypatch.setattr(os, "replace", fail_json_publish)

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RESULT.PERSIST_FAILED"
    assert len(published_pdf) == 1
    assert published_pdf[0].name.startswith("source-")
    if replace_published_pdf:
        assert published_pdf[0].read_bytes() == (
            b"replacement-not-owned-by-operation"
        )
    else:
        assert not published_pdf[0].exists()


def test_concurrent_same_operation_failure_cannot_delete_committed_snapshot(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime"),
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"same-operation-concurrency")
    content_hash = _content_hash(source)
    monkeypatch.setattr(subprocess, "run", _fake_cli_runner())
    real_replace = os.replace
    json_barrier = threading.Barrier(2)
    order_lock = threading.Lock()
    json_publishers = []
    published_pdf_by_thread = {}

    def interleaved_replace(source_path, target_path):
        thread_id = threading.get_ident()
        target_path = Path(target_path)
        if target_path.suffix == ".pdf":
            real_replace(source_path, target_path)
            published_pdf_by_thread[thread_id] = target_path
            return
        if target_path.suffix == ".json":
            with order_lock:
                json_publish_order = len(json_publishers)
                json_publishers.append(thread_id)
            json_barrier.wait(timeout=5)
            if json_publish_order == 0:
                raise OSError("injected first-attempt JSON failure")
            real_replace(source_path, target_path)
            return
        real_replace(source_path, target_path)

    monkeypatch.setattr(os, "replace", interleaved_replace)

    def index_once():
        thread_id = threading.get_ident()
        result = client.index_pdf(
            str(source),
            document_version_id="dv-concurrent-same-operation",
            expected_content_hash=content_hash,
        )
        return thread_id, result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(index_once)
        second_future = pool.submit(index_once)
        attempts = [first_future.result(), second_future.result()]

    successful = next(item for item in attempts if item[1]["status"] == "success")
    failed = next(item for item in attempts if item[1]["status"] == "failed")
    successful_source = published_pdf_by_thread[successful[0]]
    failed_source = published_pdf_by_thread[failed[0]]
    assert successful_source != failed_source
    assert successful_source.is_file()
    assert not failed_source.exists()
    artifact_path = Path(successful[1]["structure_json_path"])
    envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert envelope["source_artifact_name"] == successful_source.name
    assert successful[1]["source_artifact_path"] == str(successful_source)
    assert failed[1]["error_code"] == "PAGEINDEX.RESULT.PERSIST_FAILED"


def test_source_artifact_gc_respects_commit_markers_age_and_name_boundary(
    tmp_path,
):
    pageindex_dir = tmp_path / "pageindex"
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime"),
    )
    artifact_root = Path(client.workspace_root) / "artifacts"
    artifact_root.mkdir(parents=True)
    referenced = artifact_root / f"source-{'a' * 32}.pdf"
    orphan = artifact_root / f"source-{'b' * 32}.pdf"
    recent = artifact_root / f"source-{'c' * 32}.pdf"
    unmanaged = artifact_root / "source-not-managed.pdf"
    for path in (referenced, orphan, recent, unmanaged):
        path.write_bytes(path.name.encode("utf-8"))
    old_timestamp = time.time() - 120
    for path in (referenced, orphan, unmanaged):
        os.utime(path, (old_timestamp, old_timestamp))
    (artifact_root / f"{'d' * 64}.json").write_text(
        json.dumps(
            {
                "schema_version": "pageindex_artifact.v2",
                "source_artifact_name": referenced.name,
            }
        ),
        encoding="utf-8",
    )

    removed = client.collect_orphaned_source_artifacts(
        min_age_seconds=60,
    )

    assert removed == 1
    assert referenced.is_file()
    assert not orphan.exists()
    assert recent.is_file()
    assert unmanaged.is_file()


def test_pdf_staging_uses_fixed_extension_for_long_source_suffix(
    tmp_path, monkeypatch
):
    pageindex_dir = tmp_path / "pageindex"
    client = _install_fake_runtime(pageindex_dir)
    source = tmp_path / f"source.{'x' * 80}"
    source.write_bytes(b"long-suffix-content")
    staged_names = []

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.12", "")
        staged_source = Path(command[command.index("--pdf_path") + 1])
        staged_names.append(staged_source.name)
        output = (
            Path(kwargs["cwd"])
            / "results"
            / f"{staged_source.stem}_structure.json"
        )
        output.parent.mkdir(parents=True)
        output.write_text(
            json.dumps(
                {
                    "doc_name": staged_source.name,
                    "structure": [{"title": "ok", "nodes": []}],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)

    result = client.index_pdf(str(source))

    assert result["status"] == "success"
    assert staged_names == ["input.pdf"]


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH boundary")
def test_deep_workspace_fails_stably_before_subprocess(tmp_path, monkeypatch):
    pageindex_dir = tmp_path / "pageindex"
    _install_fake_runtime(pageindex_dir)
    workspace_root = tmp_path / ("w" * 180)
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(workspace_root),
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"content")
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RUNTIME.WORKSPACE_PATH_TOO_LONG"
    assert calls == []


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
