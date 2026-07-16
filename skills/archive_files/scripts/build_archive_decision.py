"""build_archive_decision.py — 评估文件归档动作

提取自 tools/data_cleaning_tools.py（Phase 2 拆分）。

公开 API：
- build_archive_action(...)         构造 archive_action.v1 dict
- business_phase_from_path(...)     从路径识别业务阶段

依赖：business_rules.archive_decision.evaluate_archive_decision

注意：
- 顺手修了原 _business_phase_from_path 的 phase 列表缺"项目弃标"的 bug。
  原来只检查 3 个 phase（项目投标/项目执行/项目丢标），
  现在是 4 个（项目投标/项目弃标/项目丢标/项目执行）。
"""
import os
import re
from typing import Any, Dict, List

from business_rules.archive_decision import evaluate_archive_decision


def _path_parts(source_path: str) -> List[str]:
    """把路径按 / 或 \\ 拆成段。"""
    return [part for part in re.split(r"[\\/]+", os.path.normpath(str(source_path))) if part]


def business_phase_from_path(source_path: str) -> str:
    """从路径里识别业务阶段。

    返回 4 阶段之一：项目投标 / 项目弃标 / 项目丢标 / 项目执行。
    路径里没有这 4 个 phase 时返回空字符串。
    """
    parts = _path_parts(source_path)
    for phase in ("项目投标", "项目弃标", "项目丢标", "项目执行"):
        if phase in parts:
            return phase
    return ""


def build_archive_action(
    run_id: str,
    source_file: str,
    project_name: str,
    extracted: Dict[str, Any],
    ledger_result: Dict[str, Any],
    project_files_dir: str,
) -> Dict[str, Any]:
    """构建单文件的 archive action 字典（archive_action.v1 schema）。

    提取自 DataCleaningTools._build_archive_action。
    原 self.project_files_dir 改为显式参数。

    返回字段：
    - status: already_archived / ready / needs_review
    - blockers: list[str] 阻碍动作的标记（target_exists / source_missing / fact_gate.blocked_reason）
    - archive_decision: evaluate_archive_decision 的原始输出
    """
    judgement = ledger_result.get("business_judgement", {})
    decision = evaluate_archive_decision(
        source_file=source_file,
        extracted=extracted,
        business_judgement=judgement,
        project_files_dir=project_files_dir,
    )
    document_type = decision.get("document_type") or extracted.get("document_type") or "未分类"
    target_dir = decision["target_dir"]
    target_path = decision["target_path"]
    project_name = decision.get("subject_name") or project_name
    blockers = list(decision.get("blockers", []))
    fact_gate = extracted.get("business_fact_gate") or {}
    if fact_gate.get("blocked_reason"):
        decision.setdefault("reasons", []).append(fact_gate["blocked_reason"])

    if decision.get("archive_phase") is None:
        # archive_decision 返回了未决标记（如 PM 内部文档归档待重做），
        # target_dir / target_path 暂时为 None，等 phase 重设计后再算。
        proposed_name = ""
        target_abs = ""
        already_archived = False
    else:
        proposed_name = os.path.basename(target_path)
        target_abs = os.path.normcase(os.path.abspath(target_path))

    source_abs = os.path.normcase(os.path.abspath(source_file))
    if target_path is not None:
        already_archived = source_abs == target_abs
        if os.path.exists(target_path) and not already_archived:
            blockers.append("target_exists")
    else:
        already_archived = False

    if not os.path.exists(source_file):
        blockers.append("source_missing")
    return {
        "schema_version": "archive_action.v1",
        "run_id": run_id,
        "status": "already_archived" if already_archived and not blockers else ("ready" if not blockers else "needs_review"),
        "source_file": source_file,
        "project_name": project_name,
        "document_type": document_type,
        "proposed_name": proposed_name,
        "target_dir": target_dir,
        "target_path": target_path,
        "blockers": blockers,
        "business_judgement": judgement,
        "archive_decision": decision,
    }


__all__ = ["build_archive_action", "business_phase_from_path"]
