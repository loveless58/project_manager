"""Final second-wave regressions for PageIndex publication and cache conflicts."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import threading
from pathlib import Path

import fitz
import pytest

from integrations.pageindex import pageindex_client
from integrations.pageindex.pageindex_client import PageIndexClient
from skills.document_parse import kb


def _runtime_client(tmp_path: Path) -> PageIndexClient:
    pageindex_dir = tmp_path / "pageindex"
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(tmp_path / "runtime" / "pageindex"),
    )
    Path(client.python_bin).parent.mkdir(parents=True)
    Path(client.python_bin).touch()
    Path(client.cli_script).touch()
    return client


def _write_pdf(path: Path) -> None:
    document = fitz.open()
    try:
        document.new_page(width=72, height=72)
        document.save(path)
    finally:
        document.close()


def _install_success_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.13", "")
        flag = "--pdf_path" if "--pdf_path" in command else "--md_path"
        staged_source = Path(command[command.index(flag) + 1])
        result_path = Path(kwargs["cwd"]) / "results" / f"{staged_source.stem}_structure.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            json.dumps({
                "doc_name": staged_source.name,
                "structure": [{
                    "title": "root", "node_id": "0001",
                    "start_index": 1, "end_index": 1, "nodes": [],
                }],
            }),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)


def _hold_artifact_lock(artifact_root: str, ready, release) -> None:
    with pageindex_client._exclusive_artifact_lock(Path(artifact_root)):
        ready.set()
        release.wait(timeout=10)


def test_pdf_publication_and_gc_are_one_serialized_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _runtime_client(tmp_path)
    source = tmp_path / "source.pdf"
    _write_pdf(source)
    _install_success_runner(monkeypatch)
    json_publish_reached = threading.Event()
    gc_finished = threading.Event()
    real_link = os.link

    def gated_link(source_path, destination_path, *args, **kwargs):
        if Path(destination_path).suffix == ".json":
            json_publish_reached.set()
            gc_finished.wait(timeout=1)
        return real_link(source_path, destination_path, *args, **kwargs)

    monkeypatch.setattr(pageindex_client.os, "link", gated_link)
    outcome: dict[str, object] = {}
    publishing = threading.Thread(
        target=lambda: outcome.update(client.index_pdf(str(source)))
    )
    publishing.start()
    assert json_publish_reached.wait(timeout=5)
    removed: list[int] = []

    def collect() -> None:
        removed.append(client.collect_orphaned_source_artifacts(min_age_seconds=0))
        gc_finished.set()

    collecting = threading.Thread(target=collect)
    collecting.start()
    publishing.join(timeout=5)
    collecting.join(timeout=5)
    assert not publishing.is_alive()
    assert not collecting.is_alive()
    assert outcome["status"] == "success"
    assert removed == [0]
    source_artifact = Path(str(outcome["source_artifact_path"]))
    assert source_artifact.is_file()
    assert PageIndexClient._sha256_file(source_artifact) == outcome["source_sha256"]


def test_artifact_lock_serializes_processes_and_releases_after_exception(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifacts"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_artifact_lock,
        args=(str(artifact_root), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=10)
        acquired = threading.Event()

        def acquire_in_parent() -> None:
            with pageindex_client._exclusive_artifact_lock(artifact_root):
                acquired.set()

        waiter = threading.Thread(target=acquire_in_parent)
        waiter.start()
        assert not acquired.wait(timeout=0.2)
        release.set()
        assert acquired.wait(timeout=10)
        waiter.join(timeout=5)
        assert not waiter.is_alive()
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
    assert holder.exitcode == 0

    with pytest.raises(RuntimeError, match="synthetic"):
        with pageindex_client._exclusive_artifact_lock(artifact_root):
            raise RuntimeError("synthetic")
    with pageindex_client._exclusive_artifact_lock(artifact_root):
        pass


@pytest.mark.parametrize(
    ("existing_json", "payload"),
    [
        ('{"value":true}', {"value": 1}),
        ('{"value":1}', {"value": 1.0}),
        ('{"value":1,"value":2}', {"value": 2}),
        ('{"value":NaN}', {"value": float("nan")}),
    ],
    ids=["bool-vs-int", "int-vs-float", "duplicate-key", "nan"],
)
def test_atomic_cache_conflict_requires_strict_duplicate_safe_json_equality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_json: str,
    payload: dict[str, object],
) -> None:
    target = tmp_path / "cache" / "kb.json"
    target.parent.mkdir(parents=True)
    target.write_text(existing_json, encoding="utf-8")

    def lose_replace(source, destination) -> None:
        raise PermissionError(13, "synthetic concurrent replace conflict")

    monkeypatch.setattr(kb.os, "replace", lose_replace)
    with pytest.raises(kb.KnowledgeBaseUnavailable) as exc_info:
        kb._write_json_atomic(target, payload, provider="recording")
    assert exc_info.value.error_code == "DOCUMENT_PARSE.KB.CACHE_WRITE_FAILED"
    assert target.read_text(encoding="utf-8") == existing_json
    assert not list(target.parent.glob(".*.tmp"))


def test_atomic_cache_conflict_accepts_strict_json_with_different_formatting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "cache" / "kb.json"
    target.parent.mkdir(parents=True)
    target.write_text('{\n  "second": [2, 3],\n  "first": 1\n}', encoding="utf-8")

    def lose_replace(source, destination) -> None:
        raise PermissionError(13, "synthetic concurrent replace conflict")

    monkeypatch.setattr(kb.os, "replace", lose_replace)
    kb._write_json_atomic(
        target,
        {"first": 1, "second": [2, 3]},
        provider="recording",
    )
    assert not list(target.parent.glob(".*.tmp"))
