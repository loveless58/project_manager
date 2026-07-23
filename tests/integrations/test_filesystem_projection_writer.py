from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from platform_core.models import ProjectionRequest
from platform_core.ports import ProjectionWriter


def test_projection_writer_writes_utf8_and_hash(tmp_path: Path) -> None:
    from integrations.projections.filesystem_writer import FilesystemProjectionWriter

    writer = FilesystemProjectionWriter(tmp_path)
    request = ProjectionRequest("markdown", "projects/a.md", "# 项目A\n", "text/markdown")

    result = writer.write(request)

    payload = "# 项目A\n".encode("utf-8")
    assert isinstance(writer, ProjectionWriter)
    assert (tmp_path / "projects" / "a.md").read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.size_bytes == len(payload)


def test_projection_writer_rejects_path_escape(tmp_path: Path) -> None:
    from integrations.projections.filesystem_writer import FilesystemProjectionWriter, ProjectionPathError

    writer = FilesystemProjectionWriter(tmp_path)
    request = ProjectionRequest("json", "../outside.json", "{}", "application/json")

    with pytest.raises(ProjectionPathError, match="outside configured root"):
        writer.write(request)


def test_projection_writer_is_idempotent_for_identical_utf8_json(tmp_path: Path) -> None:
    from integrations.projections.filesystem_writer import FilesystemProjectionWriter

    writer = FilesystemProjectionWriter(tmp_path)
    request = ProjectionRequest("json", "exports/项目.json", '{"项目":"A"}\n', "application/json")

    first = writer.write(request)
    second = writer.write(request)

    assert second == first
    assert (tmp_path / "exports" / "项目.json").read_bytes() == request.content.encode("utf-8")


def test_projection_writer_removes_temporary_file_when_replacement_fails(tmp_path: Path) -> None:
    from integrations.projections.filesystem_writer import FilesystemProjectionWriter

    target = tmp_path / "exports" / "blocked.json"
    target.parent.mkdir()
    target.mkdir()
    request = ProjectionRequest("json", "exports/blocked.json", "{}", "application/json")

    with pytest.raises(OSError):
        FilesystemProjectionWriter(tmp_path).write(request)

    assert list(target.parent.glob("*.tmp")) == []
