"""Shared validators for loop package contracts."""

import json
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_loop_package(package_dir: Path) -> Dict[str, Any]:
    package_dir = Path(package_dir)
    errors: List[str] = []
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.exists():
        return {"status": "failed", "errors": [f"missing {manifest_path}"]}

    manifest = load_json(manifest_path)
    package_name = package_dir.name
    if manifest.get("schema_version") != "agentplatform.loop_package.v1":
        errors.append("manifest schema_version mismatch")
    if manifest.get("name") != package_name:
        errors.append("manifest name mismatch")
    if manifest.get("skill_interface", {}).get("skill_name") != package_name:
        errors.append("skill_interface.skill_name mismatch")

    for key in [
        "route_keywords",
        "skill_interface",
        "loop_policy",
        "workset_schema",
        "trace_contract",
        "runtime_tools",
        "runtime_registrar",
        "validators",
        "fixtures",
        "hard_rules",
        "soft_guidance_sources",
        "derived_sources",
    ]:
        if key not in manifest:
            errors.append(f"manifest missing {key}")

    for relative_key in ["workset_schema", "trace_contract"]:
        relative_path = manifest.get(relative_key)
        if relative_path and not (package_dir / relative_path).exists():
            errors.append(f"{relative_key} path missing: {relative_path}")

    for fixture in manifest.get("fixtures", []):
        if not (package_dir / fixture).exists():
            errors.append(f"fixture path missing: {fixture}")

    for list_key in ["route_keywords", "runtime_tools", "validators", "fixtures", "hard_rules"]:
        value = manifest.get(list_key, [])
        if not isinstance(value, list) or not value:
            errors.append(f"manifest {list_key} must be a non-empty list")

    if not manifest.get("runtime_registrar"):
        errors.append("manifest runtime_registrar must be non-empty")

    return {
        "status": "failed" if errors else "pass",
        "errors": errors,
        "manifest": manifest,
    }


def validate_workset(workset: Dict[str, Any], schema: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    for key in schema.get("required_top_level", []):
        if key not in workset:
            errors.append(f"workset missing {key}")

    expected_schema_version = schema.get("properties", {}).get("schema_version", {}).get("const")
    if expected_schema_version and workset.get("schema_version") != expected_schema_version:
        errors.append("workset schema_version mismatch")

    allowed_task_types = schema.get("properties", {}).get("task_type", {}).get("enum", [])
    if allowed_task_types and workset.get("task_type") not in allowed_task_types:
        errors.append("workset task_type not allowed")

    inputs = workset.get("inputs", {})
    for key in schema.get("properties", {}).get("inputs", {}).get("required", []):
        if key not in inputs:
            errors.append(f"workset inputs missing {key}")

    permission_boundary = workset.get("permission_boundary", {})
    for key in schema.get("properties", {}).get("permission_boundary", {}).get("required", []):
        if key not in permission_boundary:
            errors.append(f"permission_boundary missing {key}")

    done_when = set(workset.get("done_when", []))
    for item in schema.get("properties", {}).get("done_when", {}).get("required_items", []):
        if item not in done_when:
            errors.append(f"done_when missing {item}")

    return {"status": "failed" if errors else "pass", "errors": errors}


def validate_trace_contract(trace: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    for key in contract.get("required_top_level", []):
        if key not in trace:
            errors.append(f"trace missing {key}")

    status = trace.get("status")
    if status and status not in contract.get("terminal_statuses", []):
        errors.append(f"trace status not allowed: {status}")

    metadata = trace.get("metadata", {})
    for key in contract.get("metadata_required", []):
        if key not in metadata:
            errors.append(f"metadata missing {key}")

    expected_skill = contract.get("active_skill")
    if expected_skill and metadata.get("active_skill") != expected_skill:
        errors.append("metadata.active_skill mismatch")

    rounds = trace.get("rounds", [])
    if not isinstance(rounds, list) or not rounds:
        errors.append("trace rounds must be a non-empty list")
    else:
        for index, round_item in enumerate(rounds):
            for key in contract.get("round_required", []):
                if key not in round_item:
                    errors.append(f"rounds[{index}] missing {key}")
            if round_item.get("action") == "final_answer":
                continue
            evaluation = round_item.get("observation_evaluation", {})
            if contract.get("tool_round_observation_evaluation_required") and not evaluation:
                errors.append(f"rounds[{index}] missing observation_evaluation")
            for key in contract.get("observation_evaluation_required", []):
                if key not in evaluation:
                    errors.append(f"rounds[{index}].observation_evaluation missing {key}")

    return {"status": "failed" if errors else "pass", "errors": errors}
