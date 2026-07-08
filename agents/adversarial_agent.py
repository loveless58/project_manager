from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from business_rules.adversarial_verification import run_adversarial_verification


class AdversarialAgent:
    """Read-only reviewer that challenges extracted facts and archive plans."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = workspace_dir

    def review(
        self,
        *,
        run_id: str,
        extracted_items: List[Dict[str, Any]],
        ledger_results: Optional[List[Dict[str, Any]]] = None,
        archive_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        result = run_adversarial_verification(
            workspace_dir=self.workspace_dir,
            run_id=run_id,
            extracted_items=extracted_items,
            ledger_results=ledger_results or [],
            archive_actions=archive_actions or [],
        )
        result["agent_role"] = "adversarial_agent"
        result["needs_human_review"] = result.get("overall_verdict") in {
            "needs_human_review",
            "needs_correction",
        }
        result["archive_allowed"] = result.get("overall_verdict") in {
            "pass",
            "pass_with_warnings",
        }
        result["next_actions"] = self._next_actions(result.get("overall_verdict", ""))
        self._persist_augmented_result(result)
        return result

    @staticmethod
    def _next_actions(verdict: str) -> List[str]:
        if verdict == "pass":
            return ["audit_file_organization_run"]
        if verdict == "pass_with_warnings":
            return ["audit_file_organization_run", "review_warnings_before_archive"]
        if verdict == "needs_correction":
            return ["apply_human_review", "rerun_verification_before_archive"]
        if verdict == "needs_human_review":
            return ["apply_human_review", "rerun_verification_before_archive"]
        return ["inspect_adversarial_verification"]

    @staticmethod
    def _persist_augmented_result(result: Dict[str, Any]) -> None:
        artifact_path = result.get("artifact_path")
        if not artifact_path:
            return
        with open(str(artifact_path), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
