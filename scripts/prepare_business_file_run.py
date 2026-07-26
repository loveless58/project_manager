#!/usr/bin/env python3
"""Prepare a safe, review-only business-file judgement run.

This command accepts explicit storage bindings and never applies review feedback or
confirms an archive plan. OCR is deliberately disabled for this entry point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app_bootstrap.composition import build_runtime_adapters
from common.provider_config import ProviderConfigurationError
from integrations.business_context import JsonBusinessContextProvider
from integrations.llm import OpenAICompatibleInterpreter
from ocr.providers import DisabledOcrProvider
from contracts.agent_judgement import AgentJudgementSchemaError, validate_run_id
from contracts.archive_run_artifacts import ArchiveRunArtifactError, strict_json_load, validate_task5_collection_artifact
from platform_core import load_app_settings
from platform_core.storage_bindings import StorageBindingError
from services.agent_judgement_gateway import AgentJudgementGateway
from services.document_interpretation import DocumentInterpretationService
from services.retrieval_service import RetrievalService
from tools.data_cleaning_tools import DataCleaningTools


class InputBindingError(ValueError):
    """An explicit CLI binding does not meet the read-only run contract."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare review artifacts without physically archiving files.")
    parser.add_argument("--config", required=True, help="Node-local project-manager JSON configuration.")
    parser.add_argument("--context", required=True, help="Explicit business_context_catalog.v1 JSON file.")
    parser.add_argument("--source-binding", required=True, help="Enabled readable source binding ID.")
    parser.add_argument("--target-binding", default="", help="Optional enabled writable archive-target binding ID.")
    parser.add_argument(
        "--interpreter-mode",
        choices=("configured_llm", "agent", "disabled"),
        default="configured_llm",
    )
    parser.add_argument("--resume-run", default="")
    parser.add_argument("--agent-response", default="")
    parser.add_argument("files", nargs="+", help="Explicit files contained by --source-binding.")
    return parser


def _source_binding(registry: Any, binding_id: str) -> Any:
    try:
        binding = registry.binding_for_id(binding_id)
    except StorageBindingError as exc:
        raise InputBindingError("SOURCE_BINDING.INVALID") from exc
    if not binding.enabled or not binding.readable or "source" not in binding.roles:
        raise InputBindingError("SOURCE_BINDING.INVALID")
    return binding


def _target_binding(registry: Any, binding_id: str) -> None:
    if not binding_id:
        return
    try:
        binding = registry.binding_for_id(binding_id)
    except StorageBindingError as exc:
        raise InputBindingError("TARGET_BINDING.INVALID") from exc
    if not binding.enabled or not binding.writable or "archive_target" not in binding.roles:
        raise InputBindingError("TARGET_BINDING.INVALID")


def _validate_sources(registry: Any, source_binding_id: str, files: Sequence[str]) -> list[str]:
    validated: list[str] = []
    for raw_path in files:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise InputBindingError("SOURCE_FILE.INVALID")
        try:
            reference = registry.document_ref_from_path(path)
        except StorageBindingError as exc:
            raise InputBindingError("SOURCE_BINDING.OUT_OF_SCOPE") from exc
        if reference.binding_id != source_binding_id:
            raise InputBindingError("SOURCE_BINDING.OUT_OF_SCOPE")
        validated.append(str(path))
    return validated


def _agent_manifest_entries(run_dir: str, run_id: str) -> list[dict[str, Any]]:
    try:
        validated_run_id = validate_run_id(run_id)
        manifest = strict_json_load(
            str(Path(run_dir) / "input_manifest.json")
        )
        validate_task5_collection_artifact(
            manifest, kind="input_manifest", run_id=validated_run_id,
        )
        entries = manifest["files"]
        binding_ids = {
            item["source_ref"]["binding_id"]
            for item in entries
        }
        if len(binding_ids) != 1:
            raise ValueError("agent manifest source binding")
        return entries
    except (
        AgentJudgementSchemaError,
        ArchiveRunArtifactError,
        OSError,
        TypeError,
        ValueError,
        KeyError,
    ) as exc:
        raise InputBindingError("AGENT_JUDGEMENT.RUN_INPUT_INVALID") from exc


def _agent_manifest_source_binding(entries: Sequence[dict[str, Any]]) -> str:
    binding_ids = {item["source_ref"]["binding_id"] for item in entries}
    if len(binding_ids) != 1:
        raise InputBindingError("AGENT_JUDGEMENT.RUN_INPUT_INVALID")
    return next(iter(binding_ids))


def _summary(*, prepared: dict[str, Any], verification: dict[str, Any], audit: dict[str, Any], feedback: dict[str, Any], archive: dict[str, Any], source_binding: str, target_binding: str) -> dict[str, Any]:
    failures = prepared.get("failures") if isinstance(prepared.get("failures"), list) else []
    failure_codes = sorted({str(item.get("blocked_reason", "DOCUMENT_PROCESSING.BLOCKED")) for item in failures if isinstance(item, dict)})
    blocked = bool(failures)
    return {
        "schema_version": "business_file_judgement.cli.v1",
        "status": "blocked" if blocked else "needs_review",
        "run_id": prepared.get("run_id", ""),
        "source_binding": source_binding,
        "target_binding": target_binding or None,
        "processed": int(prepared.get("processed", 0)),
        "failed": int(prepared.get("failed", 0)),
        "failure_codes": failure_codes,
        "artifacts": sorted((prepared.get("artifacts") or {}).keys()),
        "verification_status": verification.get("status", ""),
        "audit_status": audit.get("status", ""),
        "feedback_status": feedback.get("status", ""),
        "archive_execution": {"status": archive.get("status", ""), "confirmed": False},
    }


