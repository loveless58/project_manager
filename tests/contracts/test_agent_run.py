from __future__ import annotations

import pytest

from contracts.agent_run import build_agent_run


def test_agent_run_contains_only_explicit_inputs_and_initial_state() -> None:
    run = build_agent_run(
        run_id="run-1",
        goal="解析指定文档，不归档",
        input_refs=[{"logical_uri": "business://source/invoice.pdf"}],
        status="prepared",
        artifacts={},
    )

    assert run == {
        "schema_version": "agent_run.v1",
        "run_id": "run-1",
        "goal": "解析指定文档，不归档",
        "input_refs": [{"logical_uri": "business://source/invoice.pdf"}],
        "status": "prepared",
        "loaded_skills": [],
        "items": [],
        "artifacts": {},
    }


def test_agent_run_rejects_unknown_status() -> None:
    with pytest.raises(ValueError, match="status"):
        build_agent_run(
            run_id="run-1",
            goal="解析",
            input_refs=[],
            status="archived",
            artifacts={},
        )
