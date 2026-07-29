"""Controlled local CLI for the legacy and review-only document workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from agents.document_management.agent import DocumentManagementAgent
from agents.file_organizer.agent import FileOrganizerAgent
from connectors.artifacts.run_artifact_store import RunArtifactStore
from integrations.pageindex.structure_index import PageIndexStructureIndex
from integrations.projections.filesystem_writer import FilesystemProjectionWriter
from ocr.provider_chain import OcrProviderChain
from ocr.providers import EasyOcrProvider, MineruProvider, RapidOcrProvider
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry
from skills.document_management.document_facts.skill import DocumentFactsSkill
from skills.document_management.document_parse.skill import DocumentParseWorkflowSkill
from skills.document_management.document_review.skill import DocumentReviewSkill
from skills.file_organizer.document_parse import DocumentParseSkill


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="File Organizer Agent")
    parser.add_argument("--config", required=True, help="node-local configuration JSON")
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare")
    prepare.add_argument("--goal", required=True)
    prepare.add_argument("files", nargs="+")
    review = subcommands.add_parser("prepare-review")
    review.add_argument("--goal", required=True)
    review.add_argument("files", nargs="+")
    execute = subcommands.add_parser("execute-confirmed")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--confirmations", required=True, help="node-local confirmation JSON")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "prepare-review":
            result = _build_document_management_agent(Path(arguments.config)).prepare(
                arguments.files, goal=arguments.goal
            )
        else:
            agent = _build_agent(Path(arguments.config))
            if arguments.command == "prepare":
                result = agent.prepare(arguments.files, goal=arguments.goal)
            else:
                confirmations = json.loads(Path(arguments.confirmations).read_text(encoding="utf-8"))
                if not isinstance(confirmations, list):
                    raise ValueError("confirmation JSON must be a list")
                result = agent.execute_confirmed(arguments.run_id, confirmations)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "reason": "FILE_ORGANIZER.CONFIGURATION", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _build_agent(config_path: Path) -> FileOrganizerAgent:
    config, registry = _load_config(config_path)
    chain = OcrProviderChain([RapidOcrProvider(), MineruProvider(), EasyOcrProvider()])
    pageindex = config.get("pageindex", {})
    structure_index = None
    if isinstance(pageindex, dict) and pageindex.get("enabled"):
        pageindex_dir = str(pageindex.get("directory", "")).strip()
        if not pageindex_dir or pageindex_dir.startswith("<"):
            raise ValueError("enabled PageIndex requires a node-local directory")
        structure_index = PageIndexStructureIndex(pageindex_dir)
    return FileOrganizerAgent(
        Path(str(config["database_path"])), registry, DocumentParseSkill(ocr_chain=chain),
        source_binding_id=str(config["source_binding_id"]),
        archive_binding_id=str(config["archive_binding_id"]),
        structure_index=structure_index,
        pageindex_min_text_length=int(config.get("pageindex_min_text_length", 16_000)),
    )


def _build_document_management_agent(config_path: Path) -> DocumentManagementAgent:
    config, registry = _load_config(config_path)
    raw_results_root = str(config.get("results_root", "")).strip()
    if not raw_results_root or raw_results_root.startswith("<"):
        raise ValueError("prepare-review requires a node-local results_root")
    results_root = Path(raw_results_root).expanduser().resolve()
    repository_root = REPOSITORY_ROOT.resolve()
    if results_root == repository_root or repository_root in results_root.parents:
        raise ValueError("results_root must be outside the Git repository")
    protected_roots = tuple(binding.physical_root for binding in registry.bindings if binding.readable)
    writer = FilesystemProjectionWriter(results_root, protected_roots=protected_roots)
    chain = OcrProviderChain([RapidOcrProvider(), MineruProvider(), EasyOcrProvider()])
    return DocumentManagementAgent(
        Path(str(config["database_path"])), registry,
        DocumentParseWorkflowSkill(DocumentParseSkill(ocr_chain=chain)),
        DocumentFactsSkill(), DocumentReviewSkill(), RunArtifactStore(writer),
        source_binding_id=str(config["source_binding_id"]),
    )


def _load_config(config_path: Path) -> tuple[dict[str, Any], StorageBindingRegistry]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    bindings = []
    for raw in config.get("bindings", []):
        if not isinstance(raw, dict):
            raise ValueError("binding must be an object")
        bindings.append(StorageBinding(
            binding_id=str(raw["binding_id"]), provider=str(raw["provider"]),
            node_id=str(raw["node_id"]), logical_root=str(raw["logical_root"]),
            physical_root=Path(str(raw["physical_root"])), roles=tuple(raw.get("roles", ())),
            readable=bool(raw.get("readable")), writable=bool(raw.get("writable")),
            enabled=bool(raw.get("enabled", True)),
        ))
    return config, StorageBindingRegistry(bindings)


if __name__ == "__main__":
    raise SystemExit(main())
