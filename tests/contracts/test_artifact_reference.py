from __future__ import annotations

import pytest

from contracts.artifact_reference import build_artifact_reference


def test_artifact_reference_is_a_pathless_projection_contract() -> None:
    artifact = build_artifact_reference(
        kind="document_json",
        logical_uri="projection://runs/run-1/documents/doc-1/document.json",
        sha256="a" * 64,
        size_bytes=42,
    )

    assert artifact == {
        "schema_version": "artifact_reference.v1",
        "kind": "document_json",
        "logical_uri": "projection://runs/run-1/documents/doc-1/document.json",
        "sha256": "a" * 64,
        "size_bytes": 42,
    }


def test_artifact_reference_rejects_non_projection_uri() -> None:
    with pytest.raises(ValueError, match="projection"):
        build_artifact_reference(
            kind="document_json",
            logical_uri="file:///results/document.json",
            sha256="a" * 64,
            size_bytes=42,
        )
