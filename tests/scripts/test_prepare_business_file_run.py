from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeInterpreter:
    name = "fake"
    model = "fake-model"
    schema_version = "candidate_document_interpretation.v1"
    prompt_version = "document_interpretation.v1"
    policy_version = "document_interpretation_policy.v1"

    def complete_json(self, _request):
        return {"schema_version": self.schema_version, "status": "success", "document_type": "contract", "fields": {"contract_code": "CT-001"}, "relations": [{"relation_type": "contract_project", "target_candidate_id": "C-001"}], "evidence": [{"kind": "business_context", "candidate_id": "C-001", "field": "contract_code"}], "confidence": 0.9, "interpreter": self.name, "model": self.model, "prompt_version": self.prompt_version, "policy_version": self.policy_version}


def _module():
    spec = importlib.util.spec_from_file_location("prepare_business_file_run_test", PROJECT_ROOT / "scripts" / "prepare_business_file_run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _inputs(tmp_path: Path):
    source_root, target_root, runtime, projections = (tmp_path / name for name in ("source", "archive", "runtime", "projections"))
    for directory in (source_root, target_root, runtime, projections):
        directory.mkdir()
    source = source_root / "contract.md"
    original = "??\n?????CT-001\n?????????"
    source.write_text(original, encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"schema_version": "business_context_catalog.v1", "records": [{"id": "C-001", "document_type": "contract", "parties": {"buyer": {"name": "?????", "tax_id": "913100000000000001"}, "seller": {"name": "?????", "tax_id": "913100000000000002"}}, "facts": {"contract_code": "CT-001"}, "documents": [{"path": str(source), "document_version_id": "source-v1", "content_hash": digest, "media_type": "text/markdown", "page_count": 0, "requires_structure_index": False}], "path_hints": []}]}, ensure_ascii=False), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"deployment_mode": "local", "runtime_workspace": str(runtime), "storage_bindings": [{"binding_id": "source", "provider": "local", "node_id": "test-node", "logical_root": "business://source/", "physical_root": str(source_root), "roles": ["source"], "readable": True, "writable": False}, {"binding_id": "archive", "provider": "local", "node_id": "test-node", "logical_root": "business://archive/", "physical_root": str(target_root), "roles": ["archive_target"], "readable": False, "writable": True}], "providers": {"document_store": "local", "structure_index": "disabled", "projection_writer": "filesystem", "projection_root": str(projections)}}), encoding="utf-8")
    return config, catalog, source, runtime, original


def _agent_inputs(tmp_path: Path):
    config, catalog, source, runtime, original = _inputs(tmp_path)
    source.write_text("\u9879\u76ee\u7f16\u53f7\uff1aPRJ-001\n\u9879\u76ee\u540d\u79f0\uff1a\u5408\u6210\u9879\u76ee", encoding="utf-8")
    catalog_payload = json.loads(catalog.read_text(encoding="utf-8"))
    catalog_payload["records"][0]["facts"] = {"project_code": "PRJ-001"}
    catalog_payload["records"][0]["documents"][0]["content_hash"] = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog.write_text(json.dumps(catalog_payload), encoding="utf-8")
    return config, catalog, source, runtime, original


def _matching_agent_response(request: dict) -> dict:
    interpretation_request = request["interpretation_request"]
    return {
        "schema_version": "agent_judgement_response.v1",
        "run_id": request["run_id"], "request_id": request["request_id"], "request_hash": request["request_hash"],
        "interpreter": "approved_agent", "model": "approved_local_model", "created_at": "2026-07-26T00:00:00Z",
        "interpretation": {
            "schema_version": "candidate_document_interpretation.v1", "status": "success",
            "document_type": interpretation_request["document"]["document_type_hint"], "fields": {"project_code": "PRJ-001"},
            "relations": [], "evidence": [interpretation_request["business_context"]["evidence"][0]], "confidence": 0.94,
            "interpreter": "approved_agent", "model": "approved_local_model",
            "prompt_version": "document_interpretation.v1", "policy_version": "document_interpretation_policy.v1",
        },
    }


def test_cli_creates_only_review_artifacts_and_redacts_stdout(tmp_path, capsys):
    module = _module()
    config, catalog, source, runtime, original = _inputs(tmp_path)
    from tools.data_cleaning_tools import DataCleaningTools
    original_execute, archive_calls = DataCleaningTools.execute_archive_plan, []
    def record_execute(self, run_id, confirmed=False):
        archive_calls.append((run_id, confirmed))
        return original_execute(self, run_id, confirmed=confirmed)
    with patch.object(DataCleaningTools, "execute_archive_plan", record_execute):
        exit_code = module.main(["--config", str(config), "--context", str(catalog), "--source-binding", "source", "--target-binding", "archive", str(source)], interpreter_factory=FakeInterpreter)
    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    assert exit_code == 0
    assert summary["status"] in {"success", "needs_review"}
    assert summary["archive_execution"]["confirmed"] is False
    assert len(archive_calls) == 1 and archive_calls[0][1] is False
    assert str(source) not in stdout and str(config) not in stdout
    assert not list(runtime.rglob("archive_result.json"))
    assert source.read_text(encoding="utf-8") == original


def test_cli_blocks_scanned_pdf_without_archive_result(tmp_path, capsys):
    import fitz
    module = _module()
    config, catalog, source, runtime, _ = _inputs(tmp_path)
    scan = source.parent / "scan.pdf"
    document = fitz.open()
    document.new_page().draw_rect((72, 72, 180, 180), fill=(0.5, 0.5, 0.5))
    document.save(scan)
    document.close()
    exit_code = module.main(["--config", str(config), "--context", str(catalog), "--source-binding", "source", str(scan)], interpreter_factory=FakeInterpreter)
    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    assert exit_code == 2
    assert summary["status"] == "blocked"


