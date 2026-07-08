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
    _require_equal(manifest, "schema_version", "agentplatform.loop_package.v1", errors)
    _require_equal(manifest, "name", "data_cleaning_file_organization", errors)

    for key in [
        "skill_interface",
        "loop_policy",
        "workset_schema",
        "trace_contract",
        "runtime_tools",
        "validators",
        "fixtures",
        "hard_rules",
        "soft_guidance_sources",
        "derived_sources",
    ]:
        if key not in manifest:
            errors.append(f"manifest missing {key}")

    if manifest.get("skill_interface", {}).get("skill_name") != "data_cleaning_file_organization":
        errors.append("skill_interface.skill_name mismatch")
    if manifest.get("loop_policy", {}).get("policy_module") != "skill_policies.data_cleaning_file_organization":
        errors.append("loop_policy.policy_module mismatch")

    for relative_key in ["workset_schema", "trace_contract"]:
        relative_path = manifest.get(relative_key)
        if relative_path and not (package_dir / relative_path).exists():
            errors.append(f"{relative_key} path missing: {relative_path}")

    for fixture in manifest.get("fixtures", []):
        if not (package_dir / fixture).exists():
            errors.append(f"fixture path missing: {fixture}")

    required_validators = {
        "validate_loop_package",
        "validate_workset",
        "validate_trace_contract",
        "validate_run_artifacts",
    }
    if not required_validators.issubset(set(manifest.get("validators", []))):
        errors.append("manifest validators incomplete")

    runtime_tools = manifest.get("runtime_tools", [])
    if not isinstance(runtime_tools, list) or not runtime_tools:
        errors.append("manifest runtime_tools must be a non-empty list")

    required_hard_rules = {"blocked_is_not_success", "needs_confirmation_stops_loop"}
    if not required_hard_rules.issubset(set(manifest.get("hard_rules", []))):
        errors.append("manifest hard_rules incomplete")

    if "ToolRegistry" not in manifest.get("derived_sources", []):
        errors.append("ToolRegistry must be a derived source")

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

    source_files = inputs.get("source_files", [])
    if not isinstance(source_files, list) or not source_files:
        errors.append("workset inputs.source_files must be a non-empty list")
    else:
        for index, item in enumerate(source_files):
            for key in schema.get("properties", {}).get("inputs", {}).get("source_files_item_required", []):
                if key not in item:
                    errors.append(f"source_files[{index}] missing {key}")

    permission_boundary = workset.get("permission_boundary", {})
    for key in schema.get("properties", {}).get("permission_boundary", {}).get("required", []):
        if key not in permission_boundary:
            errors.append(f"permission_boundary missing {key}")

    done_when = set(workset.get("done_when", []))
    for item in schema.get("properties", {}).get("done_when", {}).get("required_items", []):
        if item not in done_when:
            errors.append(f"done_when missing {item}")

    return {"status": "failed" if errors else "pass", "errors": errors}


def validate_run_artifacts(run_result: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    if run_result.get("schema_version") != "file_organization.run.v1":
        errors.append("run_result schema_version mismatch")
    if run_result.get("status") not in {"success", "partial", "failed"}:
        errors.append("run_result status not allowed")

    artifacts = run_result.get("artifacts", {})
    required_artifacts = {
        "input_manifest": "file_organization.input_manifest.v1",
        "review_queue": {"review_queue.v1", "review_queue.v2"},
        "planned_archive_actions": "archive_plan.v1",
        "trace": "file_organization.trace.v1",
    }
    for key, expected_schema in required_artifacts.items():
        path_value = artifacts.get(key)
        if not path_value:
            errors.append(f"artifacts missing {key}")
            continue
        path = Path(path_value)
        if not path.exists():
            errors.append(f"artifact path missing: {key}")
            continue
        payload = load_json(path)
        expected_versions = expected_schema if isinstance(expected_schema, set) else {expected_schema}
        if payload.get("schema_version") not in expected_versions:
            errors.append(f"{key} schema_version mismatch")
        if run_result.get("run_id") and payload.get("run_id") != run_result["run_id"]:
            errors.append(f"{key} run_id mismatch")

    run_report = artifacts.get("run_report")
    if not run_report:
        errors.append("artifacts missing run_report")
    elif not Path(run_report).exists():
        errors.append("artifact path missing: run_report")

    run_dir = artifacts.get("run_dir")
    if not run_dir:
        errors.append("artifacts missing run_dir")
    elif not Path(run_dir).is_dir():
        errors.append("artifact run_dir missing")

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


def _require_equal(payload: Dict[str, Any], key: str, expected: Any, errors: List[str]) -> None:
    if payload.get(key) != expected:
        errors.append(f"{key} expected {expected!r}, got {payload.get(key)!r}")
