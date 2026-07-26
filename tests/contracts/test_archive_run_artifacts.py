from __future__ import annotations


def _strict_contract_artifact(run_id: str) -> dict:
    digest = "a" * 64
    source_ref = {
        "storage_provider": "local",
        "object_key": "synthetic-contract.docx",
        "logical_uri": "business://dry-run-source/synthetic-contract.docx",
        "binding_id": "dry-run-source",
    }
    return {
        "schema_version": "file_organization.extracted_document.v1",
        "run_id": run_id,
        "parse_artifact_ref": f"artifact:parsed:{digest[:24]}",
        "source_ref": source_ref,
        "content_hash": digest,
        "document_type": "合同",
        "classification": {
            "document_type": "合同",
            "business_domain": "bid_project",
            "project_phase": None,
            "archive_phase": None,
            "confidence": 0.9,
            "evidence": ["synthetic"],
            "requires_review": False,
        },
        "candidate_fields": {"project_name": "synthetic-project"},
        "text_length": 17,
    }


def test_strict_extracted_artifact_projects_to_verification_input() -> None:
    from contracts.archive_run_artifacts import (
        project_extracted_document_for_verification,
    )

    payload = _strict_contract_artifact("run_projection_test")

    projected = project_extracted_document_for_verification(
        payload, "run_projection_test"
    )

    assert projected == {
        "file": "business://dry-run-source/synthetic-contract.docx",
        "document_type": "合同",
        "fields": {"project_name": "synthetic-project"},
        "classification": payload["classification"],
        "text_length": 17,
        "source_ref": payload["source_ref"],
        "content_hash": "a" * 64,
    }
    assert projected["fields"] is not payload["candidate_fields"]
    assert projected["classification"] is not payload["classification"]
    assert projected["source_ref"] is not payload["source_ref"]
