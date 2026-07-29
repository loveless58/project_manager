"""Controlled local CLI for the File Organizer Agent.

Only explicit paths are accepted. ``prepare`` never moves a file; physical
movement is reachable solely through ``execute-confirmed`` with a separate
confirmation JSON document.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agents.file_organizer.agent import FileOrganizerAgent
from ocr.provider_chain import OcrProviderChain
from ocr.providers import EasyOcrProvider, MineruProvider, RapidOcrProvider
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry
from skills.file_organizer.document_parse import DocumentParseSkill


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="File Organizer Agent")
    parser.add_argument("--config", required=True, help="node-local configuration JSON")
    subcommands = parser.add_subparsers(dest="command", required=True)
    prepare = subcommands.add_parser("prepare")
    prepare.add_argument("--goal", required=True)
    prepare.add_argument("files", nargs="+")
    execute = subcommands.add_parser("execute-confirmed")
    execute.add_argument("--run-id", required=True)
    execute.add_argument("--confirmations", required=True, help="node-local confirmation JSON")
    arguments = parser.parse_args(argv)
    try:
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
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("configuration must be an object")
    bindings = []
    for raw in config.get("bindings", []):
        if not isinstance(raw, dict):
            raise ValueError("binding must be an object")
        bindings.append(StorageBinding(
            binding_id=str(raw["binding_id"]),
            provider=str(raw["provider"]),
            node_id=str(raw["node_id"]),
            logical_root=str(raw["logical_root"]),
            physical_root=Path(str(raw["physical_root"])),
            roles=tuple(raw.get("roles", ())),
            readable=bool(raw.get("readable")),
            writable=bool(raw.get("writable")),
            enabled=bool(raw.get("enabled", True)),
        ))
    chain = OcrProviderChain([RapidOcrProvider(), MineruProvider(), EasyOcrProvider()])
    return FileOrganizerAgent(
        Path(str(config["database_path"])),
        StorageBindingRegistry(bindings),
        DocumentParseSkill(ocr_chain=chain),
        source_binding_id=str(config["source_binding_id"]),
        archive_binding_id=str(config["archive_binding_id"]),
        pageindex_min_text_length=int(config.get("pageindex_min_text_length", 16_000)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
