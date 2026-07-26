from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import pytest


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
                ].__setitem__("contract_name", "C:/unsafe/source.docx")  # repo-hygiene: allow=synthetic-path
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


def _make_directory_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert os.path.isjunction(link)


def _tree_hashes(root: Path) -> dict[str, str]:
    import hashlib

    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_root_symlink_is_rejected_before_external_target_is_touched(
    tmp_path, monkeypatch
) -> None:
    """Resolving a root symlink before rejecting it can redirect every write."""
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    external = tmp_path / "external-root"
    external.mkdir()
    sentinel = external / "sentinel.bin"
    sentinel.write_bytes(b"do-not-touch")
    root_link = tmp_path / "dry-run-link"
    root_link.symlink_to(external, target_is_directory=True)
    before = _tree_hashes(external)

    result = run_local_agent_dry_run(
        root_link, host=StrictSyntheticAgentHost()
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.PATH_REPARSE"
    assert _tree_hashes(external) == before
    assert not (external / "source").exists()


@pytest.mark.parametrize("linked_name", ["source", "runtime", "config", "archive"])
def test_fixed_child_junction_is_rejected_before_external_writes(
    tmp_path, monkeypatch, linked_name
) -> None:
    """Following a fixed-child junction can expose source or runtime state."""
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / f"dry-run-{linked_name}"
    first = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())
    assert first["status"] == "needs_review"

    external = tmp_path / f"external-{linked_name}"
    child = root / linked_name
    if child.exists():
        child.rename(external)
    else:
        external.mkdir()
        (external / "sentinel.bin").write_bytes(b"do-not-touch")
    _make_directory_junction(child, external)
    before = _tree_hashes(external)

    result = run_local_agent_dry_run(
        root,
        host=StrictSyntheticAgentHost(),
        use_existing_synthetic_source=True,
        include_archive_binding=linked_name == "archive",
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.PATH_REPARSE"
    assert _tree_hashes(external) == before
    assert not list(external.rglob("archive_result.json"))


@pytest.mark.parametrize(
    "network_root",
    [
        r"\\synthetic-server\synthetic-share\dry-run",  # repo-hygiene: allow=synthetic-path
        "//synthetic-server/synthetic-share/dry-run",  # repo-hygiene: allow=synthetic-path
        "smb://synthetic-server/synthetic-share/dry-run",
        "nfs://synthetic-server/synthetic-share/dry-run",
        "afp://synthetic-server/synthetic-share/dry-run",
    ],
)
def test_obvious_network_root_is_rejected_before_filesystem_access(
    monkeypatch, network_root
) -> None:
    """Any filesystem call before a lexical network-root block can touch remote I/O."""
    import scripts.run_local_agent_dry_run as module

    filesystem_calls = []

    def forbidden(*args, **_kwargs):
        filesystem_calls.append(args)
        raise AssertionError("network root reached filesystem access")

    monkeypatch.setattr(module.os, "lstat", forbidden)
    monkeypatch.setattr(module, "Path", forbidden)

    result = module.run_local_agent_dry_run(
        network_root, host=StrictSyntheticAgentHost()
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.ROOT_NETWORK"
    assert filesystem_calls == []


def test_reuse_rejects_forged_source_and_self_authored_marker(
    tmp_path, monkeypatch
) -> None:
    """A marker inside source cannot be the trust anchor for source reuse."""
    import hashlib

    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    first = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())
    assert first["status"] == "needs_review"

    source = root / "source"
    project_code = "".join(("PRJ", "-001"))
    contract_code = "".join(("CT", "-001"))
    forged = source / "synthetic-governance.md"
    forged.write_text(
        f"# Synthetic governance\n\n项目编号：{project_code}\n合同编号：{contract_code}\n"
        "伪造内容：marker 不能为自己授权\n",
        encoding="utf-8",
    )
    marker = source / ".local-agent-dry-run.synthetic.v1.json"
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    marker_payload["hashes"][forged.name] = hashlib.sha256(
        forged.read_bytes()
    ).hexdigest()
    marker.write_text(json.dumps(marker_payload), encoding="utf-8")
    runtime_before = _tree_hashes(root / "runtime")

    result = run_local_agent_dry_run(
        root,
        host=StrictSyntheticAgentHost(),
        use_existing_synthetic_source=True,
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.SOURCE_UNSAFE"
    assert _tree_hashes(root / "runtime") == runtime_before


def test_acceptance_gate_rejects_missing_disabled_ocr_failure_and_cli_is_nonzero(
    tmp_path, monkeypatch, capsys
) -> None:
    from tools.data_cleaning_tools import DataCleaningTools

    _forbid_external_providers(monkeypatch)
    monkeypatch.setattr(
        DataCleaningTools,
        "prepare_file_organization_run",
        lambda _self, _paths: {"status": "success", "failures": []},
    )
    from scripts.run_local_agent_dry_run import main

    root = tmp_path / "dry-run"
    code = main(["--root", str(root)])
    payload = json.loads(capsys.readouterr().out)

    assert code != 0
    assert payload["status"] == "blocked"
    assert payload["error_code"] == "DRY_RUN.ACCEPTANCE_FAILED"


class SourceMutatingHost(StrictSyntheticAgentHost):
    def __init__(self, source_path: Path) -> None:
        super().__init__()
        self.source_path = source_path

    def write_responses(self, request_path: Path, response_path: Path) -> None:
        super().write_responses(request_path, response_path)
        self.source_path.write_text("mutated after hand-off", encoding="utf-8")


def test_acceptance_gate_rejects_source_hash_change(tmp_path, monkeypatch) -> None:
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    result = run_local_agent_dry_run(
        root,
        host=SourceMutatingHost(root / "source" / "synthetic-governance.md"),
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.ACCEPTANCE_FAILED"
    assert result["hashes_before"] != result["hashes_after"]


def test_acceptance_gate_rejects_any_archive_result(tmp_path, monkeypatch) -> None:
    from tools.data_cleaning_tools import DataCleaningTools

    _forbid_external_providers(monkeypatch)
    original = DataCleaningTools.execute_archive_plan
    root = tmp_path / "dry-run"

    def plant_archive_result(self, run_id, confirmed=False):
        result = original(self, run_id, confirmed=confirmed)
        artifact = root / "runtime" / "runs" / run_id / "archive_result.json"
        artifact.write_text('{"executed": false}', encoding="utf-8")
        return result

    monkeypatch.setattr(
        DataCleaningTools, "execute_archive_plan", plant_archive_result
    )
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    result = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.ACCEPTANCE_FAILED"


@pytest.mark.parametrize(
    "method_name",
    [
        "verify_file_organization_run",
        "audit_file_organization_run",
        "prepare_feedback_form",
    ],
)
def test_acceptance_gate_requires_successful_post_run_checks(
    tmp_path, monkeypatch, method_name
) -> None:
    from tools.data_cleaning_tools import DataCleaningTools

    _forbid_external_providers(monkeypatch)
    monkeypatch.setattr(
        DataCleaningTools,
        method_name,
        lambda _self, _run_id: {"status": "failed"},
    )
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    result = run_local_agent_dry_run(
        tmp_path / method_name, host=StrictSyntheticAgentHost()
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.ACCEPTANCE_FAILED"


@pytest.mark.parametrize(
    "counterexample",
    ["native_status", "invoice_candidate", "review_item", "confirmed_action"],
)
def test_acceptance_gate_rejects_invalid_review_only_result(
    tmp_path, monkeypatch, counterexample
) -> None:
    from tools.data_cleaning_tools import DataCleaningTools

    _forbid_external_providers(monkeypatch)
    original = DataCleaningTools.resume_agent_judgement_run

    def mutate_result(self, run_id, response_path):
        result = original(self, run_id, response_path)
        if counterexample == "native_status":
            result["candidate_interpretations"][0]["status"] = "success"
        elif counterexample == "invoice_candidate":
            invoice = next(
                item
                for item in result["candidate_interpretations"]
                if item["source_ref"]["object_key"] == "synthetic-invoice.pdf"
            )
            invoice["business_relation"]["candidate_contract_id"] = "C-999"
        elif counterexample == "review_item":
            result["review_queue"]["items"][0]["question"] = ""
        else:
            result["archive_actions"][0]["confirmed"] = True
        return result

    monkeypatch.setattr(
        DataCleaningTools, "resume_agent_judgement_run", mutate_result
    )
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    result = run_local_agent_dry_run(
        tmp_path / counterexample, host=StrictSyntheticAgentHost()
    )

    assert result["status"] == "blocked"
    assert result["error_code"] == "DRY_RUN.ACCEPTANCE_FAILED"


def test_scan_fixture_is_a_raster_image_only_pdf(tmp_path, monkeypatch) -> None:
    import fitz

    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    result = run_local_agent_dry_run(root, host=StrictSyntheticAgentHost())

    assert result["status"] == "needs_review"
    with fitz.open(root / "source" / "synthetic-scan.pdf") as scan:
        assert scan.page_count == 1
        page = scan[0]
        assert page.get_text("text").strip() == ""
        assert page.get_images(full=True)
        assert page.get_drawings() == []


def test_unexpected_cli_failure_redacts_root_from_stderr(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.run_local_agent_dry_run as module

    root = tmp_path / "private-dry-run-root"

    def fail_with_sensitive_path(*_args, **_kwargs):
        raise OSError(f"cannot write {root}")

    monkeypatch.setattr(module, "run_local_agent_dry_run", fail_with_sensitive_path)

    code = module.main(["--root", str(root)])
    captured = capsys.readouterr()

    assert code != 0
    assert str(root) not in captured.err
    assert json.loads(captured.err)["error_code"] == "DRY_RUN.UNEXPECTED"


def test_independent_archive_binding_e2e_remains_review_only(
    tmp_path, monkeypatch
) -> None:
    _forbid_external_providers(monkeypatch)
    from scripts.run_local_agent_dry_run import run_local_agent_dry_run

    root = tmp_path / "dry-run"
    result = run_local_agent_dry_run(
        root,
        host=StrictSyntheticAgentHost(),
        include_archive_binding=True,
    )

    assert result["status"] == "needs_review"
    config = json.loads(
        (root / "config" / "project-manager.local.json").read_text(
            encoding="utf-8"
        )
    )
    bindings = {item["binding_id"]: item for item in config["storage_bindings"]}
    assert set(bindings) == {"dry-run-source", "dry-run-archive"}
    archive_binding = bindings["dry-run-archive"]
    archive_root = Path(archive_binding["physical_root"]).resolve()
    assert archive_binding["roles"] == ["archive_target"]
    assert archive_binding["readable"] is False
    assert archive_binding["writable"] is True
    assert archive_root == (root / "archive").resolve()
    assert archive_root != (root / "source").resolve()
    assert archive_root != (root / "runtime").resolve()
    assert list(archive_root.iterdir()) == []
    plan = json.loads(
        next((root / "runtime" / "runs").glob("*/planned_archive_actions.json"))
        .read_text(encoding="utf-8")
    )
    assert plan["actions"]
    assert all(action["confirmed"] is False for action in plan["actions"])
    assert not list((root / "runtime").rglob("archive_result.json"))
