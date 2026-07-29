"""Versioned run-level contract for the Document Management Agent."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping


SCHEMA_VERSION = "agent_run.v1"
_STATUSES = {"prepared", "running", "awaiting_review", "completed", "failed"}


def build_agent_run(
    *,
    run_id: str,
    goal: str,
    input_refs: list[Mapping[str, Any]],
    status: str,
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the initial, source-independent record for one explicit run."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run id is invalid")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("goal is invalid")
    if status not in _STATUSES:
        raise ValueError("run status is invalid")
    if not isinstance(input_refs, list) or not all(isinstance(ref, Mapping) for ref in input_refs):
        raise ValueError("input references are invalid")
    if not isinstance(artifacts, Mapping):
        raise ValueError("run artifacts are invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "goal": goal,
        "input_refs": [deepcopy(dict(ref)) for ref in input_refs],
        "status": status,
        "loaded_skills": [],
        "items": [],
        "artifacts": deepcopy(dict(artifacts)),
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "agent": {"name": "DocumentManagementAgent", "version": "1.0"},
        "safety": {"source_files": "read_only", "archive": "not_requested"},
    }


__all__ = ["SCHEMA_VERSION", "build_agent_run"]
