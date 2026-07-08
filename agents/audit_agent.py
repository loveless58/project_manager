from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


class AuditAgent:
    """Read-only auditor for run artifacts, policy evidence, and confirmation state."""

    REQUIRED_ARTIFACTS = {
        "input_manifest": "input_manifest.json",
        "review_queue": "review_queue.json",
        "planned_archive_actions": "planned_archive_actions.json",
        "trace": "trace.json",
        "adversarial_verification": "adversarial_verification.json",
    }

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def review_run(self, run_id: str, loop_trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        run_dir = os.path.join(self.workspace_dir, "runs", run_id)
        if not os.path.isdir(run_dir):
            return {
                "schema_version": "audit_review.v1",
                "agent_role": "audit_agent",
                "status": "failed",
                "run_id": run_id,
                "audit_verdict": "blocked",
                "error": f"Run directory not found: {run_dir}",
                "missing_artifacts": ["run_dir"],
                "policy_violations": [],
                "human_confirmation_required": False,
                "next_actions": ["prepare_file_organization_run"],
            }

        artifacts = {
            key: os.path.join(run_dir, filename)
            for key, filename in self.REQUIRED_ARTIFACTS.items()
        }
        missing = [key for key, path in artifacts.items() if not os.path.exists(path)]
        verification = self._load_json(artifacts["adversarial_verification"]) if "adversarial_verification" not in missing else {}
        archive_plan = self._load_json(artifacts["planned_archive_actions"]) if "planned_archive_actions" not in missing else {}
        review_queue = self._load_json(artifacts["review_queue"]) if "review_queue" not in missing else {}

        required_feedback_items = self._required_feedback_items(review_queue)
        policy_violations = self._policy_violations(verification, archive_plan)
        audit_verdict = self._audit_verdict(missing, policy_violations, verification, required_feedback_items)
        human_confirmation_required = self._human_confirmation_required(verification, archive_plan, review_queue)
        result = {
            "schema_version": "audit_review.v1",
            "agent_role": "audit_agent",
            "status": "success",
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "audit_verdict": audit_verdict,
            "missing_artifacts": missing,
            "policy_violations": policy_violations,
            "required_feedback_items": required_feedback_items,
            "human_feedback_required": bool(required_feedback_items),
            "human_confirmation_required": human_confirmation_required,
            "next_actions": self._next_actions(
                audit_verdict,
                missing,
                verification,
                human_confirmation_required,
                required_feedback_items,
            ),
            "artifact_paths": artifacts,
            "loop_trace_summary": self._loop_trace_summary(loop_trace or {}),
        }
        result["artifact_path"] = self._write_artifact(run_dir, result)
        return result

    def _policy_violations(self, verification: Dict[str, Any], archive_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        violations: List[Dict[str, Any]] = []
        if verification:
            verdict = verification.get("overall_verdict")
            if verdict in {"needs_human_review", "needs_correction"}:
                violations.append({
                    "id": "AUD001",
                    "severity": "high",
                    "rule": "adversarial_findings_require_review",
                    "message": f"Adversarial verifier returned {verdict}.",
                })
        archive_actions = archive_plan.get("actions") if isinstance(archive_plan, dict) else []
        if archive_actions and not isinstance(archive_actions, list):
            violations.append({
                "id": "AUD002",
                "severity": "medium",
                "rule": "archive_plan_actions_must_be_list",
                "message": "planned_archive_actions.actions is not a list.",
            })
        return violations

    @staticmethod
    def _audit_verdict(
        missing: List[str],
        policy_violations: List[Dict[str, Any]],
        verification: Dict[str, Any],
        required_feedback_items: List[str],
    ) -> str:
        if missing:
            return "blocked"
        if any(item.get("severity") == "high" for item in policy_violations):
            return "needs_human_review"
        if required_feedback_items:
            return "needs_human_feedback"
        if verification.get("overall_verdict") == "pass_with_warnings" or policy_violations:
            return "acceptable_with_warnings"
        return "acceptable"

    @staticmethod
    def _human_confirmation_required(
        verification: Dict[str, Any],
        archive_plan: Dict[str, Any],
        review_queue: Dict[str, Any],
    ) -> bool:
        if verification.get("needs_human_review"):
            return True
        if verification.get("overall_verdict") in {"needs_human_review", "needs_correction", "pass_with_warnings"}:
            return True
        if review_queue.get("items"):
            return True
        return bool(archive_plan.get("actions"))

    @staticmethod
    def _next_actions(
        audit_verdict: str,
        missing: List[str],
        verification: Dict[str, Any],
        human_confirmation_required: bool,
        required_feedback_items: List[str],
    ) -> List[str]:
        if "adversarial_verification" in missing:
            return ["verify_file_organization_run"]
        if audit_verdict == "blocked":
            return ["inspect_missing_artifacts"]
        if audit_verdict == "needs_human_review":
            return ["apply_human_review", "rerun_verification_before_archive"]
        if required_feedback_items:
            return ["apply_feedback_decisions"]
        if verification.get("overall_verdict") == "pass_with_warnings":
            return ["review_warnings_before_archive"]
        if human_confirmation_required:
            return ["execute_archive_plan_with_confirmation"]
        return ["no_action_required"]

    @staticmethod
    def _required_feedback_items(review_queue: Dict[str, Any]) -> List[str]:
        items = review_queue.get("items") if isinstance(review_queue, dict) else []
        if not isinstance(items, list):
            return []
        required = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                continue
            risk = str(item.get("risk") or "")
            recommended = str(item.get("recommended_decision") or "")
            if risk in {"P0", "P1"} or recommended == "defer":
                required.append(str(item.get("id") or f"R{index:03d}"))
        return required

    @staticmethod
    def _loop_trace_summary(loop_trace: Dict[str, Any]) -> Dict[str, Any]:
        rounds = loop_trace.get("rounds") if isinstance(loop_trace, dict) else []
        return {
            "trace_id": loop_trace.get("trace_id", "") if isinstance(loop_trace, dict) else "",
            "status": loop_trace.get("status", "") if isinstance(loop_trace, dict) else "",
            "round_count": len(rounds) if isinstance(rounds, list) else 0,
        }

    @staticmethod
    def _load_json(path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_artifact(run_dir: str, result: Dict[str, Any]) -> str:
        artifact_path = os.path.join(run_dir, "audit_review.json")
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return artifact_path