def _agent_summary(*, result: dict[str, Any], source_binding: str | None) -> dict[str, Any]:
    """Return only host-safe identifiers and status for an agent-gateway result."""
    status = str(result.get("status", "blocked"))
    payload = {
        "schema_version": "business_file_judgement.cli.v1",
        "status": status,
        "interpreter_mode": "agent",
        "run_id": str(result.get("run_id", "")),
        "source_binding": source_binding,
        "target_binding": None,
        "artifacts": sorted((result.get("artifacts") or {}).keys()),
        "archive_execution": {"status": "", "confirmed": False},
    }
    if status == "blocked":
        payload["error_code"] = str(result.get("blocked_reason", "LLM.CAPABILITY_DISABLED"))
    return payload


def _capability_block(*, interpreter_mode: str) -> dict[str, Any]:
    return {
        "schema_version": "business_file_judgement.cli.v1",
        "status": "blocked",
        "interpreter_mode": interpreter_mode,
        "error_code": "LLM.CAPABILITY_DISABLED",
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None, *, interpreter_factory: Callable[[], Any] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.agent_response and (
            args.interpreter_mode != "agent" or not args.resume_run
        ):
            raise InputBindingError("AGENT_JUDGEMENT.ARGUMENT_INVALID")
        if args.resume_run and args.interpreter_mode != "agent":
            raise InputBindingError("AGENT_JUDGEMENT.ARGUMENT_INVALID")
        if (
            args.interpreter_mode == "agent"
            and args.resume_run
            and not args.agent_response
        ):
            _emit(_capability_block(interpreter_mode="agent"))
            return 2
        if args.interpreter_mode == "disabled":
            _emit(_capability_block(interpreter_mode="disabled"))
            return 2
        settings = load_app_settings(config_file=args.config)
        adapters = build_runtime_adapters(settings)
        registry = adapters.storage_binding_registry
        if registry is None:
            raise InputBindingError("SOURCE_BINDING.INVALID")
        _source_binding(registry, args.source_binding)
        _target_binding(registry, args.target_binding)
        files = _validate_sources(registry, args.source_binding, args.files)
        context = JsonBusinessContextProvider(args.context, storage_bindings=registry)
        retrieval = RetrievalService(context, adapters.structure_index)
        tool_kwargs = {
            "workspace_dir": str(settings.runtime_workspace),
            "ocr_adapter": DisabledOcrProvider().extract,
            "storage_binding_registry": registry,
            "document_store_router": adapters.document_store_router,
            "retrieval_service": retrieval,
            "archive_target_resolver": adapters.archive_target_resolver,
        }
        if args.interpreter_mode == "agent":
            tools = DataCleaningTools(
                **tool_kwargs,
                agent_judgement_gateway=AgentJudgementGateway(),
            )
            if args.resume_run:
                source_binding = args.source_binding
                result = tools.resume_agent_judgement_run(
                    args.resume_run, args.agent_response, files,
                )
            else:
                result = tools.prepare_agent_judgement_run(files)
                source_binding = None
                if result.get("status") == "awaiting_agent_judgement":
                    source_binding = _agent_manifest_source_binding(
                        _agent_manifest_entries(
                            result["artifacts"]["run_dir"], result["run_id"],
                        )
                    )
            _emit(
                _agent_summary(
                    result=result,
                    source_binding=source_binding,
                )
            )
            return 0 if result.get("status") in {"awaiting_agent_judgement", "success", "needs_review"} else 2
        interpreter = (interpreter_factory or OpenAICompatibleInterpreter)()
        interpretation = DocumentInterpretationService(retrieval, interpreter)
        tools = DataCleaningTools(**tool_kwargs, interpretation_service=interpretation)
        prepared = tools.prepare_file_organization_run(files)
        verification = tools.verify_file_organization_run(prepared["run_id"])
        audit = tools.audit_file_organization_run(prepared["run_id"])
        feedback = tools.prepare_feedback_form(prepared["run_id"])
        archive = tools.execute_archive_plan(prepared["run_id"], confirmed=False)
        _emit(_summary(prepared=prepared, verification=verification, audit=audit, feedback=feedback, archive=archive, source_binding=args.source_binding, target_binding=args.target_binding))
        return 2 if prepared.get("failed", 0) else 0
    except ProviderConfigurationError:
        _emit(_capability_block(interpreter_mode="configured_llm"))
        return 2
    except InputBindingError as exc:
        _emit({"schema_version": "business_file_judgement.cli.v1", "status": "blocked", "error_code": str(exc)})
        return 2
    except Exception:
        _emit({"schema_version": "business_file_judgement.cli.v1", "status": "failed", "error_code": "BUSINESS_FILE_RUN.UNEXPECTED"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
