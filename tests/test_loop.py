#!/usr/bin/env python3
"""
Project Manager Loop 测试套件

验证内容：
1. 模块导入（common/ 已内联）
2. 工具注册（34个工具）
3. LoopEngine 在 RuleBasedPlanner 下的完整运行
4. 各场景的典型路径验证
5. 状态管理（sliding_window）

用法：
    cd /Users/zhang/Desktop/工作文件/project_manager/project_manager
    python tests/test_loop.py
"""

import os
import sys
import json
import unittest

# 将项目根目录加入 Python 路径
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TEST_DIR)
sys.path.insert(0, PROJECT_DIR)

from common import (
    LoopEngine, LoopTrace, LoopRound,
    ToolRegistry, Tool,
    StateManager, MemoryStore,
    build_react_prompt, MockLLMAdapter, LLMResponse
)


# ────────────────────────────────────────────
# 辅助：构建 mock LLM 响应，模拟 ReAct 行为
# ────────────────────────────────────────────

def make_mock_planner_responses(tool_chain: list, final_answer: str = "任务完成") -> list:
    """
    根据工具调用链生成 mock LLM 响应序列
    
    Args:
        tool_chain: [(tool_name, action_input_dict), ...]
        final_answer: 最终回答文本
    
    Returns:
        ReAct 格式的响应字符串列表
    """
    responses = []
    for tool_name, action_input in tool_chain:
        resp = f"Thought: 需要调用 {tool_name}\nAction: {tool_name}\nAction Input: {json.dumps(action_input, ensure_ascii=False)}"
        responses.append(resp)
    responses.append(f"Final Answer: {final_answer}")
    return responses


# ────────────────────────────────────────────
# 测试 1：模块导入与实例化
# ────────────────────────────────────────────

