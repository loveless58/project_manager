from __future__ import annotations

import hashlib
from pathlib import Path

from connectors.artifacts.run_artifact_store import RunArtifactStore
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry
from skills.document_management.document_facts.skill import DocumentFactsSkill
from skills.document_management.document_review.skill import DocumentReviewSkill


def _registry(source_root: Path) -> StorageBindingRegistry:
    return StorageBindingRegistry(
        [
            StorageBinding(
                "incoming", "local", "node", "business://incoming/", source_root,
                ("source",), True, False,
            )
        ]
    )


def _parsed(*, status: str, text: str, source_ref: dict[str, str], reason: str = "") -> dict[str, object]:
    result = {
        "schema_version": "structured_document.v1",
        "status": status,
        "source_ref": source_ref,
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "media_type": "text/plain",
        "parser": "native_text" if status == "success" else "unavailable",
        "text": text,
        "pages": [{"page": 1, "text": text, "confidence": None}] if text else [],
        "tables": [],
        "fields": {},
    }
    if status == "needs_review":
        result["reason"] = reason
    return result


class _ParseSkill:
    def __init__(self, *, status: str, text: str = "", reason: str = "") -> None:
        self._status, self._text, self._reason = status, text, reason

    def parse(self, _path: str, *, source_ref: dict[str, str]) -> dict[str, object]:
        return _parsed(status=self._status, text=self._text, source_ref=source_ref, reason=self._reason)


def _build_agent(tmp_path: Path, database_path, parse_skill: _ParseSkill):
    from agents.document_management.agent import DocumentManagementAgent

    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "input.txt"
    source.write_text("source sentinel", encoding="utf-8")
    return (
        DocumentManagementAgent(
            database_path.path,
            _registry(source_root),
            parse_skill,
            DocumentFactsSkill(),
            DocumentReviewSkill(),
            RunArtifactStore(tmp_path / "results", protected_roots=(source_root,)),
            source_binding_id="incoming",
        ),
        source,
    )


INVOICE_TEXT = """电子发票（普通发票）
发票号码：26112000002732686171
购方名称：北京华胜天成科技股份有限公司
销方名称：普华和诚（北京）信息有限公司
价税合计（小写）：¥48000.00
"""


def test_agent_progressively_loads_parse_facts_review_and_publishes_artifacts(tmp_path, database_path) -> None:
    agent, source = _build_agent(tmp_path, database_path, _ParseSkill(status="success", text=INVOICE_TEXT))

    result = agent.prepare([str(source)], goal="解析发票，不归档")

    assert result["status"] == "awaiting_review"
    assert result["loaded_skills"] == [
        "document_parse", "document_facts", "document_review"
    ]
    assert result["items"][0]["decision"]["suggested_action"] == "no_archive"
    assert source.read_text("utf-8") == "source sentinel"
    assert result["artifacts"]["run_summary"]["logical_uri"].startswith("projection://runs/")


def test_parse_failure_loads_parse_and_review_but_not_invoice_facts(tmp_path, database_path) -> None:
    agent, source = _build_agent(
        tmp_path, database_path, _ParseSkill(status="needs_review", reason="NATIVE.UNSUPPORTED_MEDIA")
    )

    result = agent.prepare([str(source)], goal="解析，不归档")

    assert result["loaded_skills"] == ["document_parse", "document_review"]
    assert result["items"][0]["decision"]["reasons"] == ["NATIVE.UNSUPPORTED_MEDIA"]


def test_non_invoice_loads_parse_and_review_and_still_publishes_result(tmp_path, database_path) -> None:
    agent, source = _build_agent(tmp_path, database_path, _ParseSkill(status="success", text="会议纪要"))

    result = agent.prepare([str(source)], goal="解析，不归档")

    assert result["loaded_skills"] == ["document_parse", "document_review"]
    assert result["items"][0]["artifacts"]["document_json"]["kind"] == "document_json"
