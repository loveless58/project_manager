import json
import os
from datetime import datetime
from typing import Any, Dict, List

from tools.data_cleaning_tools import DataCleaningTools


MODULES = [
    "file_organization_run_package",
    "project_overview_ledger",
    "business_rule_judgement",
    "image_scanned_document_processing",
]


def run_goal_validation(workspace_dir: str) -> Dict[str, Any]:
    """Run the three required validation cases and score the four target modules."""
    os.makedirs(workspace_dir, exist_ok=True)
    cases_root = os.path.join(workspace_dir, "goal_validation_cases")
    os.makedirs(cases_root, exist_ok=True)

    cases = [
        _run_normal_simulation(cases_root),
        _run_failure_simulation(cases_root),
        _run_real_case_simulation(cases_root),
    ]
    module_scores = _score_modules(cases)
    below_threshold = {
        module: score for module, score in module_scores.items()
        if score["score"] < 85
    }
    optimization_cycles = [{
        "cycle": 1,
        "status": "pass" if not below_threshold else "needs_optimization",
        "below_threshold": below_threshold,
        "feedback_for_next_iteration": {
            module: score["feedback"] for module, score in below_threshold.items()
        },
    }]

    report = {
        "schema_version": "project_manager.goal_validation.v1",
        "created_at": datetime.now().isoformat(),
        "cases": cases,
        "module_scores": module_scores,
        "below_threshold": below_threshold,
        "optimization_cycles": optimization_cycles,
        "final_status": "pass" if not below_threshold else "needs_optimization",
    }
    report_path = os.path.join(workspace_dir, "goal_validation_report.json")
    summary_path = os.path.join(workspace_dir, "goal_validation_summary.md")
    _write_json(report_path, report)
    _write_summary(summary_path, report)
    report["report_path"] = report_path
    report["summary_path"] = summary_path
    _write_json(report_path, report)
    return report


def _run_normal_simulation(cases_root: str) -> Dict[str, Any]:
    case_dir = os.path.join(cases_root, "normal_simulation")
    os.makedirs(case_dir, exist_ok=True)
    source_path = os.path.join(case_dir, "采购公告.docx")
    _write_docx(source_path, [
        "航天时代飞鸿技术有限公司",
        "《采购公告》",
        "项目名称：打印刻录系统采购项目",
        "采购人：测试客户",
        "报名截止时间：2026-07-10",
        "开标时间：2026-07-12",
    ])
    tools = DataCleaningTools(workspace_dir=os.path.join(case_dir, "workspace"))
    result = tools.prepare_file_organization_run([source_path])
    return _case_report("normal_simulation", "success", result)


def _run_failure_simulation(cases_root: str) -> Dict[str, Any]:
    case_dir = os.path.join(cases_root, "failure_simulation")
    os.makedirs(case_dir, exist_ok=True)
    source_path = os.path.join(case_dir, "无OCR扫描公告.png")
    scanned_pdf = os.path.join(case_dir, "无OCR扫描PDF.pdf")
    _write_png(source_path)
    _write_blank_pdf(scanned_pdf)
    tools = DataCleaningTools(workspace_dir=os.path.join(case_dir, "workspace"))
    result = tools.prepare_file_organization_run([source_path, scanned_pdf])
    return _case_report("failure_simulation", "failed", result)


def _run_real_case_simulation(cases_root: str) -> Dict[str, Any]:
    case_dir = os.path.join(cases_root, "real_case_simulation")
    os.makedirs(case_dir, exist_ok=True)
    docx_path = os.path.join(case_dir, "真实案例-采购公告.docx")
    scanned_pdf = os.path.join(case_dir, "真实案例-扫描补充.pdf")
    _write_docx(docx_path, [
        "项目名称：工业互联网网络基础条件项目",
        "采购人：某工业互联网公司",
        "项目详情：服务器区防火墙系统升级采购",
        "中标结果：已中标",
        "合同状态：未签约",
        "销售负责人：张三",
    ])
    with open(scanned_pdf, "wb") as f:
        f.write(b"%PDF-1.4\n% scanned validation fixture\n")
    with open(f"{scanned_pdf}.ocr.txt", "w", encoding="utf-8") as f:
        f.write("项目名称：工业互联网网络基础条件项目\n采购人：某工业互联网公司\n补充说明：扫描件OCR旁路文本")

    tools = DataCleaningTools(workspace_dir=os.path.join(case_dir, "workspace"))
    result = tools.prepare_file_organization_run([docx_path, scanned_pdf])
    return _case_report("real_case_simulation", "success", result)


