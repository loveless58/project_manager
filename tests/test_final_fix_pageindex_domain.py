"""Final regression coverage for the PageIndex/document_parse review domain."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from integrations.pageindex.pageindex_client import PageIndexClient


@pytest.fixture(autouse=True)
def _explicit_pdf_page_count_adapter(monkeypatch):
    """Keep domain doubles independent of the physical PDF parser."""
    monkeypatch.setattr(
        PageIndexClient, "_pdf_page_count", staticmethod(lambda path: 1)
    )


def _install_pageindex_runtime(
    tmp_path: Path,
    *,
    workspace_name: str = "runtime",
) -> PageIndexClient:
    pageindex_dir = tmp_path / "pageindex"
    workspace_root = tmp_path / workspace_name / "pageindex"
    client = PageIndexClient(
        str(pageindex_dir),
        workspace_root=str(workspace_root),
    )
    Path(client.python_bin).parent.mkdir(parents=True)
    Path(client.python_bin).touch()
    Path(client.cli_script).touch()
    return client


def _valid_node(title: str) -> dict[str, object]:
    return {
        "title": title,
        "node_id": "0001",
        "start_index": 1,
        "end_index": 1,
        "nodes": [],
    }


def _install_index_runner(
    monkeypatch,
    *,
    rendezvous: threading.Barrier | None = None,
) -> list[str]:
    titles: list[str] = []
    title_lock = threading.Lock()

    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.13", "")

        with title_lock:
            title = f"attempt-{len(titles) + 1}"
            titles.append(title)
        if rendezvous is not None:
            rendezvous.wait(timeout=5)

        cwd = Path(kwargs["cwd"])
        input_flag = "--pdf_path" if "--pdf_path" in command else "--md_path"
        staged_source = Path(command[command.index(input_flag) + 1])
        result_path = cwd / "results" / f"{staged_source.stem}_structure.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            json.dumps(
                {
                    "doc_name": staged_source.name,
                    "structure": [_valid_node(title)],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    return titles


def _index_pdf(client: PageIndexClient, source: Path) -> dict[str, object]:
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    return client.index_pdf(
        str(source),
        document_version_id="dv-stable-publication",
        expected_content_hash=content_hash,
    )


def test_repeated_same_operation_reuses_one_deterministic_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _install_pageindex_runtime(tmp_path)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"same-operation-source")
    execution_titles = _install_index_runner(monkeypatch)

    first = _index_pdf(client, source)
    second = _index_pdf(client, source)

    assert first["status"] == second["status"] == "success"
    assert len(execution_titles) == 1
    assert first["structure"] == second["structure"]
    assert first["source_artifact_path"] == second["source_artifact_path"]
    assert Path(first["source_artifact_path"]).name == (
        f"source-{first['operation_id']}.pdf"
    )
    assert len(list((Path(client.workspace_root) / "artifacts").glob("source-*.pdf"))) == 1


def test_concurrent_same_operation_returns_the_single_committed_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _install_pageindex_runtime(tmp_path)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"same-operation-concurrent-source")
    rendezvous = threading.Barrier(2)
    _install_index_runner(monkeypatch, rendezvous=rendezvous)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_index_pdf, client, source) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert all(result["status"] == "success" for result in results)
    artifact_path = Path(results[0]["structure_json_path"])
    envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert results[0]["structure"] == results[1]["structure"] == envelope["structure"]
    assert results[0]["source_artifact_path"] == results[1]["source_artifact_path"]
    assert envelope["source_artifact_name"] == Path(
        results[0]["source_artifact_path"]
    ).name
    assert len(list(artifact_path.parent.glob("source-*.pdf"))) == 1


def test_same_operation_callers_remain_readable_after_source_gc(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _install_pageindex_runtime(tmp_path)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"same-operation-gc-source")
    _install_index_runner(monkeypatch)

    results = [_index_pdf(client, source), _index_pdf(client, source)]
    source_paths = [Path(result["source_artifact_path"]) for result in results]
    old_timestamp = 1_700_000_000
    os.utime(source_paths[0], (old_timestamp, old_timestamp))

    removed = client.collect_orphaned_source_artifacts(min_age_seconds=0)

    assert removed == 0
    assert source_paths[0] == source_paths[1]
    assert all(path.is_file() for path in source_paths)
