import os
import re
from pathlib import Path
from typing import Any, Dict, List


INTERNAL_PROJECT_NAME = "project_manager"


def evaluate_archive_decision(
    source_file: str,
    extracted: Dict[str, Any],
    business_judgement: Dict[str, Any],
    project_files_dir: str,
) -> Dict[str, Any]:
    """Evaluate archive subject and target independently from extraction."""
    filename = extracted.get("filename") or os.path.basename(source_file)
    text = extracted.get("extracted_text", "") or ""
    fields = extracted.get("fields") or {}

    if _is_project_manager_internal_document(filename, text):
        # TODO: PM 内部文档归档 phase 待重新设计 (2026-07-15)
        # 原"项目归档"作为业务状态删除后，这里临时返回未决标记，
        # 避免 PM 内部文档被错误归档到业务项目目录。
        return {
            "schema_version": "archive_decision.v1",
            "subject_type": "internal_project",
            "subject_name": INTERNAL_PROJECT_NAME,
            "archive_phase": None,
            "document_type": "项目治理文档",
            "confidence": 0.0,
            "target_dir": None,
            "target_path": None,
            "blockers": ["pm_internal_archive_pending_redesign"],
            "human_review_required": True,
            "reasons": ["pm_internal_archive_needs_redesign"],
        }

    project_name = fields.get("project_name") or ""
    if not project_name and filename == "项目记录.md":
        parent_name = Path(source_file).parent.name
        if parent_name and parent_name not in {"项目投标", "项目弃标", "项目丢标", "项目执行"}:
            project_name = parent_name

    archive_phase = _archive_phase_for_bid_project(source_file, fields, business_judgement)
    blockers: List[str] = []
    if project_name in {"", "未命名项目", "鏈懡鍚嶉」鐩?"}:
        blockers.append("unknown_project")
    if (
        business_judgement.get("human_review_required")
        and archive_phase != "项目丢标"
        and extracted.get("extract_method") != "metadata_passthrough"
    ):
        blockers.append("human_review_recommended")

    subject_name = project_name or "未命名项目"
    return _decision(
        source_file=source_file,
        subject_type="bid_project" if project_name else "unknown",
        subject_name=subject_name,
        archive_phase=archive_phase,
        document_type=extracted.get("document_type") or "未分类",
        confidence=0.72 if archive_phase == "项目丢标" and project_name else (0.45 if project_name else 0.0),
        project_files_dir=project_files_dir,
        blockers=blockers,
        human_review_required=bool(blockers),
        reasons=["closed_lost_project_policy"] if archive_phase == "项目丢标" else ["fallback_bid_project_policy"],
    )


def _decision(
    source_file: str,
    subject_type: str,
    subject_name: str,
    archive_phase: str,
    document_type: str,
    confidence: float,
    project_files_dir: str,
    blockers: List[str],
    human_review_required: bool,
    reasons: List[str],
) -> Dict[str, Any]:
    target_dir = os.path.join(project_files_dir, archive_phase, _safe_name(subject_name), *_target_bucket(source_file))
    target_path = os.path.join(target_dir, os.path.basename(source_file))
    return {
        "schema_version": "archive_decision.v1",
        "subject_type": subject_type,
        "subject_name": subject_name,
        "archive_phase": archive_phase,
        "document_type": document_type,
        "confidence": confidence,
        "target_dir": target_dir,
        "target_path": target_path,
        "blockers": blockers,
        "human_review_required": human_review_required,
        "reasons": reasons,
    }


def _is_project_manager_internal_document(filename: str, text: str) -> bool:
    haystack = f"{filename}\n{text}".lower()
    if "project_manager" in haystack:
        return True
    internal_markers = [
        "prd-project-manager",
        "归档摘要报告",
        "全量文件审计报告",
        "项目总览",
        "file organization",
        "workspace config",
        "ocr provider",
    ]
    return any(marker.lower() in haystack for marker in internal_markers)


def _archive_phase_for_bid_project(
    source_file: str,
    fields: Dict[str, Any],
    business_judgement: Dict[str, Any],
) -> str:
    path_parts = set(_path_parts(source_file))
    if "项目丢标" in path_parts:
        return "项目丢标"
    if "项目执行" in path_parts:
        return "项目执行"
    if fields.get("lifecycle_stage") in {"closed", "closed_lost"}:
        return "项目丢标"
    if fields.get("lifecycle_stage") in {"execution", "executing", "delivery"}:
        return "项目执行"
    if fields.get("bid_status") in {"弃标", "已弃标", "未中标", "已丢标", "丢标"}:
        return "项目丢标"
    if business_judgement.get("business_stage") in {"closed", "closed_lost"}:
        return "项目丢标"
    if business_judgement.get("business_stage") in {"execution", "executing", "delivery"}:
        return "项目执行"
    return "项目投标"


def _target_bucket(source_file: str) -> List[str]:
    path_parts = set(_path_parts(source_file))
    if path_parts.intersection({"数据资产", "数字资产"}):
        return ["数字资产", "导入资产"]
    return ["原始文件"]


def _safe_name(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", str(name))
    return safe.strip("_") or "unknown"


def _path_parts(source_file: str) -> List[str]:
    return [part for part in re.split(r"[\\/]+", os.path.normpath(str(source_file))) if part]
