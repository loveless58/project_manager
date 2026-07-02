import json
import re
from typing import List, Optional

from .base import PolicyDecision


class DataCleaningFileOrganizationPolicy:
    name = "data_cleaning_file_organization"

    def plan(
        self,
        goal: str,
        executed: List[str],
        last_observation: Optional[str],
    ) -> Optional[PolicyDecision]:
        paths = self._extract_file_paths(goal)
        workbook_paths = [path for path in paths if path.lower().endswith((".xlsx", ".xls"))]
        wants_html = any(k in goal.lower() for k in ["html", "展示", "总览", "投标进度"])
        wants_file_organization = any(k in goal for k in ["整理文件", "文件整理", "归档", "重命名", "人工复核", "复核"])

        if paths and wants_file_organization and "prepare_file_organization_run" not in executed:
            payload = {
                "file_paths": paths,
                "project_name": "",
            }
            return PolicyDecision(
                "Thought: 用户要求整理和归档真实文件，先准备文件整理运行包：清洗结构化、业务判断、人工复核队列和归档计划；此步不移动源文件。\n"
                "Action: prepare_file_organization_run\n"
                f"Action Input: {json.dumps(payload, ensure_ascii=False)}"
            )

        if "prepare_file_organization_run" in executed and "execute_archive_plan" not in executed:
            return PolicyDecision(
                "Final Answer: 文件整理运行包已生成，包含结构化提取结果、项目总览账本、人工复核队列、归档计划和运行报告。"
                " 为避免误移动原文件，归档执行需要人工确认后调用 execute_archive_plan(run_id, confirmed=true)。"
            )

        if workbook_paths and "import_project_detail_workbook" not in executed:
            payload = {"file_path": workbook_paths[0]}
            return PolicyDecision(
                "Thought: 用户提供了项目明细表类 Excel，先读取工作簿、标准化项目字段，并写入多个项目总览账本。\n"
                "Action: import_project_detail_workbook\n"
                f"Action Input: {json.dumps(payload, ensure_ascii=False)}"
            )

        if (wants_html or "import_project_detail_workbook" in executed) and "generate_bid_progress_html" not in executed:
            return PolicyDecision(
                "Thought: 项目账本已经具备汇总事实，继续从 project_ledgers 派生投标进度总览 HTML。\n"
                "Action: generate_bid_progress_html\n"
                "Action Input: {}"
            )

        if "generate_bid_progress_html" in executed:
            return PolicyDecision("Final Answer: 项目明细表 Excel 已导入项目账本，并已从项目账本生成投标进度总览 HTML。")

        if paths and "process_documents_to_ledger" not in executed:
            payload = {
                "file_paths": paths,
                "project_name": "",
            }
            return PolicyDecision(
                "Thought: 用户提供了真实源文件，先通过数据清洗及文件整理 skill 读取文件、输出结构化结果并写入项目总览账本。\n"
                "Action: process_documents_to_ledger\n"
                f"Action Input: {json.dumps(payload, ensure_ascii=False)}"
            )

        if "process_documents_to_ledger" in executed:
            return PolicyDecision(
                "Final Answer: 真实文件的数据清洗及文件整理 loop 已完成；请以工具返回的 structured_outputs、project_overview_md 和 project_ledger_json 作为结构化产物入口。"
            )

        if "update_project_ledger" not in executed:
            return PolicyDecision(
                "Thought: 数据清洗及文件整理的第一步是把清洗后的候选事实写入项目总览账本，先跑最小 ledger 循环。\n"
                "Action: update_project_ledger\n"
                "Action Input: {\"project_name\": \"示例项目\", \"facts\": {\"project_name\": \"示例项目\", \"bid_status\": \"待补充\"}, "
                "\"evidence\": [{\"field\": \"bid_status\", \"source_ref\": \"manual_goal\", \"confidence\": 0.6}], \"source_type\": \"local_file\"}"
            )

        return PolicyDecision(
            "Final Answer: 数据清洗及文件整理的项目总览账本循环已跑通；请以工具返回的 project_overview_md 和 project_ledger_json 为结构化产物入口。"
        )

    def _extract_file_paths(self, goal: str) -> List[str]:
        pattern = r'(?:[A-Za-z]:\\[^"\'\s]+|[\/~][^"\'\s]+)\.(?:docx|pdf|xlsx|xls|md|html|png|jpg|jpeg)'
        return re.findall(pattern, goal, flags=re.IGNORECASE)
