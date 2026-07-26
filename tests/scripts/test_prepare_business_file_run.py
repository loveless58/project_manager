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
