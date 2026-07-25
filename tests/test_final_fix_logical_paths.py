"""Cross-platform regression tests for canonical POSIX logical paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from integrations.document_store.local_store import (
    DocumentStorePathError,
    LocalDocumentStore,
)
from integrations.projections.filesystem_writer import (
    FilesystemProjectionWriter,
    ProjectionPathError,
)
from platform_core.models import DocumentRef, ProjectionRequest


INVALID_LOGICAL_PATHS = (
    "",
    "/absolute.json",
    "C:/absolute.json",
    "C:drive-relative.json",
    "//server/share/file.json",  # repo-hygiene: allow=synthetic-path
    r"\\server\share\file.json",  # repo-hygiene: allow=synthetic-path
    r"folder\file.json",
    ".",
    "./file.json",
    "nested/./file.json",
    "..",
    "../file.json",
    "nested/../file.json",
    "nested//file.json",
    "nested/file.json/",
)


@pytest.mark.parametrize("logical_path", INVALID_LOGICAL_PATHS)
def test_local_store_rejects_noncanonical_paths_on_every_host(
    tmp_path: Path,
    logical_path: str,
) -> None:
    ref = DocumentRef("local", logical_path, f"local://{logical_path}")

    with pytest.raises(DocumentStorePathError):
        LocalDocumentStore(tmp_path).stat(ref)


@pytest.mark.parametrize("logical_path", INVALID_LOGICAL_PATHS)
def test_projection_writer_rejects_noncanonical_paths_on_every_host(
    tmp_path: Path,
    logical_path: str,
) -> None:
    request = ProjectionRequest("json", logical_path, "{}", "application/json")

    with pytest.raises(ProjectionPathError):
        FilesystemProjectionWriter(tmp_path).write(request)


def test_local_store_requires_logical_uri_to_match_canonical_object_key(
    tmp_path: Path,
) -> None:
    source = tmp_path / "tenant" / "document.txt"
    source.parent.mkdir()
    source.write_text("content", encoding="utf-8")
    ref = DocumentRef("local", "tenant/document.txt", "local://other/document.txt")

    with pytest.raises(DocumentStorePathError, match="logical URI"):
        LocalDocumentStore(tmp_path).stat(ref)


def test_both_adapters_preserve_the_same_posix_logical_path(
    tmp_path: Path,
) -> None:
    store_root = tmp_path / "objects"
    projection_root = tmp_path / "projections"
    source = store_root / "tenant" / "项目.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    logical_path = "tenant/项目.json"
    document_ref = DocumentRef("local", logical_path, f"local://{logical_path}")

    stat = LocalDocumentStore(store_root).stat(document_ref)
    projection_ref = FilesystemProjectionWriter(projection_root).write(
        ProjectionRequest("json", logical_path, "{}", "application/json")
    )

    assert stat.ref.logical_uri == "local://tenant/项目.json"
    assert projection_ref.logical_uri == "projection://tenant/项目.json"
    assert (projection_root / "tenant" / "项目.json").is_file()
