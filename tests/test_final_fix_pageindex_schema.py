"""Strict recursive schema and traversal regressions for PageIndex results."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import fitz
import pytest

from integrations.pageindex.pageindex_client import PageIndexClient


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


def _write_pdf(path: Path, page_count: int = 2) -> None:
    document = fitz.open()
    try:
        for _ in range(page_count):
            document.new_page(width=72, height=72)
        document.save(path)
    finally:
        document.close()


def _node(**overrides) -> dict[str, object]:
    value: dict[str, object] = {
        "title": "Chapter",
        "node_id": "0001",
        "start_index": 1,
        "end_index": 2,
        "nodes": [],
    }
    value.update(overrides)
    return value


def _install_result_runner(monkeypatch, structure: list[object]) -> None:
    def run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "Python 3.13", "")
        flag = "--pdf_path" if "--pdf_path" in command else "--md_path"
        staged_source = Path(command[command.index(flag) + 1])
        result_path = (
            Path(kwargs["cwd"])
            / "results"
            / f"{staged_source.stem}_structure.json"
        )
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            json.dumps({"doc_name": staged_source.name, "structure": structure}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)


@pytest.mark.parametrize(
    "invalid_child",
    [
        _node(title=7),
        _node(node_id=7),
        _node(start_index=True),
        _node(end_index=False),
        _node(start_index=0),
        _node(end_index=0),
        _node(start_index=2, end_index=1),
        _node(nodes={}),
        {
            "title": "Missing children",
            "node_id": "0002",
            "start_index": 1,
            "end_index": 1,
        },
    ],
    ids=[
        "title-not-string",
        "node-id-not-string",
        "bool-start-index",
        "bool-end-index",
        "start-before-first-page",
        "end-before-first-page",
        "reversed-range",
        "children-not-list",
        "children-missing",
    ],
)
def test_invalid_recursive_pdf_node_is_rejected_without_publication(
    tmp_path: Path,
    monkeypatch,
    invalid_child: dict[str, object],
) -> None:
    client = _runtime_client(tmp_path)
    source = tmp_path / "source.pdf"
    _write_pdf(source)
    structure = [_node(nodes=[invalid_child])]
    _install_result_runner(monkeypatch, structure)

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RESULT.INVALID_SCHEMA"
    artifact_root = Path(client.workspace_root) / "artifacts"
    assert not list(artifact_root.glob("*.json"))
    assert not list(artifact_root.glob("source-*.pdf"))


def test_pdf_node_range_cannot_exceed_physical_page_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _runtime_client(tmp_path)
    source = tmp_path / "two-pages.pdf"
    _write_pdf(source, page_count=2)
    _install_result_runner(monkeypatch, [_node(end_index=3)])

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.RESULT.INVALID_SCHEMA"
    assert not (Path(client.workspace_root) / "artifacts").exists()


def test_pdf_with_unreadable_physical_page_count_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _runtime_client(tmp_path)
    source = tmp_path / "corrupt.pdf"
    source.write_bytes(b"%PDF-1.7\ncorrupt")
    runner_called = False

    def must_not_run(command, **kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("invalid PDF must fail before the external runner")

    monkeypatch.setattr(subprocess, "run", must_not_run)

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.INPUT.INVALID_PDF"
    assert runner_called is False
    assert not (Path(client.workspace_root) / "artifacts").exists()


def test_password_encrypted_pdf_fails_before_external_runner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _runtime_client(tmp_path)
    source = tmp_path / "encrypted.pdf"
    document = fitz.open()
    try:
        document.new_page(width=72, height=72)
        document.save(
            source,
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw="synthetic-owner",
            user_pw="synthetic-user",
        )
    finally:
        document.close()
    runner_called = False

    def must_not_run(command, **kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("encrypted PDF must fail before the external runner")

    monkeypatch.setattr(subprocess, "run", must_not_run)

    result = client.index_pdf(str(source))

    assert result["status"] == "failed"
    assert result["error_code"] == "PAGEINDEX.INPUT.INVALID_PDF"
    assert runner_called is False
    assert not (Path(client.workspace_root) / "artifacts").exists()


def test_markdown_positions_use_their_own_unbounded_positive_range(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _runtime_client(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("# Chapter\n", encoding="utf-8")
    _install_result_runner(
        monkeypatch,
        [_node(start_index=500, end_index=999)],
    )

    result = client.index_md(str(source))

    assert result["status"] == "success"
    assert result["structure"][0]["end_index"] == 999


def test_find_nodes_by_title_skips_malformed_legacy_nodes_without_type_error() -> None:
    result = PageIndexClient.find_nodes_by_title(
        {
            "structure": [
                None,
                {"title": 42, "nodes": []},
                {"title": "bad children", "nodes": {"title": "not-a-list"}},
                _node(title="Matching chapter"),
            ]
        },
        "matching",
    )

    assert result == [
        {
            "title": "Matching chapter",
            "node_id": "0001",
            "start_index": 1,
            "end_index": 2,
            "summary": None,
        }
    ]
