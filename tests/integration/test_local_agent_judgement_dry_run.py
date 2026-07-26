from __future__ import annotations

import json
from pathlib import Path


class StrictSyntheticAgentHost:
    """Test host that can respond only by reading the persisted request file."""

    def __init__(self) -> None:
        self.request_hashes: list[str] = []

    def write_responses(self, request_path: Path, response_path: Path) -> None:
        requests = json.loads(request_path.read_text(encoding="utf-8"))
        assert requests["schema_version"] == "agent_judgement_requests.v1"
        assert requests["requests"]
        self.request_hashes = [item["request_hash"] for item in requests["requests"]]
        responses = [self._response(item) for item in requests["requests"]]
        response_path.write_text(
            json.dumps(
                {
                    "schema_version": "agent_judgement_responses.v1",
                    "run_id": requests["run_id"],
                    "responses": responses,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _response(request: dict) -> dict:
        interpretation_request = request["interpretation_request"]
        document_type = interpretation_request["document"]["document_type_hint"]
        evidence = interpretation_request["business_context"]["evidence"][:1]
        return {
            "schema_version": "agent_judgement_response.v1",
            "run_id": request["run_id"],
            "request_id": request["request_id"],
            "request_hash": request["request_hash"],
            "interpreter": "strict_synthetic_agent",
            "model": "deterministic_fixture_v1",
            "created_at": "2026-07-26T00:00:00Z",
            "interpretation": {
                "schema_version": "candidate_document_interpretation.v1",
                "status": "success",
                "document_type": document_type,
                "fields": {},
                "relations": [
                    {
                        "relation_type": (
                            "invoice_contract"
                            if document_type == "invoice"
                            else "contract_project"
                        ),
                        "target_candidate_id": "C-001",
                    }
                ],
                "evidence": evidence,
                "confidence": 0.94,
                "interpreter": "strict_synthetic_agent",
                "model": "deterministic_fixture_v1",
                "prompt_version": "document_interpretation.v1",
                "policy_version": "document_interpretation_policy.v1",
            },
        }


def test_local_agent_dry_run_round_trip_is_review_only(tmp_path, monkeypatch) -> None:
    """Dropping the persisted host hand-off or a review-only gate is a bug."""
    from integrations.llm import OpenAICompatibleInterpreter
    from tools.data_cleaning_tools import DataCleaningTools

    def forbidden(*_args, **_kwargs):
        raise AssertionError("configured LLM and real OCR providers are forbidden")

    monkeypatch.setattr(OpenAICompatibleInterpreter, "__init__", forbidden)
    for method in (
        "_default_ocr_adapter",
        "_ocr_with_easyocr",
        "_ocr_with_rapidocr",
        "_ocr_with_tesseract",
        "_ocr_with_vision_macos",
    ):
        monkeypatch.setattr(DataCleaningTools, method, staticmethod(forbidden))

    original_execute = DataCleaningTools.execute_archive_plan
    archive_confirmations = []

    def record_archive_gate(self, run_id, confirmed=False):
        archive_confirmations.append(confirmed)
        return original_execute(self, run_id, confirmed=confirmed)

    monkeypatch.setattr(DataCleaningTools, "execute_archive_plan", record_archive_gate)

    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    host = StrictSyntheticAgentHost()

    result = run_local_agent_dry_run(root, host=host)

    assert result["native_statuses"] == {
        "invoice": "needs_review",
        "contract": "needs_review",
        "markdown": "needs_review",
        "xlsx": "needs_review",
        "xml": "needs_review",
    }
    assert result["scan_failure"] == "OCR.CAPABILITY_DISABLED"
    assert result["invoice_candidate"] == "C-001"
    assert result["hashes_before"] == result["hashes_after"]
    assert len(host.request_hashes) == 5
    assert result["review_items_valid"]
    assert all(result["review_items_valid"])
    assert archive_confirmations == [False]
    assert result["archive_execution"]["confirmed"] is False
    assert not list((root / "runtime").rglob("archive_result.json"))

    config = json.loads(
        (root / "config" / "project-manager.local.json").read_text(encoding="utf-8")
    )
    assert [item["binding_id"] for item in config["storage_bindings"]] == [
        "dry-run-source"
    ]
    sqlite_path = Path(config["database"]["sqlite_path"]).resolve()
    assert sqlite_path.is_file()
    assert sqlite_path.is_relative_to((root / "runtime").resolve())
    assert not sqlite_path.is_relative_to((root / "source").resolve())

    run_dir = root / "runtime" / "runs" / result["run_id"]
    plan = json.loads(
        (run_dir / "planned_archive_actions.json").read_text(encoding="utf-8")
    )
    assert plan["actions"]
    assert all(action["confirmed"] is False for action in plan["actions"])


class MissingResponseHost:
    def write_responses(self, request_path: Path, _response_path: Path) -> None:
        assert json.loads(request_path.read_text(encoding="utf-8"))["requests"]


class MutatingResponseHost(StrictSyntheticAgentHost):
    def __init__(self, mutation) -> None:
        super().__init__()
        self.mutation = mutation

    def write_responses(self, request_path: Path, response_path: Path) -> None:
        super().write_responses(request_path, response_path)
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        self.mutation(payload)
        response_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )


def _forbid_external_providers(monkeypatch) -> None:
    from integrations.llm import OpenAICompatibleInterpreter
    from tools.data_cleaning_tools import DataCleaningTools

    def forbidden(*_args, **_kwargs):
        raise AssertionError("configured LLM and real OCR providers are forbidden")

    monkeypatch.setattr(OpenAICompatibleInterpreter, "__init__", forbidden)
    for method in (
        "_default_ocr_adapter",
        "_ocr_with_easyocr",
        "_ocr_with_rapidocr",
        "_ocr_with_tesseract",
        "_ocr_with_vision_macos",
    ):
        monkeypatch.setattr(DataCleaningTools, method, staticmethod(forbidden))


def test_invalid_or_missing_host_response_fails_closed(tmp_path, monkeypatch) -> None:
    """Accepting absent, hash-altered, unsafe, or unevidenced output is a bug."""
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    cases = [
        (
            "missing",
            MissingResponseHost(),
            "LLM.CAPABILITY_DISABLED",
        ),
        (
            "altered-hash",
            MutatingResponseHost(
                lambda payload: payload["responses"][0].__setitem__(
                    "request_hash", "0" * 64
                )
            ),
            "AGENT_JUDGEMENT.RESPONSE_INVALID",
        ),
        (
            "unsafe-path",
            MutatingResponseHost(
                lambda payload: payload["responses"][0]["interpretation"][
                    "fields"
                ].__setitem__("contract_name", "C:/unsafe/source.docx")
            ),
            "AGENT_JUDGEMENT.RESPONSE_INVALID",
        ),
        (
            "relation-without-evidence",
            MutatingResponseHost(
                lambda payload: payload["responses"][0]["interpretation"].__setitem__(
                    "evidence", []
                )
            ),
            "AGENT_JUDGEMENT.RESPONSE_INVALID",
        ),
    ]

    for name, host, expected_code in cases:
        root = tmp_path / name
        result = run_local_agent_dry_run(root, host=host)

        assert result["status"] == "blocked"
        assert result["error_code"] == expected_code
        assert result["hashes_before"] == result["hashes_after"]
        assert result["archive_execution"]["confirmed"] is False
        assert not list((root / "runtime").rglob("archive_result.json"))
        assert '"confirmed": true' not in json.dumps(result).lower()


def test_source_outside_explicit_root_is_rejected_before_bootstrap(
    tmp_path, monkeypatch
) -> None:
    """Resolving an explicit source outside root/source is a safety bug."""
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    outside = tmp_path / "outside.md"
    outside.write_text("synthetic but out of scope", encoding="utf-8")
    root = tmp_path / "dry-run"

    result = run_local_agent_dry_run(
        root, host=StrictSyntheticAgentHost(), source_paths=[outside]
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "SOURCE_BINDING.OUT_OF_SCOPE"
    assert not root.exists()


def test_existing_source_requires_explicit_synthetic_reuse_flag(
    tmp_path, monkeypatch
) -> None:
    """Silently taking ownership of an existing source directory is a bug."""
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    (root / "source").mkdir(parents=True)

    result = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.SOURCE_ALREADY_EXISTS"
    assert not (root / "runtime").exists()


def test_unrelated_existing_root_content_is_not_modified(tmp_path) -> None:
    """Bootstrapping over an unrelated directory can overwrite user data."""
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "not-a-dry-run"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("do not modify", encoding="utf-8")

    result = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.ROOT_NOT_EMPTY"
    assert sentinel.read_text(encoding="utf-8") == "do not modify"
    assert not (root / "source").exists()


def test_explicit_flag_reuses_only_recorded_synthetic_source(
    tmp_path, monkeypatch
) -> None:
    """Rejecting a hash-bound synthetic source after explicit reuse is a bug."""
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    first = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())
    second = run_local_agent_dry_run(
        root,
        host=StrictSyntheticAgentHost(),
        use_existing_synthetic_source=True,
    )

    assert first["status"] == second["status"] == "needs_review"
    assert first["hashes_before"] == second["hashes_before"]
    assert not list((root / "runtime").rglob("archive_result.json"))


def test_cli_requires_root_and_redacts_generated_paths(
    tmp_path, monkeypatch, capsys
) -> None:
    """Making root optional or printing the physical root is a CLI safety bug."""
    import pytest

    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import main

    with pytest.raises(SystemExit) as missing_root:
        main([])
    assert missing_root.value.code == 2

    root = tmp_path / "cli-dry-run"
    code = main(["--root", str(root)])
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert code == 0
    assert payload["status"] == "needs_review"
    assert str(root) not in output
    assert not list((root / "runtime").rglob("archive_result.json"))