def _case_report(case_name: str, expected_status: str, result: Dict[str, Any]) -> Dict[str, Any]:
    structured = _load_structured_outputs(result.get("structured_outputs", []))
    ledger_artifacts = [
        item.get("ledger_artifacts", {}) for item in structured
        if item.get("ledger_artifacts")
    ]
    ledger_checks = [_inspect_ledger_artifact(artifact) for artifact in ledger_artifacts]
    business_judgements = [
        item.get("business_judgement", {}) for item in structured
        if item.get("business_judgement")
    ]
    ocr_extractions = [
        item.get("extraction", {}) for item in structured
        if item.get("extraction", {}).get("extract_method") == "ocr"
    ]
    failure_errors = " ".join(str(item.get("error", "")) for item in result.get("failures", []))
    blocked_reasons = {
        str(value)
        for item in result.get("failures", [])
        for value in [
            item.get("blocked_reason"),
            item.get("ocr", {}).get("blocked_reason") if isinstance(item.get("ocr"), dict) else None,
        ]
        if value
    }
    artifacts = result.get("artifacts", {})

    module_evidence = {
        "file_organization": {
            "has_run_package": all(os.path.exists(artifacts.get(name, "")) for name in [
                "input_manifest",
                "review_queue",
                "planned_archive_actions",
                "run_report",
                "trace",
            ]),
            "processed": result.get("processed", 0),
            "failed": result.get("failed", 0),
        },
        "project_ledger": {
            "has_ledger_artifacts": any(
                os.path.exists(artifact.get("project_overview_md", ""))
                and os.path.exists(artifact.get("project_ledger_json", ""))
                for artifact in ledger_artifacts
            ),
            "ledger_artifacts": ledger_artifacts,
            "ledger_checks": ledger_checks,
            "has_decision_log": any(check["has_decision_log"] for check in ledger_checks),
            "has_evidence_index": any(check["has_evidence_index"] for check in ledger_checks),
            "has_persisted_business_judgement": any(check["has_business_judgement"] for check in ledger_checks),
        },
        "business_rules": {
            "has_business_judgement": any(j.get("schema_version") == "bid_project.business_judgement.v1" for j in business_judgements),
            "business_judgements": business_judgements,
        },
        "scanned_document_processing": {
            "has_successful_ocr": any(extraction.get("ocr", {}).get("status") == "success" for extraction in ocr_extractions),
            "blocked_without_ocr": "ocr_adapter_unavailable" in blocked_reasons or "ocr_adapter_unavailable" in failure_errors,
            "ocr_extractions": ocr_extractions,
        },
    }
    return {
        "case": case_name,
        "expected_status": expected_status,
        "result": result,
        "artifacts": artifacts,
        "module_evidence": module_evidence,
    }