def test_agent_mode_outputs_redacted_handoff_without_model_env(tmp_path, capsys):
    """Changing agent mode to construct the configured interpreter is a bug."""
    module = _module()
    config, catalog, source, runtime, _ = _agent_inputs(tmp_path)
    interpreter_calls = []

    def forbidden_interpreter():
        interpreter_calls.append(True)
        raise AssertionError("agent mode must not construct a configured interpreter")

    exit_code = module.main(
        [
            "--config", str(config), "--context", str(catalog),
            "--source-binding", "source", "--interpreter-mode", "agent", str(source),
        ],
        interpreter_factory=forbidden_interpreter,
    )

    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    assert exit_code == 0
    assert summary["status"] == "awaiting_agent_judgement"
    assert summary["interpreter_mode"] == "agent"
    assert summary["run_id"].startswith("run_")
    assert (runtime / "runs" / summary["run_id"] / "agent_judgement_requests.json").is_file()
    assert interpreter_calls == []
    assert "physical_root" not in stdout
    assert str(source) not in stdout and str(config) not in stdout


def test_agent_resume_rejects_missing_response_without_configured_llm(tmp_path, capsys):
    """Dropping the resume-response guard must remain a capability block."""
    module = _module()
    config, catalog, source, _, _ = _inputs(tmp_path)
    interpreter_calls = []

    def forbidden_interpreter():
        interpreter_calls.append(True)
        raise AssertionError("agent mode must not construct a configured interpreter")

    exit_code = module.main(
        [
            "--config", str(config), "--context", str(catalog),
            "--source-binding", "source", "--interpreter-mode", "agent",
            "--resume-run", "run_0123456789abcdef0123456789abcdef", str(source),
        ],
        interpreter_factory=forbidden_interpreter,
    )

    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert summary["error_code"] == "LLM.CAPABILITY_DISABLED"
    assert interpreter_calls == []


def test_configured_mode_constructs_the_injected_interpreter_once(tmp_path, capsys):
    """Bypassing the configured interpreter would change legacy CLI behavior."""
    module = _module()
    config, catalog, source, _, _ = _inputs(tmp_path)
    interpreter_calls = []

    def configured_interpreter():
        interpreter_calls.append(True)
        return FakeInterpreter()

    exit_code = module.main(
        ["--config", str(config), "--context", str(catalog), "--source-binding", "source", str(source)],
        interpreter_factory=configured_interpreter,
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["status"] in {"success", "needs_review"}
    assert interpreter_calls == [True]


def test_disabled_mode_blocks_without_model_invocation(tmp_path, capsys):
    """Replacing the disabled branch with an implicit model fallback is a bug."""
    module = _module()
    config, catalog, source, _, _ = _inputs(tmp_path)
    interpreter_calls = []

    def forbidden_interpreter():
        interpreter_calls.append(True)
        raise AssertionError("disabled mode must not construct a configured interpreter")

    exit_code = module.main(
        [
            "--config", str(config), "--context", str(catalog), "--source-binding", "source",
            "--interpreter-mode", "disabled", str(source),
        ],
        interpreter_factory=forbidden_interpreter,
    )

    summary = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert summary["status"] == "blocked"
    assert summary["error_code"] == "LLM.CAPABILITY_DISABLED"
    assert interpreter_calls == []


def test_agent_resume_creates_review_artifacts_without_executing_archive(tmp_path, capsys):
    """Changing resume to invoke archive execution (or a model) is a safety bug."""
    module = _module()
    config, catalog, source, runtime, _ = _agent_inputs(tmp_path)
    interpreter_calls = []

    def forbidden_interpreter():
        interpreter_calls.append(True)
        raise AssertionError("agent mode must not construct a configured interpreter")

    prepare_code = module.main(
        [
            "--config", str(config), "--context", str(catalog),
            "--source-binding", "source", "--interpreter-mode", "agent", str(source),
        ],
        interpreter_factory=forbidden_interpreter,
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepare_code == 0
    run_dir = runtime / "runs" / prepared["run_id"]
    requests = json.loads((run_dir / "agent_judgement_requests.json").read_text(encoding="utf-8"))
    response_path = tmp_path / "agent-response.json"
    response_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_judgement_responses.v1",
                "run_id": prepared["run_id"],
                "responses": [_matching_agent_response(item) for item in requests["requests"]],
            }
        ),
        encoding="utf-8",
    )

    from tools.data_cleaning_tools import DataCleaningTools

    with patch.object(
        DataCleaningTools,
        "execute_archive_plan",
        side_effect=AssertionError("agent resume must not execute an archive plan"),
    ):
        resume_code = module.main(
            [
                "--config", str(config), "--context", str(catalog),
                "--source-binding", "source", "--interpreter-mode", "agent",
                "--resume-run", prepared["run_id"], "--agent-response", str(response_path), str(source),
            ],
            interpreter_factory=forbidden_interpreter,
        )

    summary = json.loads(capsys.readouterr().out)
    assert resume_code == 0
    assert summary["status"] in {"success", "needs_review"}
    assert summary["archive_execution"]["confirmed"] is False
    assert (run_dir / "review_queue.json").is_file()
    assert (run_dir / "agent_judgement_consumption.json").is_file()
    assert not (run_dir / "archive_result.json").exists()
    assert interpreter_calls == []
    assert str(response_path) not in json.dumps(summary)
