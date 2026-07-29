"""Publish immutable-looking review artifacts under an external results root."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from contracts.artifact_reference import build_artifact_reference
from integrations.projections.filesystem_writer import FilesystemProjectionWriter
from platform_core.models import ProjectionRequest


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ArtifactConflictError(RuntimeError):
    """A run artifact exists already but does not represent the same content."""


class RunArtifactStore:
    """Write canonical JSON and derived Markdown outside source storage.

    The connector never exposes a physical result path in its return contract.
    It treats each logical artifact target as write-once: a retry with identical
    bytes succeeds, while a changed retry fails for human review.
    """

    def __init__(
        self,
        root: str | Path | FilesystemProjectionWriter,
        *,
        protected_roots: tuple[str | Path, ...] = (),
    ) -> None:
        if isinstance(root, FilesystemProjectionWriter):
            if protected_roots:
                raise ValueError("protected roots belong to the projection writer")
            self._writer = root
        else:
            self._writer = FilesystemProjectionWriter(root, protected_roots=protected_roots)

    def publish_document(
        self, run: Mapping[str, Any], document_result: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Publish `document.json` and an auditable derived `review.md`."""
        run_id = _run_id(run)
        document_id = _document_id(document_result)
        base = f"runs/{run_id}/documents/{document_id}"
        document_json = _canonical_json(document_result)
        json_ref = self._publish(
            "document_json",
            f"{base}/document.json",
            document_json,
            "application/json",
        )
        markdown_ref = self._publish(
            "review_markdown",
            f"{base}/review.md",
            _render_review(document_result),
            "text/markdown",
        )
        return json_ref, markdown_ref

    def publish_run(
        self,
        run: Mapping[str, Any],
        document_results: list[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Publish the final run manifest and a human-oriented summary."""
        run_id = _run_id(run)
        if not isinstance(document_results, list):
            raise ValueError("document results are invalid")
        payload = {
            "schema_version": "agent_run_artifacts.v1",
            "run": dict(run),
            "documents": [dict(item) for item in document_results],
        }
        run_json = self._publish(
            "run_json",
            f"runs/{run_id}/run.json",
            _canonical_json(payload),
            "application/json",
        )
        run_summary = self._publish(
            "run_summary",
            f"runs/{run_id}/run-summary.md",
            _render_run_summary(run, document_results),
            "text/markdown",
        )
        return run_json, run_summary

    def _publish(
        self, kind: str, relative_path: str, content: str, media_type: str
    ) -> dict[str, Any]:
        target, canonical = self._writer._resolve(relative_path)
        payload = content.encode("utf-8")
        if target.exists():
            if not target.is_file() or target.read_bytes() != payload:
                raise ArtifactConflictError(
                    f"artifact {canonical} already exists with different bytes"
                )
            return build_artifact_reference(
                kind=kind,
                logical_uri=f"projection://{canonical}",
                sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
            )
        projection = self._writer.write(
            ProjectionRequest(kind, relative_path, content, media_type)
        )
        return build_artifact_reference(
            kind=kind,
            logical_uri=projection.logical_uri,
            sha256=projection.sha256,
            size_bytes=projection.size_bytes,
        )


def _run_id(run: Mapping[str, Any]) -> str:
    run_id = run.get("run_id") if isinstance(run, Mapping) else None
    if not isinstance(run_id, str) or _SAFE_IDENTIFIER.fullmatch(run_id) is None:
        raise ValueError("run id is invalid")
    return run_id


def _document_id(document_result: Mapping[str, Any]) -> str:
    document = document_result.get("document") if isinstance(document_result, Mapping) else None
    document_id = document.get("document_id") if isinstance(document, Mapping) else None
    if not isinstance(document_id, str) or _SAFE_IDENTIFIER.fullmatch(document_id) is None:
        raise ValueError("document id is invalid")
    return document_id


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _render_review(document_result: Mapping[str, Any]) -> str:
    document = document_result["document"]
    parse = document_result["parse"]
    decision = document_result["decision"]
    facts = document_result.get("facts", {})
    reasons = decision.get("reasons", [])
    lines = [
        "# 文档审查",
        "",
        f"- 文档 ID：{document['document_id']}",
        f"- 来源：{document['source_ref'].get('logical_uri', '')}",
        "- 源文件是否修改：否",
        "",
        "## 解析",
        "",
        f"- 状态：{parse.get('status', '')}",
        f"- 解析器：{parse.get('parser', '')}",
        "",
        "## 结构化事实",
        "",
        "```json",
        json.dumps(facts, ensure_ascii=False, sort_keys=True, indent=2),
        "```",
        "",
        "## 审查结论",
        "",
        f"- 状态：{decision.get('status', '')}",
        f"- 建议动作：{decision.get('suggested_action', '')}",
        f"- 原因：{', '.join(str(reason) for reason in reasons) if reasons else '无'}",
        "",
    ]
    return "\n".join(lines)


def _render_run_summary(
    run: Mapping[str, Any], document_results: list[Mapping[str, Any]]
) -> str:
    lines = [
        "# 文档管理运行摘要",
        "",
        f"- 运行 ID：{_run_id(run)}",
        f"- 目标：{run.get('goal', '')}",
        "- 源文件是否修改：否",
        f"- 文档数：{len(document_results)}",
        "",
        "## 文档",
        "",
    ]
    for result in document_results:
        document = result.get("document", {})
        decision = result.get("decision", {})
        lines.extend(
            [
                f"- `{document.get('document_id', '')}`：{decision.get('suggested_action', '')}",
            ]
        )
    return "\n".join(lines) + "\n"


__all__ = ["ArtifactConflictError", "RunArtifactStore"]