def _score_modules(cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    normal_or_real = [case for case in cases if case["expected_status"] == "success"]
    failure = [case for case in cases if case["expected_status"] == "failed"]

    checks = {
        "file_organization_run_package": [
            ("all cases write run package artifacts", all(case["module_evidence"]["file_organization"]["has_run_package"] for case in cases)),
            ("success cases process at least one file", all(case["result"].get("processed", 0) >= 1 for case in normal_or_real)),
            ("failure case preserves failure evidence", all(case["result"].get("failed", 0) >= 1 for case in failure)),
        ],
        "project_overview_ledger": [
            ("success cases write ledger artifacts", all(case["module_evidence"]["project_ledger"]["has_ledger_artifacts"] for case in normal_or_real)),
            ("success cases write decision logs", all(case["module_evidence"]["project_ledger"]["has_decision_log"] for case in normal_or_real)),
            ("success cases persist evidence index", all(case["module_evidence"]["project_ledger"]["has_evidence_index"] for case in normal_or_real)),
            ("success cases persist business judgement", all(case["module_evidence"]["project_ledger"]["has_persisted_business_judgement"] for case in normal_or_real)),
            ("failure case does not fabricate ledger success", all(not case["module_evidence"]["project_ledger"]["has_ledger_artifacts"] for case in failure)),
        ],
        "business_rule_judgement": [
            ("success cases include business judgement", all(case["module_evidence"]["business_rules"]["has_business_judgement"] for case in normal_or_real)),
            ("at least one validation case reaches non-unknown business stage", any(
                _has_non_unknown_stage(case["module_evidence"]["business_rules"]["business_judgements"])
                for case in normal_or_real
            )),
            ("real case reaches won_pending_contract stage", any(
                case["case"] == "real_case_simulation"
                and _has_stage(case["module_evidence"]["business_rules"]["business_judgements"], "won_pending_contract")
                for case in normal_or_real
            )),
            ("non-unknown business judgements include next actions", all(
                _non_unknown_stages_have_next_actions(case["module_evidence"]["business_rules"]["business_judgements"])
                for case in normal_or_real
            )),
            ("business judgement has next action or stage", all(
                _has_rule_content(case["module_evidence"]["business_rules"]["business_judgements"])
                for case in normal_or_real
            )),
        ],
        "image_scanned_document_processing": [
            ("OCR is exposed as explicit data-cleaning tool", _has_registered_ocr_tool()),
            ("scanned/image without OCR is blocked", any(case["module_evidence"]["scanned_document_processing"]["blocked_without_ocr"] for case in failure)),
            ("scanned PDF sidecar OCR succeeds", any(case["module_evidence"]["scanned_document_processing"]["has_successful_ocr"] for case in normal_or_real)),
        ],
    }

    scores: Dict[str, Dict[str, Any]] = {}
    for module, module_checks in checks.items():
        passed = [description for description, ok in module_checks if ok]
        failed = [description for description, ok in module_checks if not ok]
        score = int(round(100 * len(passed) / len(module_checks))) if module_checks else 0
        scores[module] = {
            "score": score,
            "threshold": 85,
            "status": "pass" if score >= 85 else "needs_optimization",
            "evidence": passed,
            "feedback": failed,
        }
    return scores


def _has_rule_content(judgements: List[Dict[str, Any]]) -> bool:
    for judgement in judgements:
        if judgement.get("business_stage") or judgement.get("next_actions") or judgement.get("risk_reasons"):
            return True
    return False


def _has_non_unknown_stage(judgements: List[Dict[str, Any]]) -> bool:
    return any(judgement.get("business_stage") not in {"", None, "unknown"} for judgement in judgements)


def _has_stage(judgements: List[Dict[str, Any]], stage: str) -> bool:
    return any(judgement.get("business_stage") == stage for judgement in judgements)


def _non_unknown_stages_have_next_actions(judgements: List[Dict[str, Any]]) -> bool:
    non_unknown = [
        judgement for judgement in judgements
        if judgement.get("business_stage") not in {"", None, "unknown"}
    ]
    return all(bool(judgement.get("next_actions")) for judgement in non_unknown)


def _inspect_ledger_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    state_path = artifact.get("project_ledger_json", "")
    project_dir = os.path.dirname(state_path) if state_path else ""
    decision_log = os.path.join(project_dir, "decision_log.jsonl") if project_dir else ""
    state = {}
    if state_path and os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            state = {}
    return {
        "project_ledger_json": state_path,
        "decision_log": decision_log,
        "has_decision_log": bool(decision_log and os.path.exists(decision_log) and os.path.getsize(decision_log) > 0),
        "has_evidence_index": bool(state.get("evidence_index")),
        "has_business_judgement": bool(state.get("business_judgement")),
    }


def _load_structured_outputs(paths: List[str]) -> List[Dict[str, Any]]:
    items = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                items.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return items


def _write_docx(path: str, paragraphs: List[str]) -> None:
    from docx import Document

    doc = Document()
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    doc.save(path)


def _write_png(path: str) -> None:
    from PIL import Image

    Image.new("RGB", (320, 120), color="white").save(path)


def _write_blank_pdf(path: str) -> None:
    try:
        import fitz

        doc = fitz.open()
        doc.new_page(width=320, height=160)
        doc.save(path)
        doc.close()
    except ImportError:
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4\n% scanned validation fixture placeholder\n")


def _has_registered_ocr_tool() -> bool:
    try:
        import main

        return "run_ocr" in main._build_registry_for_skill("data_cleaning_file_organization").list_tools()
    except Exception:
        return False


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_summary(path: str, report: Dict[str, Any]) -> None:
    lines = [
        "# Project Manager Goal Validation",
        "",
        f"- schema_version: `{report['schema_version']}`",
        f"- created_at: `{report['created_at']}`",
        "",
        "## Cases",
    ]
    for case in report["cases"]:
        lines.append(f"- {case['case']}: status={case['result'].get('status')} processed={case['result'].get('processed')} failed={case['result'].get('failed')}")
    lines.extend(["", "## Module Scores"])
    for module, score in report["module_scores"].items():
        lines.append(f"- {module}: {score['score']} ({score['status']})")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
