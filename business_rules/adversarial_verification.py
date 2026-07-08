"""Read-only evaluator for extracted facts, ledger results, and archive plans."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from contracts.review_dimensions import DOCUMENT_REQUIRED_FIELDS, OCR_CONFIDENCE_THRESHOLD


SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def run_adversarial_verification(
    *,
    workspace_dir: str,
    run_id: str,
    extracted_items: List[Dict[str, Any]],
    ledger_results: Optional[List[Dict[str, Any]]] = None,
    archive_actions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Review run artifacts and persist a verification report without mutating business state."""

    findings: List[Dict[str, Any]] = []
    findings.extend(_review_ocr_quality(extracted_items))
    if not findings:
        findings.extend(_review_field_completeness(extracted_items))
        findings.extend(_review_field_accuracy(extracted_items))
        findings.extend(_review_archive_plan(archive_actions or []))

    result = {
        "schema_version": "adversarial_verification.v1",
        "status": "success",
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(),
        "overall_verdict": _overall_verdict(findings),
        "finding_count": len(findings),
        "findings": findings,
        "ledger_result_count": len(ledger_results or []),
        "archive_action_count": len(archive_actions or []),
    }
    result["artifact_path"] = _write_artifact(workspace_dir, run_id, result)
    return result


def _review_ocr_quality(extracted_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    for item in extracted_items:
        confidences = [
            float(page.get("confidence", 0.85))
            for page in (item.get("ocr") or {}).get("pages", [])
            if page.get("confidence") is not None
        ]
        if not confidences:
            continue
        average = sum(confidences) / len(confidences)
        if average < OCR_CONFIDENCE_THRESHOLD:
            findings.append(_finding(
                "ocr_quality",
                "high",
                item,
                f"OCR confidence {average:.2f} is below threshold {OCR_CONFIDENCE_THRESHOLD:.2f}.",
                {"ocr_confidence": round(average, 4), "threshold": OCR_CONFIDENCE_THRESHOLD},
            ))
    return findings


def _review_field_completeness(extracted_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    for item in extracted_items:
        fields = item.get("fields") or {}
        document_type = item.get("document_type") or fields.get("document_type") or "未分类"
        required = DOCUMENT_REQUIRED_FIELDS.get(document_type, DOCUMENT_REQUIRED_FIELDS["未分类"])
        missing = [field for field in required if not fields.get(field)]
        if missing:
            findings.append(_finding(
                "field_completeness",
                "high" if len(missing) >= 2 else "medium",
                item,
                "Required fields are missing from extracted fields.",
                {"document_type": document_type, "missing_fields": missing, "available_fields": sorted(fields)},
            ))
    return findings


def _review_field_accuracy(extracted_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    for item in extracted_items:
        text = item.get("extracted_text") or ""
        if not text:
            continue
        fields = item.get("fields") or {}
        for field in ("budget", "quoted_amount", "amount"):
            value = fields.get(field)
            if value and not _value_is_supported_by_text(value, text):
                findings.append(_finding(
                    "field_accuracy",
                    "medium",
                    item,
                    f"Extracted field {field} is not supported by the source text.",
                    {"field": field, "value": value},
                ))
        for field in ("deadline", "bid_deadline", "registration_deadline", "bid_open_time"):
            value = fields.get(field)
            if value and not re.search(r"\d{4}[-年/]\d{1,2}", str(value)):
                findings.append(_finding(
                    "field_accuracy",
                    "medium",
                    item,
                    f"Extracted date field {field} does not look like a date.",
                    {"field": field, "value": value},
                ))
    return findings


def _review_archive_plan(archive_actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings = []
    for action in archive_actions:
        project_name = str(action.get("project_name") or "").strip()
        target_path = str(action.get("target_path") or "")
        if project_name and _safe_name(project_name) not in target_path:
            findings.append({
                "id": "",
                "dimension": "archive_plan",
                "severity": "low",
                "confidence": 0.65,
                "file": action.get("source_file", ""),
                "message": "Archive target path does not include the project name.",
                "details": {"project_name": project_name, "target_path": target_path},
            })
    return _assign_ids(findings)


def _overall_verdict(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return "pass"
    if any(finding.get("dimension") == "ocr_quality" for finding in findings):
        return "needs_human_review"
    if any(finding.get("severity") == "high" for finding in findings):
        return "needs_correction"
    if any(finding.get("severity") == "medium" for finding in findings):
        return "pass_with_warnings"
    return "pass_with_warnings"


def _finding(
    dimension: str,
    severity: str,
    item: Dict[str, Any],
    message: str,
    details: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "id": "",
        "dimension": dimension,
        "severity": severity,
        "confidence": {"high": 0.88, "medium": 0.78, "low": 0.65}.get(severity, 0.70),
        "file": item.get("file", ""),
        "message": message,
        "details": details,
    }


def _assign_ids(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for idx, finding in enumerate(findings, 1):
        finding["id"] = f"AV{idx:03d}"
    return findings


def _write_artifact(workspace_dir: str, run_id: str, result: Dict[str, Any]) -> str:
    run_dir = os.path.join(workspace_dir, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    artifact_path = os.path.join(run_dir, "adversarial_verification.json")
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return artifact_path


def _value_is_supported_by_text(value: Any, text: str) -> bool:
    value_text = str(value).strip()
    if value_text and value_text in text:
        return True
    value_digits = re.sub(r"[^\d.]", "", value_text)
    text_digits = re.sub(r"[^\d.]", "", text)
    return bool(value_digits and value_digits in text_digits)


def _safe_name(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", str(name))
    return safe.strip("_") or "unknown"

