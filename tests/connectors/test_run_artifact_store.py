from __future__ import annotations

import json
from pathlib import Path

import pytest


RUN = {"run_id": "run-1", "goal": "解析指定文档，不归档", "status": "prepared"}
DOCUMENT_RESULT = {
    "schema_version": "document_result.v1",
    "run": RUN,
    "document": {
        "document_id": "doc-1",
        "source_ref": {"logical_uri": "business://source/invoice.pdf"},
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
    },
    "parse": {
        "schema_version": "structured_document.v1",
        "status": "success",
        "source_ref": {"logical_uri": "business://source/invoice.pdf"},
        "content_hash": "a" * 64,
        "media_type": "application/pdf",
        "parser": "native_pdf",
        "text": "电子发票正文",
        "pages": [{"page": 1, "text": "电子发票正文", "confidence": None}],
        "tables": [],
        "fields": {},
    },
    "facts": {"document_type": {"value": "invoice", "confidence": 0.99, "evidence": []}},
    "decision": {"status": "needs_review", "suggested_action": "no_archive", "reasons": []},
    "artifacts": [],
}


def test_publish_document_writes_run_scoped_json_and_markdown(tmp_path: Path) -> None:
    from connectors.artifacts.run_artifact_store import RunArtifactStore

    store = RunArtifactStore(tmp_path)
    json_ref, markdown_ref = store.publish_document(RUN, DOCUMENT_RESULT)

    document_json = tmp_path / "runs/run-1/documents/doc-1/document.json"
    review_markdown = tmp_path / "runs/run-1/documents/doc-1/review.md"
    assert document_json.is_file()
    assert review_markdown.is_file()
    assert json.loads(document_json.read_text("utf-8")) == DOCUMENT_RESULT
    assert document_json.read_bytes().endswith(b"\n")
    assert json_ref["logical_uri"].startswith("projection://runs/run-1/")
    assert markdown_ref["kind"] == "review_markdown"
    assert "源文件是否修改：否" in review_markdown.read_text("utf-8")


def test_publish_document_rejects_root_overlapping_a_protected_source(tmp_path: Path) -> None:
    from connectors.artifacts.run_artifact_store import RunArtifactStore
    from integrations.projections.filesystem_writer import ProjectionPathError

    with pytest.raises(ProjectionPathError, match="must not overlap"):
        RunArtifactStore(tmp_path, protected_roots=(tmp_path / "source",))


def test_publish_document_rejects_different_bytes_for_same_artifact(tmp_path: Path) -> None:
    from connectors.artifacts.run_artifact_store import ArtifactConflictError, RunArtifactStore

    store = RunArtifactStore(tmp_path)
    store.publish_document(RUN, DOCUMENT_RESULT)
    changed = {
        **DOCUMENT_RESULT,
        "facts": {"document_type": {"value": "invoice", "confidence": 0.5, "evidence": []}},
    }

    with pytest.raises(ArtifactConflictError, match="already exists with different bytes"):
        store.publish_document(RUN, changed)


def test_publish_run_writes_only_run_level_artifacts(tmp_path: Path) -> None:
    from connectors.artifacts.run_artifact_store import RunArtifactStore

    run_json, run_summary = RunArtifactStore(tmp_path).publish_run(RUN, [DOCUMENT_RESULT])

    assert run_json["kind"] == "run_json"
    assert run_summary["kind"] == "run_summary"
    assert (tmp_path / "runs/run-1/run.json").is_file()
    assert (tmp_path / "runs/run-1/run-summary.md").is_file()
