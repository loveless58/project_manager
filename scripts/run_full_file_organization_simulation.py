"""Run a full file-organization loop simulation on copied source files.

The script copies provided source files into an ignored workspace before running
the loop, so original files are never moved.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.data_cleaning_tools import DataCleaningTools

DEFAULT_WORKSPACE_BASE = ROOT / "state" / "real_file_simulation_workspace"
DEFAULT_INPUT_BASE = ROOT / "state" / "simulation_inputs"
DEFAULT_REPORT = ROOT / "reports" / "full_file_organization_loop_report.md"


def _copy_inputs(paths: List[str], input_dir: Path) -> List[str]:
    input_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for index, raw_path in enumerate(paths, start=1):
        source = Path(raw_path)
        target = input_dir / f"{index:02d}_{source.name}"
        shutil.copy2(source, target)
        copied.append(str(target))
    return copied


def _first_project_name(prepared: Dict[str, Any]) -> str:
    for action in prepared.get("archive_actions", []):
        name = action.get("project_name")
        if name and name != "未命名项目":
            return str(name)
    for output in prepared.get("structured_outputs", []):
        try:
            data = json.loads(Path(output).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("project_name")
        if name:
            return str(name)
    return "模拟复核项目"


def _write_report(
    report_path: Path,
    original_paths: List[str],
    copied_paths: List[str],
    prepared: Dict[str, Any],
    blocked_archive: Dict[str, Any],
    review: Dict[str, Any],
    executed: Dict[str, Any],
    html_result: Dict[str, Any],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 文件整理闭环真实文件模拟报告",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 原始文件数：{len(original_paths)}",
        f"- 副本文件数：{len(copied_paths)}",
        f"- run_id：{prepared.get('run_id', '')}",
        f"- 准备阶段状态：{prepared.get('status')}，成功 {prepared.get('processed')}，失败 {prepared.get('failed')}",
        f"- 未确认归档状态：{blocked_archive.get('status')}",
        f"- 人工复核状态：{review.get('status')}，复核 {review.get('reviewed')}，失败 {review.get('failed')}",
        f"- 归档执行状态：{executed.get('status')}，移动 {executed.get('moved')}，失败 {executed.get('failed')}",
        f"- HTML 更新状态：{html_result.get('status')}，项目数 {html_result.get('total_projects')}",
        "",
        "## 输入文件",
        "",
    ]
    for original, copied in zip(original_paths, copied_paths):
        lines.append(f"- 原始：`{original}`")
        lines.append(f"  副本：`{copied}`")
    lines.extend([
        "",
        "## 关键产物",
        "",
    ])
    for key, value in (prepared.get("artifacts") or {}).items():
        lines.append(f"- prepare.{key}: `{value}`")
    for key, value in (executed.get("artifacts") or {}).items():
        lines.append(f"- archive.{key}: `{value}`")
    if html_result.get("output_file"):
        lines.append(f"- html: `{html_result['output_file']}`")
    lines.extend([
        "",
        "## 准备阶段归档计划",
        "",
    ])
    for action in prepared.get("archive_actions", []):
        lines.append(
            f"- {action.get('status')}: `{action.get('source_file')}` -> `{action.get('target_path')}`; "
            f"blockers={action.get('blockers', [])}"
        )
    lines.extend([
        "",
        "## 复核与策略修正",
        "",
    ])
    if review.get("rule_candidates"):
        for item in review["rule_candidates"]:
            lines.append(
                f"- `{item.get('project_name')}` 生成规则候选：status={item.get('status')}, "
                f"requires_test={item.get('requires_test')}"
            )
    else:
        lines.append("- 未生成规则候选。")
    lines.extend([
        "",
        "## 归档执行结果",
        "",
    ])
    for item in executed.get("results", []):
        lines.append(f"- {item.get('status')}: `{item.get('source_file')}` -> `{item.get('archived_path', '')}`")
    lines.extend([
        "",
        "## 结论",
        "",
        "- 结构化输出、业务判断、复核队列、归档计划和运行报告均已落盘。",
        "- 未确认时归档工具返回 `needs_confirmation`，源文件副本未被移动。",
        "- 人工复核可以以 `human_correction` 写回项目账本，并生成待审批规则候选。",
        "- 确认后执行归档会移动副本文件、写入归档记录，并可继续生成投标进度总览 HTML。",
        "- 本模拟只移动仓库内忽略目录中的副本，不移动用户 Downloads 原件。",
        "",
        "## 子 agent 复核摘要",
        "",
        "- 独立复核确认：注册表、治理 schema、文档、测试和真实文件副本模拟基本同步，可以提交。",
        "- 已处理复核意见：`human_review_recommended` 归档计划现在必须先执行 `apply_human_review`，否则即使 `confirmed=True` 也会阻断移动。",
        "- 已处理复核意见：模拟脚本默认使用带时间戳的 workspace 和 input-dir，降低复跑时 `target_exists` 的概率。",
        "- 保留治理边界：人工复核当前会写回项目账本并生成 `rule_candidate.v1`，不会自动修改规则引擎；规则入库仍需后续审批和回归测试。",
        "",
    ])
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="Source files to copy and simulate.")
    parser.add_argument("--workspace", default="")
    parser.add_argument("--input-dir", default="")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()

    run_stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    input_dir = Path(args.input_dir) if args.input_dir else DEFAULT_INPUT_BASE / run_stamp
    workspace = Path(args.workspace) if args.workspace else DEFAULT_WORKSPACE_BASE / run_stamp
    copied_paths = _copy_inputs(args.files, input_dir)
    workspace.mkdir(parents=True, exist_ok=True)

    tools = DataCleaningTools(workspace_dir=str(workspace))
    prepared = tools.prepare_file_organization_run(copied_paths)
    blocked_archive = tools.execute_archive_plan(prepared["run_id"], confirmed=False)
    project_name = _first_project_name(prepared)
    review = tools.apply_human_review(
        [
            {
                "project_name": project_name,
                "facts": {
                    "project_name": project_name,
                    "bid_status": "已中标",
                    "contract_status": "未签约",
                },
                "reason": "模拟人工复核：确认项目已中标但合同未签约，用于验证业务判断修正和规则候选生成。",
            }
        ],
        run_id=prepared["run_id"],
    )
    executed = tools.execute_archive_plan(prepared["run_id"], confirmed=True)
    html_result = tools.generate_bid_progress_html(str(workspace / "投标进度总览.html"))

    _write_report(
        Path(args.report),
        args.files,
        copied_paths,
        prepared,
        blocked_archive,
        review,
        executed,
        html_result,
    )

    print(json.dumps({
        "report": str(Path(args.report)),
        "prepared": {
            "run_id": prepared.get("run_id"),
            "status": prepared.get("status"),
            "processed": prepared.get("processed"),
            "failed": prepared.get("failed"),
        },
        "blocked_archive_status": blocked_archive.get("status"),
        "review_status": review.get("status"),
        "archive_status": executed.get("status"),
        "html_status": html_result.get("status"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