class TestModuleImports(unittest.TestCase):
    """验证所有模块能正确导入和实例化"""
    
    def test_common_imports(self):
        """common/ 所有导出类可导入"""
        from common import (
            LoopEngine, LoopTrace, LoopRound,
            ToolRegistry, Tool,
            StateManager, MemoryStore,
            build_react_prompt
        )
        self.assertTrue(True)
    
    def test_loop_engine_init(self):
        """LoopEngine 可实例化"""
        engine = LoopEngine(
            agent_name="test",
            max_rounds=5,
            state_mode="sliding_window",
            sliding_window_size=3,
        )
        self.assertEqual(engine.max_rounds, 5)
        self.assertEqual(engine.state_mode, "sliding_window")
    
    def test_tool_registry_init(self):
        """ToolRegistry 可实例化并注册工具"""
        reg = ToolRegistry()
        reg.register("test_tool", "测试工具", lambda x: f"result: {x}", {}, [])
        self.assertIn("test_tool", reg.list_tools())
    
    def test_state_manager_init(self):
        """StateManager 可实例化"""
        sm = StateManager(mode="sliding_window", window_size=3)
        self.assertEqual(sm.mode, "sliding_window")
    
    def test_memory_store_init(self):
        """MemoryStore 可实例化"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ms = MemoryStore(base_dir=td)
            ms.save("test", {"key": "value"})
            data = ms.load("test")
            self.assertEqual(data["key"], "value")


# ────────────────────────────────────────────
# 测试 2：工具注册与 schema 生成
# ────────────────────────────────────────────

class TestToolRegistry(unittest.TestCase):
    """验证工具注册表功能"""
    
    def test_register_and_call(self):
        """注册工具并调用"""
        reg = ToolRegistry()
        
        def dummy_scan():
            return {"projects": [], "count": 0}
        
        reg.register("scan_projects", "扫描项目", dummy_scan, {}, [])
        self.assertEqual(len(reg.list_tools()), 1)
        
        result = reg["scan_projects"](**{})
        self.assertEqual(result["count"], 0)
    
    def test_to_prompt_text(self):
        """prompt 文本生成"""
        reg = ToolRegistry()
        reg.register("scan_projects", "扫描项目", lambda: {}, {}, [])
        
        text = reg.to_prompt_text()
        self.assertIn("scan_projects", text)
        self.assertIn("扫描项目", text)
    
    def test_auto_params(self):
        """自动参数推断"""
        reg = ToolRegistry()
        
        def func_with_params(a: str, b: int = 0) -> str:
            return f"{a}-{b}"
        
        reg.register("auto_tool", "自动推断", func_with_params)
        tool = reg.get("auto_tool")
        self.assertIn("a", tool.parameters)
        self.assertEqual(tool.parameters["a"]["type"], "string")
        self.assertEqual(tool.parameters["b"]["type"], "integer")
        # b 有默认值，不应在 required 中
        self.assertNotIn("b", tool.required_params)

    def test_main_registry_includes_cloudcc_domain(self):
        """主注册表包含 CloudCC/CRM 受控工具域"""
        import main

        reg = main._build_registry()
        tools = set(reg.list_tools())
        self.assertEqual(len(tools), 34)
        self.assertIn("cloudcc_session_probe", tools)
        self.assertIn("cloudcc_duplicate_check", tools)
        self.assertIn("cloudcc_fill_draft_gated", tools)
        self.assertIn("update_project_ledger", tools)
        self.assertIn("process_documents_to_ledger", tools)
        self.assertIn("prepare_file_organization_run", tools)
        self.assertIn("apply_human_review", tools)
        self.assertIn("execute_archive_plan", tools)
        self.assertIn("import_project_detail_workbook", tools)
        self.assertIn("generate_bid_progress_html", tools)

    def test_data_cleaning_skill_registry_exposes_only_its_tools(self):
        """渐进式披露：激活数据清洗及文件整理 skill 时只暴露本 skill 工具"""
        import main

        reg = main._build_registry_for_skill("data_cleaning_file_organization")
        self.assertEqual(set(reg.list_tools()), {
            "scan_raw_files",
            "extract_pdf",
            "extract_document",
            "classify_document",
            "batch_process",
            "save_structured",
            "process_documents_to_ledger",
            "prepare_file_organization_run",
            "apply_human_review",
            "execute_archive_plan",
            "import_project_detail_workbook",
            "generate_bid_progress_html",
            "update_project_ledger",
        })

    def test_route_skill_selects_data_cleaning_file_organization(self):
        """项目账本/项目总览类请求路由到数据清洗及文件整理 skill"""
        import main

        self.assertEqual(
            main._route_skill("跑通数据清洗及文件整理项目总览账本循环"),
            "data_cleaning_file_organization",
        )
        self.assertEqual(
            main._route_skill("帮我整理文件并归档 C:\\tmp\\demo.docx"),
            "data_cleaning_file_organization",
        )

    def test_rule_planner_routes_file_organization_to_prepare_run(self):
        """整理/归档类文件目标应优先准备文件整理运行包，而不是直接移动文件"""
        from planner import RuleBasedPlanner

        planner = RuleBasedPlanner("帮我整理文件并归档 C:\\tmp\\demo.docx")
        response, _ = planner.get_response([])

        self.assertIn("Action: prepare_file_organization_run", response)
        payload = json.loads(response.split("Action Input: ", 1)[1])
        self.assertEqual(payload["file_paths"], ["C:\\tmp\\demo.docx"])


class TestCloudCCCrmTools(unittest.TestCase):
    """验证 CloudCC/CRM fake adapter 的安全边界"""

    def test_session_probe_blocks_without_adapter(self):
        from tools.cloudcc_crm_tools import CloudCCCrmTools

        result = CloudCCCrmTools().cloudcc_session_probe()
        self.assertEqual(result["schema_version"], "cloudcc.crm.result.v1")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "browser_adapter_unavailable")
        self.assertFalse(result["secrets_included"])

    def test_duplicate_check_blocked_is_not_no_match(self):
        from tools.cloudcc_crm_tools import CloudCCCrmTools

        result = CloudCCCrmTools().cloudcc_duplicate_check(project_code="P-001")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["evidence"]["duplicate_conclusion"], "blocked")
        self.assertFalse(result["evidence"]["search_executed"])

    def test_fill_draft_requires_confirmation(self):
        from tools.cloudcc_crm_tools import CloudCCCrmTools

        result = CloudCCCrmTools().cloudcc_fill_draft_gated({"opportunity_name": "测试项目"})
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["pending_confirmation"]["action"], "submit_opportunity")
        self.assertFalse(result["data"]["crm_write_performed"])


class TestDataCleaningFileOrganizationLedger(unittest.TestCase):
    """Verify the data-cleaning file-organization ledger loop primitives."""

    def test_project_ledger_accepts_local_file_fact_and_writes_overview(self):
        import tempfile
        from ledger import ProjectLedger

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            result = ledger.apply_patch({
                "project_name": "测试项目",
                "source_type": "local_file",
                "facts": {"project_name": "测试项目", "customer_name": "测试客户"},
                "evidence": [{
                    "field": "customer_name",
                    "source_ref": "招标文件.pdf#page=1",
                    "extract_method": "pdf_text",
                    "confidence": 0.90,
                }],
            })

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["decisions"]["customer_name"]["status"], "verified")
            self.assertTrue(os.path.exists(result["markdown_path"]))
            with open(result["markdown_path"], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("# 项目总览：测试项目", content)
            self.assertIn("customer_name", content)
            self.assertIn("测试客户", content)

    def test_project_ledger_records_conflict_without_overwriting_higher_weight_fact(self):
        import tempfile
        from ledger import ProjectLedger

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            first = ledger.apply_patch({
                "project_name": "冲突项目",
                "source_type": "human_correction",
                "facts": {"customer_name": "正式客户名称"},
            })
            second = ledger.apply_patch({
                "project_name": "冲突项目",
                "source_type": "xlsx_summary",
                "facts": {"customer_name": "客户简称"},
            })

            self.assertEqual(first["decisions"]["customer_name"]["status"], "verified")
            self.assertEqual(second["decisions"]["customer_name"]["status"], "conflict")
            self.assertEqual(second["current_facts"]["customer_name"], "正式客户名称")
            self.assertEqual(second["conflicts"][0]["field"], "customer_name")

    def test_update_project_ledger_tool_returns_structured_artifacts(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            tools = DataCleaningTools(workspace_dir=td)
            result = tools.update_project_ledger(
                project_name="工具项目",
                facts={"project_name": "工具项目", "bid_status": "已报名"},
                evidence=[{"field": "bid_status", "source_ref": "项目总览.md#状态", "confidence": 0.80}],
                source_type="local_file",
            )

            self.assertEqual(result["schema_version"], "project_ledger.update.v1")
            self.assertEqual(result["status"], "success")
            self.assertTrue(os.path.exists(result["artifacts"]["project_overview_md"]))
            self.assertIn("bid_status", result["decisions"])

    def test_process_docx_documents_to_ledger_outputs_structured_artifacts(self):
        import tempfile
        from docx import Document
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            doc_path = os.path.join(td, "采购公告.docx")
            doc = Document()
            doc.add_paragraph("航天时代飞鸿技术有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph("打印刻录系统采购项目")
            doc.save(doc_path)

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.process_documents_to_ledger([doc_path])

            self.assertEqual(result["schema_version"], "data_cleaning.documents_to_ledger.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["project_name"], "打印刻录系统采购项目")
            self.assertTrue(os.path.exists(result["structured_outputs"][0]))
            self.assertTrue(os.path.exists(result["artifacts"]["project_overview_md"]))
            with open(result["artifacts"]["project_overview_md"], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("打印刻录系统采购项目", content)
            self.assertIn("customer_name", content)

    def test_business_rules_identify_won_pending_contract_actions(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine().evaluate({
            "project_name": "中标待签约项目",
            "bid_status": "已中标",
            "contract_status": "未签约",
            "customer_name": "测试客户",
            "sales_owner": "张三",
        })

        self.assertEqual(result["schema_version"], "bid_project.business_judgement.v1")
        self.assertEqual(result["business_stage"], "won_pending_contract")
        self.assertEqual(result["display_status"], "已中标")
        self.assertEqual(result["risk_level"], "medium")
        self.assertTrue(result["bpm_required"])
        self.assertIn("准备合同签约", result["next_actions"])
        self.assertIn("补充 BPM 合同号", result["next_actions"])

    def test_business_rules_flag_overdue_registration_and_missing_fields(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine(today="2026-07-02").evaluate({
            "project_name": "逾期待报名项目",
            "registration_status": "待报名",
            "registration_deadline": "2026-07-01",
            "bid_status": "待开标",
            "bid_bond_amount": 50000,
            "bid_bond_paid": "否",
        })

        self.assertEqual(result["business_stage"], "pending_registration")
        self.assertEqual(result["risk_level"], "high")
        self.assertTrue(result["human_review_required"])
        self.assertIn("报名截止已过但状态仍为待报名", result["risk_reasons"])
        self.assertIn("投标保证金存在但未确认支付", result["risk_reasons"])
        self.assertIn("customer_name", result["missing_fields"])
        self.assertIn("sales_owner", result["missing_fields"])
        self.assertIn("确认报名是否完成", result["next_actions"])

    def test_project_ledger_writes_business_judgement_to_state_and_markdown(self):
        import tempfile
        from ledger import ProjectLedger

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            result = ledger.apply_patch({
                "project_name": "业务判断项目",
                "source_type": "xlsx_summary",
                "facts": {
                    "project_name": "业务判断项目",
                    "bid_status": "已中标",
                    "contract_status": "未签约",
                    "customer_name": "测试客户",
                    "sales_owner": "张三",
                },
                "evidence": [{"field": "bid_status", "source_ref": "项目明细表.xlsx", "confidence": 0.9}],
            })

            self.assertIn("business_judgement", result)
            self.assertEqual(result["business_judgement"]["business_stage"], "won_pending_contract")
            self.assertIn("准备合同签约", result["business_judgement"]["next_actions"])

            with open(result["state_path"], "r", encoding="utf-8") as f:
                state = json.load(f)
            self.assertEqual(state["business_judgement"]["display_status"], "已中标")

            with open(result["markdown_path"], "r", encoding="utf-8") as f:
                md = f.read()
            self.assertIn("## 6. 业务判断", md)
            self.assertIn("准备合同签约", md)

    def test_import_project_detail_workbook_updates_multiple_ledgers(self):
        import tempfile
        from openpyxl import Workbook
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            xlsx_path = os.path.join(td, "项目明细表.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "项目明细表"
            ws.append([
                "序号", "项目名称", "项目编号", "招标人/客户", "负责销售", "报名截止", "开标时间",
                "投标保证金", "保证金已支付", "项目类型", "报名状态", "中标状态", "签约状态",
                "备注", "立项金额", "招标编号",
            ])
            ws.append([
                1, "工业互联网网络基础条件项目", "C000027902", "首都航天机械有限公司", "邹迅",
                "2026-05-12", "2026-05-12", 50000, "是", "产品", "已报名", "已中标", "未签约",
                "中标通知书已归档", 3438800, "BID-001",
            ])
            ws.append([
                2, "台式电脑采购", "C000028118", "XXZYBDCGFW", "陈丞",
                "2026-05-13", "2026-05-22", None, "否", "产品", "已报名", "已弃标", "未签约",
                "已弃标", None, "BID-002",
            ])
            exec_ws = wb.create_sheet("项目执行")
            exec_ws.append([
                "序号", "项目名称", "项目编号", "客户", "负责销售", "合同金额", "合同编号",
                "签订日期", "签约状态", "里程碑节点", "预计完成", "实际完成", "备注",
            ])
            exec_ws.append([
                1, "工业互联网网络基础条件项目", "C000027902", "首都航天机械有限公司", "邹迅",
                3438800, "HT-001", "2026-05-29", "已签合同", "合同签订", "2026-05", "2026-05",
                "合同已归档",
            ])
            wb.save(xlsx_path)

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.import_project_detail_workbook(xlsx_path)

            self.assertEqual(result["schema_version"], "project_detail_workbook.import.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["processed_projects"], 2)
            self.assertEqual(result["execution_rows"], 1)
            self.assertEqual(result["projects"][0]["facts"]["project_code"], "C000027902")
            self.assertEqual(result["projects"][0]["facts"]["bid_status"], "已中标")
            self.assertEqual(result["projects"][0]["facts"]["lifecycle_stage"], "execution")
            self.assertEqual(result["projects"][0]["facts"]["contract_code"], "HT-001")
            self.assertEqual(result["projects"][1]["facts"]["lifecycle_stage"], "closed_lost")
            self.assertTrue(os.path.exists(result["projects"][0]["artifacts"]["project_overview_md"]))

    def test_import_project_detail_workbook_skips_placeholder_values(self):
        import tempfile
        from openpyxl import Workbook
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            xlsx_path = os.path.join(td, "项目明细表.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "项目明细表"
            ws.append(["序号", "项目名称", "项目编号", "招标人/客户", "负责销售", "项目类型", "报名状态", "中标状态", "签约状态"])
            ws.append([1, "占位字段项目", "待录入", "待确认", "待确认", "服务", "待报名", "待开标", "未签约"])
            exec_ws = wb.create_sheet("项目执行")
            exec_ws.append(["序号", "项目名称", "合同编号", "签订日期"])
            exec_ws.append([1, "占位字段项目", "（待补充）", "（待补充）"])
            wb.save(xlsx_path)

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.import_project_detail_workbook(xlsx_path)
            facts = result["projects"][0]["facts"]

            self.assertNotIn("project_code", facts)
            self.assertNotIn("customer_name", facts)
            self.assertNotIn("sales_owner", facts)
            self.assertNotIn("contract_code", facts)
            self.assertNotIn("contract_signed_date", facts)

    def test_generate_bid_progress_html_from_project_ledgers(self):
        import tempfile
        from openpyxl import Workbook
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            xlsx_path = os.path.join(td, "项目明细表.xlsx")
            wb = Workbook()
            ws = wb.active
            ws.title = "项目明细表"
            ws.append([
                "序号", "项目名称", "项目编号", "招标人/客户", "负责销售", "报名截止", "开标时间",
                "投标保证金", "保证金已支付", "项目类型", "报名状态", "中标状态", "签约状态",
                "备注", "立项金额", "招标编号",
            ])
            ws.append([
                1, "工业互联网网络基础条件项目", "C000027902", "首都航天机械有限公司", "邹迅",
                "2026-05-12", "2026-05-12", 50000, "是", "产品", "已报名", "已中标", "已签合同",
                "中标通知书已归档", 3438800, "BID-001",
            ])
            ws.append([
                2, "服务器区防火墙系统升级采购", "C000028121", "北京外企数字科技有限责任公司", "邹迅",
                "", "", "", "否", "产品", "待报名", "待开标", "未签约", "", "", "",
            ])
            wb.save(xlsx_path)

            tools = DataCleaningTools(workspace_dir=td)
            tools.import_project_detail_workbook(xlsx_path)
            result = tools.generate_bid_progress_html()

            self.assertEqual(result["schema_version"], "bid_progress_html.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["total_projects"], 2)
            self.assertEqual(result["counts"]["已中标"], 1)
            self.assertEqual(result["counts"]["参与中"], 1)
            self.assertTrue(os.path.exists(result["output_file"]))
            with open(result["output_file"], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("投标进度总览", content)
            self.assertIn("BPM销售合同号/非订单编号", content)
            self.assertIn("工业互联网网络基础条件项目", content)
            self.assertIn("服务器区防火墙系统升级采购", content)
            self.assertIn("项目详情", content)
            self.assertIn("里程碑进度", content)
            self.assertIn("任务跟踪", content)
            self.assertIn("风险与问题", content)
            self.assertIn("最新进展", content)

    def test_prepare_file_organization_run_outputs_partial_package_and_archive_plan(self):
        import tempfile
        from docx import Document
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            doc_path = os.path.join(td, "采购公告.docx")
            doc = Document()
            doc.add_paragraph("航天时代飞鸿技术有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph("打印刻录系统采购项目")
            doc.save(doc_path)
            missing_path = os.path.join(td, "不存在.pdf")

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.prepare_file_organization_run([doc_path, missing_path])

            self.assertEqual(result["schema_version"], "file_organization.run.v1")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertTrue(os.path.exists(result["artifacts"]["input_manifest"]))
            self.assertTrue(os.path.exists(result["artifacts"]["review_queue"]))
            self.assertTrue(os.path.exists(result["artifacts"]["planned_archive_actions"]))
            self.assertTrue(os.path.exists(result["artifacts"]["run_report"]))
            self.assertEqual(len(result["archive_actions"]), 1)
            self.assertIn(result["archive_actions"][0]["status"], {"ready", "needs_review"})
            self.assertTrue(os.path.exists(result["structured_outputs"][0]))
            self.assertTrue(os.path.exists(doc_path), "prepare step must not move source files")

    def test_apply_human_review_corrects_ledger_business_judgement(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            tools = DataCleaningTools(workspace_dir=td)
            tools.update_project_ledger(
                project_name="人工复核项目",
                facts={
                    "project_name": "人工复核项目",
                    "bid_status": "待开标",
                    "contract_status": "未签约",
                    "customer_name": "测试客户",
                    "sales_owner": "张三",
                },
                evidence=[],
                source_type="xlsx_summary",
            )

            result = tools.apply_human_review([
                {
                    "project_name": "人工复核项目",
                    "facts": {"bid_status": "已中标", "contract_status": "未签约"},
                    "reason": "人工确认已中标，Excel 状态滞后",
                }
            ])

            self.assertEqual(result["schema_version"], "human_review.apply.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["reviewed"], 1)
            judgement = result["results"][0]["business_judgement"]
            self.assertEqual(judgement["business_stage"], "won_pending_contract")
            self.assertIn("准备合同签约", judgement["next_actions"])
            self.assertTrue(result["rule_candidates"])

    def test_execute_archive_plan_requires_confirmation_then_moves_and_updates_ledger(self):
        import tempfile
        from docx import Document
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_dir = os.path.join(td, "source")
            os.makedirs(source_dir)
            doc_path = os.path.join(source_dir, "采购公告.docx")
            doc = Document()
            doc.add_paragraph("航天时代飞鸿技术有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph("打印刻录系统采购项目")
            doc.save(doc_path)

            tools = DataCleaningTools(workspace_dir=td)
            prepared = tools.prepare_file_organization_run([doc_path])
            blocked = tools.execute_archive_plan(prepared["run_id"], confirmed=False)

            self.assertEqual(blocked["status"], "needs_confirmation")
            self.assertTrue(os.path.exists(doc_path))

            review_blocked = tools.execute_archive_plan(prepared["run_id"], confirmed=True)

            self.assertEqual(review_blocked["status"], "failed")
            self.assertEqual(review_blocked["results"][0]["status"], "blocked")
            self.assertIn("human_review_required", review_blocked["results"][0]["blockers"])
            self.assertTrue(os.path.exists(doc_path))

            tools.apply_human_review([
                {
                    "project_name": "打印刻录系统采购项目",
                    "facts": {"bid_status": "待开标"},
                    "reason": "测试确认归档计划可以执行",
                }
            ], run_id=prepared["run_id"])
            executed = tools.execute_archive_plan(prepared["run_id"], confirmed=True)

            self.assertEqual(executed["schema_version"], "archive_plan.execute.v1")
            self.assertEqual(executed["status"], "success")
            self.assertEqual(executed["moved"], 1)
            self.assertFalse(os.path.exists(doc_path))
            archived_path = executed["results"][0]["archived_path"]
            self.assertTrue(os.path.exists(archived_path))
            self.assertTrue(os.path.exists(executed["artifacts"]["archive_result"]))
            self.assertTrue(os.path.exists(executed["artifacts"]["run_report"]))


# ────────────────────────────────────────────
# 测试 3：LoopEngine 核心循环
# ────────────────────────────────────────────

class TestLoopEngine(unittest.TestCase):
    """验证 LoopEngine 的完整循环逻辑"""
    
    def setUp(self):
        self.tools = {
            "scan_projects": lambda: {"projects": [{"name": "测试项目"}], "count": 1},
            "check_milestones": lambda: {"overdue": [], "upcoming": [], "summary": {"total_checked": 1}},
            "write_response": lambda content, filename: f"Saved: {filename}",
        }
    
    def test_simple_loop_completion(self):
        """简单场景：Planner 直接返回 Final Answer，循环立即完成"""
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Final Answer: 测试完成"
        ])
        
        engine = LoopEngine(agent_name="test", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="测试",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        self.assertEqual(trace.status, "completed")
        self.assertEqual(len(trace.rounds), 1)
        self.assertEqual(trace.final_result, "Final Answer: 测试完成")
    
    def test_two_round_loop(self):
        """两轮场景：先调用工具，再返回结论"""
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Thought: 先扫描项目\nAction: scan_projects\nAction Input: {}",
            "Final Answer: 扫描完成，发现 1 个项目"
        ])
        
        engine = LoopEngine(agent_name="test", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="扫描项目",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        self.assertEqual(trace.status, "completed")
        self.assertEqual(len(trace.rounds), 2)
        # 第一轮调用了 scan_projects
        self.assertEqual(trace.rounds[0].action, "scan_projects")
        self.assertEqual(trace.rounds[0].status, "success")
        # 第二轮是 Final Answer
        self.assertEqual(trace.rounds[1].action, "final_answer")
    
    def test_tool_not_found(self):
        """工具不存在时的错误处理"""
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Thought: 调用不存在的工具\nAction: nonexistent_tool\nAction Input: {}",
            "Final Answer: 工具不存在，结束"
        ])
        
        engine = LoopEngine(agent_name="test", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="测试",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        # 第一轮工具调用失败，但循环继续
        self.assertEqual(trace.rounds[0].status, "failed")
        self.assertIn("not found", trace.rounds[0].observation.lower())
        self.assertEqual(trace.status, "completed")
    
    def test_max_rounds_timeout(self):
        """达到最大轮数时超时"""
        mock_llm = MockLLMAdapter()
        # 始终返回工具调用，不返回 Final Answer
        mock_llm.set_mock_responses([
            "Thought: 继续扫描\nAction: scan_projects\nAction Input: {}"
        ] * 10)
        
        engine = LoopEngine(agent_name="test", max_rounds=3)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="扫描项目",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        self.assertEqual(trace.status, "timeout")
        self.assertEqual(len(trace.rounds), 3)
        self.assertIn("MAX ROUNDS", trace.final_result)
    
    def test_dedup_detection(self):
        """重复调用检测"""
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Thought: 第一次扫描\nAction: scan_projects\nAction Input: {}",
            "Thought: 第二次扫描（重复）\nAction: scan_projects\nAction Input: {}",
            "Thought: 第三次扫描（重复）\nAction: scan_projects\nAction Input: {}",
            "Final Answer: 检测到重复调用"
        ])
        
        engine = LoopEngine(agent_name="test", max_rounds=10, dedup_threshold=2)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="扫描项目",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        # 第一轮成功，第二轮成功，第三轮被拦截（blocked）
        self.assertEqual(trace.rounds[0].status, "success")
        self.assertEqual(trace.rounds[1].status, "success")
        self.assertEqual(trace.rounds[2].status, "blocked")
        self.assertIn("DEDUP", trace.rounds[2].observation)
    
    def test_trace_save_and_load(self):
        """Trace 保存和加载"""
        import tempfile
        
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses(["Final Answer: 测试"])
        
        engine = LoopEngine(agent_name="test", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="测试",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            path = f.name
        
        try:
            trace.save(path)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["status"], "completed")
            self.assertEqual(data["goal"], "测试")
        finally:
            os.unlink(path)


# ────────────────────────────────────────────
# 测试 4：状态管理
# ────────────────────────────────────────────

class TestStateManager(unittest.TestCase):
    """验证状态管理策略"""
    
    def test_full_mode(self):
        """全量模式不裁剪"""
        sm = StateManager(mode="full")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "goal"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "o1"},
        ]
        result = sm.trim(msgs)
        self.assertEqual(len(result), 4)
    
    def test_sliding_window(self):
        """滑动窗口模式裁剪中间历史"""
        sm = StateManager(mode="sliding_window", window_size=2)
        # 构造 11 条消息（2 头 + 3*3 尾 = 11 刚好不裁剪）
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "goal"},
        ]
        for i in range(9):
            msgs.append({"role": "assistant" if i % 2 == 0 else "user", "content": f"msg{i}"})
        
        result = sm.trim(msgs)
        # 2 + 2*3 = 8（因为 > 11 才裁剪）
        # 等等，2 + 2*3 = 8，但 msgs 有 11 条，11 > 8 所以应该裁剪
        # 保留 head=2 + tail=6 = 8
        self.assertEqual(len(result), 8)
        self.assertEqual(result[0]["content"], "sys")
        self.assertEqual(result[1]["content"], "goal")
    
    def test_summary_mode(self):
        """摘要模式"""
        sm = StateManager(mode="summary")
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "goal"}]
        for i in range(12):
            msgs.append({"role": "assistant" if i % 2 == 0 else "user", "content": f"msg{i}"})
        
        result = sm.trim(msgs)
        # 保留 head=2 + tail=6 = 8
        self.assertEqual(len(result), 8)


# ────────────────────────────────────────────
# 测试 5：ReAct Prompt 构建
# ────────────────────────────────────────────

class TestReActPrompt(unittest.TestCase):
    """验证 ReAct prompt 构建"""
    
    def test_build_prompt(self):
        """prompt 包含必要元素"""
        prompt = build_react_prompt(
            goal="测试目标",
            tools_text="工具列表",
            memory_text="记忆内容"
        )
        self.assertIn("测试目标", prompt)
        self.assertIn("工具列表", prompt)
        self.assertIn("记忆内容", prompt)
        self.assertIn("Thought:", prompt)
        self.assertIn("Action:", prompt)
        self.assertIn("Final Answer:", prompt)


# ────────────────────────────────────────────
# 测试 6：端到端集成（模拟主流程）
# ────────────────────────────────────────────

class TestEndToEnd(unittest.TestCase):
    """模拟完整的 agent 运行流程"""
    
    def test_e2e_risk_check(self):
        """模拟风险检查场景"""
        # 构造 mock 工具
        tools = {
            "scan_projects": lambda: {"projects": [{"name": "A项目"}, {"name": "B项目"}], "count": 2},
            "check_milestones": lambda: {"overdue": [{"project": "A项目", "days_overdue": 3}], "upcoming": [], "summary": {"overdue_count": 1}},
            "write_response": lambda content, filename: f"Saved: {filename}",
        }
        
        # 构造 mock planner 响应链
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Thought: 先扫描项目\nAction: scan_projects\nAction Input: {}",
            "Thought: 检查里程碑\nAction: check_milestones\nAction Input: {}",
            "Thought: 写入报告\nAction: write_response\nAction Input: {\"content\": \"风险报告\", \"filename\": \"risk.md\"}",
            "Final Answer: 风险检查完成，发现 1 个逾期项目"
        ])
        
        engine = LoopEngine(agent_name="risk_check", max_rounds=10)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="今天有什么风险项目",
            system_prompt="你是项目经理 Agent",
            llm_call=llm_call,
            tools=tools,
        )
        
        self.assertEqual(trace.status, "completed")
        self.assertEqual(len(trace.rounds), 4)
        self.assertEqual(trace.rounds[0].action, "scan_projects")
        self.assertEqual(trace.rounds[1].action, "check_milestones")
        self.assertEqual(trace.rounds[2].action, "write_response")
        self.assertEqual(trace.rounds[3].action, "final_answer")
    
    def test_e2e_bid_overview(self):
        """模拟投标进度总览场景"""
        tools = {
            "generate_bid_overview": lambda: "投标进度总览已生成",
            "write_response": lambda content, filename: f"Saved: {filename}",
        }
        
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Thought: 生成投标总览\nAction: generate_bid_overview\nAction Input: {}",
            "Final Answer: 投标进度总览生成完成"
        ])
        
        engine = LoopEngine(agent_name="bid_overview", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="生成投标进度总览",
            system_prompt="你是项目经理 Agent",
            llm_call=llm_call,
            tools=tools,
        )
        
        self.assertEqual(trace.status, "completed")
        self.assertEqual(len(trace.rounds), 2)
        self.assertEqual(trace.rounds[0].action, "generate_bid_overview")


# ────────────────────────────────────────────
# 测试 7：错误恢复
# ────────────────────────────────────────────

class TestErrorRecovery(unittest.TestCase):
    """验证错误恢复机制"""
    
    def test_retry_and_fail(self):
        """工具多次失败后返回结构化错误"""
        call_count = 0
        
        def failing_tool():
            nonlocal call_count
            call_count += 1
            raise ValueError(f"故意失败 #{call_count}")
        
        tools = {"fail_tool": failing_tool}
        
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Thought: 调用失败工具\nAction: fail_tool\nAction Input: {}",
            "Final Answer: 工具失败，结束"
        ])
        
        engine = LoopEngine(agent_name="test", max_rounds=5, retry_max=2)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="测试",
            system_prompt="你是测试 Agent",
            llm_call=llm_call,
            tools=tools,
        )
        
        # retry_max=2，所以调用 1 次 + 重试 2 次 = 3 次
        self.assertEqual(call_count, 3)
        self.assertEqual(trace.rounds[0].status, "failed")
        self.assertIn("TOOL FAILED", trace.rounds[0].observation)


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 Project Manager Loop 测试套件")
    print("=" * 60)
    print()
    
    # 运行所有测试
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestModuleImports))
    suite.addTests(loader.loadTestsFromTestCase(TestToolRegistry))
    suite.addTests(loader.loadTestsFromTestCase(TestCloudCCCrmTools))
    suite.addTests(loader.loadTestsFromTestCase(TestDataCleaningFileOrganizationLedger))
    suite.addTests(loader.loadTestsFromTestCase(TestLoopEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestStateManager))
    suite.addTests(loader.loadTestsFromTestCase(TestReActPrompt))
    suite.addTests(loader.loadTestsFromTestCase(TestEndToEnd))
    suite.addTests(loader.loadTestsFromTestCase(TestErrorRecovery))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print()
    print("=" * 60)
    if result.wasSuccessful():
        print("✅ 所有测试通过！")
    else:
        print(f"❌ 测试失败：{len(result.failures)} 失败，{len(result.errors)} 错误")
    print("=" * 60)
