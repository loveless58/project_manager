import hashlib

from agents.file_organizer.agent import FileOrganizerAgent
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry


class _MixedParseSkill:
    def parse(self, path, *, source_ref):
        if path.endswith("bad.pdf"):
            with open(path, "rb") as file_handle:
                content_hash = hashlib.sha256(file_handle.read()).hexdigest()
            return {
                "schema_version": "structured_document.v1",
                "status": "needs_review",
                "reason": "OCR.PROVIDERS_UNAVAILABLE",
                "source_ref": source_ref,
                "content_hash": content_hash,
                "media_type": "application/pdf",
                "parser": "unavailable",
                "text": "",
                "pages": [],
                "tables": [],
                "fields": {},
            }
        with open(path, "rb") as file_handle:
            content_hash = hashlib.sha256(file_handle.read()).hexdigest()
        return {
            "schema_version": "structured_document.v1",
            "status": "success",
            "source_ref": source_ref,
            "content_hash": content_hash,
            "media_type": "text/markdown",
            "parser": "native_markdown",
            "text": "合成文本",
            "pages": [],
            "tables": [],
            "fields": {},
        }


def _registry(source_root, archive_root):
    return StorageBindingRegistry([
        StorageBinding("incoming", "local", "node", "business://incoming/", source_root, ("source",), True, False),
        StorageBinding("archive", "local", "node", "business://archive/", archive_root, ("archive",), True, True),
    ])


def test_agent_records_review_item_for_every_explicit_parse_failure(tmp_path, database_path):
    source_root = tmp_path / "source"
    archive_root = tmp_path / "archive"
    source_root.mkdir()
    archive_root.mkdir()
    good = source_root / "good.md"
    bad = source_root / "bad.pdf"
    good.write_text("synthetic", encoding="utf-8")
    bad.write_bytes(b"not a pdf")
    agent = FileOrganizerAgent(
        database_path.path,
        _registry(source_root, archive_root),
        _MixedParseSkill(),
        source_binding_id="incoming",
        archive_binding_id="archive",
    )

    prepared = agent.prepare([str(good), str(bad)], goal="整理合成资料")

    assert len(prepared["items"]) == 2
    assert all(item["item_id"] for item in prepared["items"])
    assert prepared["items"][1]["proposal"] == {
        "schema_version": "organization_proposal.v1",
        "status": "needs_review",
        "reasons": ["OCR.PROVIDERS_UNAVAILABLE"],
    }
    assert good.exists()
    assert bad.exists()
