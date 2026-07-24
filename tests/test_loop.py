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
    cd /path/to/project_manager
    python tests/test_loop.py
"""
# repo-hygiene: data=synthetic
# 本文件中的项目、客户、人员、编号、金额、票号与文件名均为不可关联的合成数据。

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
        self.assertEqual(len(tools), 43)
        self.assertIn("cloudcc_session_probe", tools)
        self.assertIn("cloudcc_duplicate_check", tools)
        self.assertIn("cloudcc_fill_draft_gated", tools)
        self.assertIn("update_project_ledger", tools)
        self.assertIn("process_documents_to_ledger", tools)
        self.assertIn("extract_structured_business_output", tools)
        self.assertIn("semantic_structure_document", tools)
        self.assertIn("prepare_file_organization_run", tools)
        self.assertIn("verify_file_organization_run", tools)
        self.assertIn("audit_file_organization_run", tools)
        self.assertIn("prepare_feedback_form", tools)
        self.assertIn("apply_feedback_form", tools)
        self.assertIn("apply_feedback_decisions", tools)
        self.assertIn("generate_candidate_tests", tools)
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
            "run_ocr",
            "classify_document",
            "batch_process",
            "save_structured",
            "process_documents_to_ledger",
            "extract_structured_business_output",
            "semantic_structure_document",
            "prepare_file_organization_run",
            "verify_file_organization_run",
            "audit_file_organization_run",
            "prepare_feedback_form",
            "apply_feedback_form",
            "apply_feedback_decisions",
            "generate_candidate_tests",
            "apply_human_review",
            "execute_archive_plan",
            "import_project_detail_workbook",
            "generate_bid_progress_html",
            "update_project_ledger",
        })

    def test_each_skill_registry_exposes_only_declared_tools(self):
        """每个 active skill 的运行时注册表都必须严格等于声明的工具集合"""
        import main

        for skill_name, expected_tools in main.SKILL_TOOL_MAP.items():
            with self.subTest(skill_name=skill_name):
                reg = main._build_registry_for_skill(skill_name)
                self.assertEqual(reg.list_tools(), expected_tools)

    def test_each_skill_has_level_1_contract_file(self):
        """Level 1 skill docs describe boundaries, not a second full tool registry."""
        from pathlib import Path
        import main

        skills_dir = Path(PROJECT_DIR) / "skills"
        for skill_name, expected_tools in main.SKILL_TOOL_MAP.items():
            with self.subTest(skill_name=skill_name):
                contract_path = skills_dir / f"{skill_name}.md"
                self.assertTrue(contract_path.exists(), f"missing skill contract: {contract_path}")
                content = contract_path.read_text(encoding="utf-8")
                self.assertIn(f"name: {skill_name}", content)
                self.assertIn("## 定位", content)
                self.assertIn("## 硬规则", content)
                self.assertIn("ToolRegistry", content)
                listed_tools = [tool_name for tool_name in expected_tools if f"`{tool_name}`" in content]
                self.assertLess(
                    len(listed_tools),
                    len(expected_tools),
                    f"{skill_name} doc should not duplicate the full active tool registry",
                )

    def test_skill_index_marks_all_runtime_skills_as_active_contracts(self):
        """Skill index 不能把已经可路由和可注册的运行时 skill 标为 planned"""
        from pathlib import Path
        import main

        content = (Path(PROJECT_DIR) / "skills" / "project_manager.md").read_text(encoding="utf-8")
        for skill_name in main.SKILL_TOOL_MAP:
            with self.subTest(skill_name=skill_name):
                self.assertIn(f"`{skill_name}`", content)
        self.assertNotIn("planned | existing tools, contract not split yet", content)

    def test_route_skill_selects_all_runtime_domains(self):
        """Level 0 路由应覆盖所有运行时 skill 域"""
        import main

        cases = {
            "今天有哪些项目风险": "project_management",
            "解析招标公告并生成商机上下文": "opportunity_management",
            "CloudCC 商机查重": "cloudcc_crm",
            "整理文件并更新项目账本": "data_cleaning_file_organization",
        }
        for goal, expected_skill in cases.items():
            with self.subTest(goal=goal):
                self.assertEqual(main._route_skill(goal), expected_skill)

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

    def test_rule_planner_delegates_file_organization_to_skill_policy(self):
        """文件整理 fallback 流程应由 skill policy 承担，避免 planner 继续膨胀。"""
        from planner import RuleBasedPlanner
        from skill_policies import get_policy

        self.assertIsNotNone(get_policy("data_cleaning_file_organization"))
        self.assertNotIn("_plan_project_ledger", type(RuleBasedPlanner("x")).__dict__)

        planner = RuleBasedPlanner("帮我整理文件并归档 C:\\tmp\\demo.docx")
        response, _ = planner.get_response([])

        self.assertIn("Action: prepare_file_organization_run", response)

    def test_rule_planner_delegates_cloudcc_to_skill_policy(self):
        """CloudCC fallback 流程应由 skill policy 承担，planner 只负责委托。"""
        from planner import RuleBasedPlanner
        from skill_policies import get_policy

        self.assertIsNotNone(get_policy("cloudcc_crm"))
        self.assertNotIn("_plan_cloudcc_crm", type(RuleBasedPlanner("x")).__dict__)

        planner = RuleBasedPlanner("CloudCC CRM 草稿填写")
        response, _ = planner.get_response([])

        self.assertIn("Action: cloudcc_session_probe", response)

    def test_rule_planner_uses_observation_evaluation_control_signal(self):
        """规则 planner 应读取 Observation Evaluation 控制信号，而不只靠原始错误文本"""
        from planner import RuleBasedPlanner

        planner = RuleBasedPlanner("解析扫描图片")
        response, _ = planner.get_response([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "解析扫描图片"},
            {"role": "assistant", "content": "Thought: OCR\nAction: extract_document\nAction Input: {\"file_path\": \"scan.png\"}"},
            {"role": "user", "content": "Observation: {'status': 'blocked'}"},
            {
                "role": "user",
                "content": "Observation Evaluation: {\"status\": \"blocked\", \"error_code\": \"ocr_adapter_unavailable\", \"retryable\": false, \"needs_confirmation\": false}",
            },
        ])

        self.assertIn("Final Answer:", response)
        self.assertIn("ocr_adapter_unavailable", response)

    def test_rule_planner_stops_on_needs_confirmation_evaluation(self):
        """规则 planner 遇到 needs_confirmation 应停在人工确认，不继续执行外部写入"""
        from planner import RuleBasedPlanner

        planner = RuleBasedPlanner("CloudCC CRM 草稿填写")
        response, _ = planner.get_response([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "CloudCC CRM 草稿填写"},
            {"role": "assistant", "content": "Thought: 填写\nAction: cloudcc_fill_draft_gated\nAction Input: {\"draft\": {}}"},
            {"role": "user", "content": "Observation: {'status': 'needs_confirmation'}"},
            {
                "role": "user",
                "content": "Observation Evaluation: {\"status\": \"needs_confirmation\", \"error_code\": \"\", \"retryable\": false, \"needs_confirmation\": true}",
            },
        ])

        self.assertIn("Final Answer:", response)
        self.assertIn("人工确认", response)


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

        result = CloudCCCrmTools().cloudcc_fill_draft_gated({"opportunity_name": '合成项目001'})
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
                "project_name": '合成项目001',
                "source_type": "local_file",
                "facts": {"project_name": '合成项目001', "customer_name": '合成客户001'},
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
            self.assertIn(os.path.join('合成项目001', "数字资产", "项目总览.md"), result["markdown_path"])
            self.assertIn(os.path.join('合成项目001', "数字资产", "project_ledger.json"), result["state_path"])
            with open(result["markdown_path"], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn('# 项目总览：合成项目001', content)
            self.assertIn("customer_name", content)
            self.assertIn('合成客户001', content)

    def test_project_ledger_records_conflict_without_overwriting_higher_weight_fact(self):
        import tempfile
        from ledger import ProjectLedger

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            first = ledger.apply_patch({
                "project_name": '合成项目002',
                "source_type": "human_correction",
                "facts": {"customer_name": '合成客户002'},
            })
            second = ledger.apply_patch({
                "project_name": '合成项目002',
                "source_type": "xlsx_summary",
                "facts": {"customer_name": '合成客户003'},
            })

            self.assertEqual(first["decisions"]["customer_name"]["status"], "verified")
            self.assertEqual(second["decisions"]["customer_name"]["status"], "conflict")
            self.assertEqual(second["current_facts"]["customer_name"], '合成客户002')
            self.assertEqual(second["conflicts"][0]["field"], "customer_name")

    def test_project_ledger_does_not_auto_merge_similar_project_names(self):
        import re
        import tempfile
        from difflib import SequenceMatcher
        from pathlib import Path
        from ledger import ProjectLedger

        first_name = "合成项目Alpha安全平台升级"
        second_name = "合成项目 Alpha安全平台升级 V2"
        normalized_first = re.sub(r"\s+", "", first_name).casefold()
        normalized_second = re.sub(r"\s+", "", second_name).casefold()
        self.assertGreater(
            SequenceMatcher(None, normalized_first, normalized_second).ratio(),
            0.90,
        )

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            first = ledger.apply_patch({
                "project_name": first_name,
                "facts": {"project_name": first_name, "bid_status": "已弃标"},
                "evidence": [{"field": "project_name", "source_ref": "lost", "confidence": 0.9}],
                "source_type": "unit_test",
            })
            second = ledger.apply_patch({
                "project_name": second_name,
                "facts": {"project_name": second_name, "lifecycle_stage": "execution"},
                "evidence": [{"field": "project_name", "source_ref": "execution", "confidence": 0.9}],
                "source_type": "unit_test",
            })

            self.assertNotEqual(Path(first["project_dir"]), Path(second["project_dir"]))
            self.assertTrue((Path(first["project_dir"]) / "数字资产" / "project_ledger.json").exists())
            self.assertTrue((Path(second["project_dir"]) / "数字资产" / "project_ledger.json").exists())

    def test_update_project_ledger_tool_returns_structured_artifacts(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            tools = DataCleaningTools(workspace_dir=td)
            result = tools.update_project_ledger(
                project_name='合成项目005',
                facts={"project_name": '合成项目005', "bid_status": "已报名"},
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
            doc.add_paragraph("合成机构022有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph('合成项目006')
            doc.save(doc_path)

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.process_documents_to_ledger([doc_path])

            self.assertEqual(result["schema_version"], "data_cleaning.documents_to_ledger.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["project_name"], '合成项目006')
            self.assertTrue(os.path.exists(result["structured_outputs"][0]))
            self.assertTrue(os.path.exists(result["artifacts"]["project_overview_md"]))
            with open(result["artifacts"]["project_overview_md"], "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn('合成项目006', content)
            self.assertIn("customer_name", content)

    def test_business_rules_identify_won_pending_contract_actions(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine().evaluate({
            "project_name": '合成项目007',
            "bid_status": "已中标",
            "contract_status": "未签约",
            "customer_name": '合成客户001',
            "sales_owner": '虚构甲',
        })

        self.assertEqual(result["schema_version"], "bid_project.business_judgement.v1")
        self.assertEqual(result["business_stage"], "won_pending_contract")
        self.assertEqual(result["display_status"], "已中标")
        self.assertEqual(result["risk_level"], "medium")
        self.assertTrue(result["bpm_required"])
        self.assertIn("准备合同签约", result["next_actions"])
        self.assertIn("补充 BPM 合同号", result["next_actions"])

    def test_business_rules_identify_execution_from_lifecycle_stage(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine().evaluate({
            "project_name": '合成项目008',
            "customer_name": '合成客户001',
            "sales_owner": '虚构乙',
            "lifecycle_stage": "execution",
        })

        self.assertEqual(result["business_stage"], "execution")
        self.assertEqual(result["display_status"], "已中标")
        self.assertIn("跟踪执行里程碑", result["next_actions"])

    def test_business_rules_distinguish_abandoned_from_lost_projects(self):
        from business_rules import BidProjectRuleEngine

        engine = BidProjectRuleEngine()

        abandoned = engine.evaluate({"bid_status": "已弃标"})
        lost = engine.evaluate({"bid_status": "已丢标"})

        self.assertEqual(abandoned["business_stage"], "closed")
        self.assertEqual(abandoned["display_status"], "已弃标")
        self.assertFalse(abandoned["crm_required"])
        self.assertEqual(lost["business_stage"], "closed")
        self.assertEqual(lost["display_status"], "已丢标")
        self.assertFalse(lost["crm_required"])

    def test_business_rules_flag_closed_project_with_pending_registration_as_conflict(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine().evaluate({
            "project_name": '合成项目009',
            "registration_status": "待报名",
            "bid_status": "已弃标",
            "lifecycle_stage": "closed",
            "closed_reason_type": "abandoned_by_us",
        })

        self.assertEqual(result["business_stage"], "closed")
        self.assertEqual(result["risk_level"], "high")
        self.assertTrue(result["human_review_required"])
        self.assertIn("报名状态与中标状态冲突", result["data_quality_flags"])
        self.assertIn("已弃标不能同时处于待报名", result["risk_reasons"])

    def test_business_rules_flag_execution_project_with_closed_bid_status_as_conflict(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine().evaluate({
            "project_name": '合成项目010',
            "lifecycle_stage": "execution",
            "bid_status": "已弃标",
        })

        self.assertEqual(result["business_stage"], "closed")
        self.assertEqual(result["risk_level"], "high")
        self.assertTrue(result["human_review_required"])
        self.assertIn("执行阶段与中标状态冲突", result["data_quality_flags"])
        self.assertIn("执行阶段不能同时处于已弃标", result["risk_reasons"])

    def test_business_rules_flag_overdue_registration_and_missing_fields(self):
        from business_rules import BidProjectRuleEngine

        result = BidProjectRuleEngine(today="2026-07-02").evaluate({
            "project_name": '合成项目011',
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
        self.assertNotIn("customer_name", result["missing_fields"])
        self.assertNotIn("sales_owner", result["missing_fields"])
        self.assertIn("确认报名是否完成", result["next_actions"])

    def test_project_ledger_writes_business_judgement_to_state_and_markdown(self):
        import tempfile
        from ledger import ProjectLedger

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            result = ledger.apply_patch({
                "project_name": '合成项目012',
                "source_type": "xlsx_summary",
                "facts": {
                    "project_name": '合成项目012',
                    "bid_status": "已中标",
                    "contract_status": "未签约",
                    "customer_name": '合成客户001',
                    "sales_owner": '虚构甲',
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

    def test_project_overview_markdown_hides_maintenance_removed_candidates(self):
        import json
        import tempfile
        from ledger import ProjectLedger

        with tempfile.TemporaryDirectory() as td:
            ledger = ProjectLedger(base_dir=td)
            result = ledger.apply_patch({
                "project_name": '合成项目013',
                "source_type": "local_file",
                "facts": {
                    "project_name": '合成项目013',
                    "customer_name": "待确认",
                    "sales_owner": "虚构人员字段019",
                    "bid_status": "已弃标",
                    "registration_status": "待报名",
                },
            })
            with open(result["state_path"], "r", encoding="utf-8") as f:
                state = json.load(f)
            state["current_facts"].pop("customer_name", None)
            state["current_facts"].pop("sales_owner", None)
            state["current_facts"]["registration_status"] = "已弃标"
            state["fact_meta"].pop("customer_name", None)
            state["fact_meta"].pop("sales_owner", None)
            state["maintenance_events"] = [{
                "event": "field_quality_cleanup",
                "removed_facts": [
                    {"field": "customer_name", "value": "待确认"},
                    {"field": "sales_owner", "value": "待确认"},
                ],
            }, {
                "event": "state_consistency_cleanup",
                "updated_facts": {"registration_status": "已弃标"},
            }]

            ledger._save_markdown(result["markdown_path"], state)

            with open(result["markdown_path"], "r", encoding="utf-8") as f:
                md = f.read()
            self.assertNotIn("| customer_name | 待确认 |", md)
            self.assertNotIn("| sales_owner | 待确认 |", md)
            self.assertNotIn("| registration_status | 待报名 |", md)

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
                1, "合成项目020", "SYN-PROJECT-003", "合成机构023有限公司", "虚构甲",
                "2026-05-12", "2026-05-12", 50000, "是", "产品", "已报名", "已中标", "未签约",
                "中标通知书已归档", 3438800, "BID-001",
            ])
            ws.append([
                2, '合成项目014', "SYN-PROJECT-005", "SYN-CUSTOMER-001", '虚构乙',
                "2026-05-13", "2026-05-22", None, "否", "产品", "已报名", "已弃标", "未签约",
                "已弃标", None, "BID-002",
            ])
            exec_ws = wb.create_sheet("项目执行")
            exec_ws.append([
                "序号", "项目名称", "项目编号", "客户", "负责销售", "合同金额", "合同编号",
                "签订日期", "签约状态", "里程碑节点", "预计完成", "实际完成", "备注",
            ])
            exec_ws.append([
                1, "合成项目020", "SYN-PROJECT-003", "合成机构023有限公司", "虚构甲",
                3438800, 'SYN-CONTRACT-001', "2026-05-29", "已签合同", "合同签订", "2026-05", "2026-05",
                "合同已归档",
            ])
            wb.save(xlsx_path)

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.import_project_detail_workbook(xlsx_path)

            self.assertEqual(result["schema_version"], "project_detail_workbook.import.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["processed_projects"], 2)
            self.assertEqual(result["execution_rows"], 1)
            self.assertEqual(result["projects"][0]["facts"]["project_code"], "SYN-PROJECT-003")
            self.assertEqual(result["projects"][0]["facts"]["bid_status"], "已中标")
            self.assertEqual(result["projects"][0]["facts"]["lifecycle_stage"], "execution")
            self.assertEqual(result["projects"][0]["facts"]["contract_code"], 'SYN-CONTRACT-001')
            self.assertEqual(result["projects"][1]["facts"]["lifecycle_stage"], "closed")
            with open(result["projects"][1]["artifacts"]["project_ledger_json"], "r", encoding="utf-8") as f:
                lost_state = json.load(f)
            self.assertEqual(lost_state["business_judgement"]["display_status"], "已弃标")
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
                1, "合成项目020", "SYN-PROJECT-003", "合成机构023有限公司", "虚构甲",
                "2026-05-12", "2026-05-12", 50000, "是", "产品", "已报名", "已中标", "已签合同",
                "中标通知书已归档", 3438800, "BID-001",
            ])
            ws.append([
                2, "合成项目021", "SYN-PROJECT-006", "合成机构024有限公司", "虚构甲",
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
            self.assertIn("合成项目020", content)
            self.assertIn("合成项目021", content)
            self.assertIn('<col class="col-index">', content)
            self.assertIn('<col class="col-project">', content)
            self.assertIn('<th class="center">序号</th>', content)
            self.assertIn('<th class="project-heading">项目名称</th>', content)
            self.assertIn('<table class="project-table">', content)
            self.assertIn("table-layout: fixed", content)
            self.assertNotIn("source-note", content)
            self.assertNotIn("展示页是派生产物", content)
            self.assertNotIn("当前筛选口径", content)
            self.assertNotIn("项目账本总量", content)
            self.assertNotIn("明细表、业务判断、下一步动作、复核标记均来自项目账本", content)
            self.assertNotIn("th { position: sticky; top: 59px", content)
            self.assertIn("项目详情", content)
            self.assertIn("里程碑进度", content)
            self.assertIn("任务跟踪", content)
            self.assertIn("风险与问题", content)
            self.assertIn("最新进展", content)

    def test_generate_bid_progress_html_default_output_is_business_root_entry(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from ledger import ProjectLedger
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            business_root = Path(td) / "business"
            runtime_workspace = Path(td) / "_project_manager_workspace"
            project_files_dir = business_root / "项目文件"
            business_root.mkdir()
            project_files_dir.mkdir(parents=True)
            ProjectLedger(base_dir=str(project_files_dir)).apply_patch({
                "project_name": '合成项目015',
                "facts": {
                    "project_name": '合成项目015',
                    "customer_name": '合成客户001',
                    "sales_owner": '虚构乙',
                    "bid_status": "已中标",
                },
                "evidence": [{"field": "bid_status", "source_ref": "test", "confidence": 0.9}],
                "source_type": "unit_test",
            })

            with patch.dict(os.environ, {
                "PROJECT_MANAGER_BUSINESS_ROOT": str(business_root),
                "PROJECT_MANAGER_WORKSPACE_DIR": str(runtime_workspace),
            }, clear=False):
                result = DataCleaningTools().generate_bid_progress_html()

            expected = business_root / "投标进度总览.html"
            self.assertEqual(Path(result["output_file"]), expected)
            self.assertTrue(expected.exists())
            self.assertFalse((runtime_workspace / "数据清洗工作台" / "投标进度总览.html").exists())

    def test_default_archive_plan_targets_business_project_files_not_runtime_workspace(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            business_root = Path(td) / "business"
            runtime_workspace = Path(td) / "_project_manager_workspace"
            project_name = '合成项目016'
            source_dir = business_root / "项目文件" / "项目丢标" / project_name
            source_dir.mkdir(parents=True)
            source = source_dir / "项目记录.md"
            source.write_text(f"# 项目记录：{project_name}\n\n报名状态：已弃标\n", encoding="utf-8")

            with patch.dict(os.environ, {
                "PROJECT_MANAGER_BUSINESS_ROOT": str(business_root),
                "PROJECT_MANAGER_WORKSPACE_DIR": str(runtime_workspace),
            }, clear=False):
                result = DataCleaningTools().prepare_file_organization_run([str(source)])

            action = result["archive_actions"][0]
            self.assertEqual(action["status"], "ready")
            self.assertTrue(str(action["target_path"]).startswith(str(business_root / "项目文件")))
            self.assertFalse(str(action["target_path"]).startswith(str(runtime_workspace)))
            self.assertIn(os.path.join("项目丢标", project_name, "原始文件"), action["target_path"])
            self.assertTrue((business_root / "项目文件" / "项目丢标" / project_name / "数字资产" / "project_ledger.json").exists())

    def test_prepare_file_organization_run_outputs_partial_package_and_archive_plan(self):
        import tempfile
        from docx import Document
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            doc_path = os.path.join(td, "采购公告.docx")
            doc = Document()
            doc.add_paragraph("合成机构022有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph('合成项目006')
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

    def test_image_document_uses_ocr_adapter_and_updates_ledger(self):
        import tempfile
        from PIL import Image
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "扫描公告.png")
            Image.new("RGB", (320, 120), color="white").save(image_path)

            def fake_ocr(path):
                return {
                    "status": "success",
                    "engine": "fake-test-ocr",
                    "text": '项目名称：合成项目017\n采购人：合成客户001',
                    "pages": [{"page": 1, "text": '项目名称：合成项目017\n采购人：合成客户001', "confidence": 0.91}],
                }

            tools = DataCleaningTools(workspace_dir=td, ocr_adapter=fake_ocr)
            result = tools.prepare_file_organization_run([image_path])

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["processed"], 1)
            self.assertEqual(result["project_name"], '合成项目017')
            self.assertEqual(result["archive_actions"][0]["project_name"], '合成项目017')
            self.assertTrue(os.path.exists(result["structured_outputs"][0]))
            with open(result["structured_outputs"][0], "r", encoding="utf-8") as f:
                structured = json.load(f)
            self.assertEqual(structured["extraction"]["extract_method"], "ocr")
            self.assertEqual(structured["extraction"]["ocr"]["engine"], "fake-test-ocr")
            self.assertEqual(structured["extraction"]["fields"]["project_name"], '合成项目017')

    def test_image_document_without_ocr_adapter_is_blocked_not_silent_success(self):
        import tempfile
        from PIL import Image
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "扫描公告.png")
            Image.new("RGB", (320, 120), color="white").save(image_path)

            tools = DataCleaningTools(workspace_dir=td)
            extracted = tools.extract_document(image_path)

            self.assertEqual(extracted["status"], "blocked")
            self.assertEqual(extracted["blocked_reason"], "ocr_engine_failed")
            self.assertTrue(extracted["is_scanned"])
            self.assertEqual(extracted["fields"], {})

            result = tools.prepare_file_organization_run([image_path])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["processed"], 0)
            self.assertEqual(result["failed"], 1)
            self.assertIn("engine", result["failures"][0]["error"].lower())

    def test_scanned_pdf_can_use_ocr_sidecar_text(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "扫描公告.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n% scanned fixture placeholder\n")
            with open(f"{pdf_path}.ocr.txt", "w", encoding="utf-8") as f:
                f.write('项目名称：合成项目018\n采购人：合成客户001')

            tools = DataCleaningTools(workspace_dir=td)
            result = tools.prepare_file_organization_run([pdf_path])

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["project_name"], '合成项目018')
            with open(result["structured_outputs"][0], "r", encoding="utf-8") as f:
                structured = json.load(f)
            self.assertEqual(structured["extraction"]["extract_method"], "ocr")
            self.assertEqual(structured["extraction"]["ocr"]["engine"], "sidecar_text")
            self.assertEqual(structured["extraction"]["fields"]["project_name"], '合成项目018')

    def test_apply_human_review_corrects_ledger_business_judgement(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            tools = DataCleaningTools(workspace_dir=td)
            tools.update_project_ledger(
                project_name='合成项目019',
                facts={
                    "project_name": '合成项目019',
                    "bid_status": "待开标",
                    "contract_status": "未签约",
                    "customer_name": '合成客户001',
                    "sales_owner": '虚构甲',
                },
                evidence=[],
                source_type="xlsx_summary",
            )

            result = tools.apply_human_review([
                {
                    "project_name": '合成项目019',
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
            doc.add_paragraph("合成机构022有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph('合成项目006')
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
                    "project_name": '合成项目006',
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

    def test_execute_archive_plan_writes_archive_status_ledger_under_archive_phase(self):
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            project_name = '合成项目020'
            source = Path(td) / "incoming" / "项目执行" / project_name / "合同.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"%PDF placeholder")

            target = Path(tools.project_files_dir) / "项目执行" / project_name / "原始文件" / source.name
            run_id = "test_execute_phase_ledger"
            run_dir = Path(tools.workspace_dir) / "runs" / run_id
            run_dir.mkdir(parents=True)
            plan = {
                "schema_version": "archive_plan.v1",
                "run_id": run_id,
                "actions": [{
                    "schema_version": "archive_action.v1",
                    "run_id": run_id,
                    "status": "ready",
                    "source_file": str(source),
                    "project_name": project_name,
                    "document_type": "合同",
                    "target_dir": str(target.parent),
                    "target_path": str(target),
                    "blockers": [],
                    "archive_decision": {
                        "archive_phase": "项目执行",
                    },
                }],
            }
            with open(run_dir / "planned_archive_actions.json", "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)

            executed = tools.execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(executed["status"], "success")
            self.assertTrue(target.exists())
            self.assertTrue((Path(tools.project_files_dir) / "项目执行" / project_name / "数字资产" / "project_ledger.json").exists())
            self.assertFalse((Path(tools.project_files_dir) / project_name / "数字资产" / "project_ledger.json").exists())

    def test_execute_archive_plan_updates_business_state_without_event_log(self):
        """execute_archive_plan 应更新业务账本（state + 项目总览.md），
        但不再写 archive_events.jsonl 事件日志（2026-07-15 用户决策）。"""
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            project_name = '合成项目021'
            source_dir = Path(td) / "incoming" / "项目执行" / project_name
            source_dir.mkdir(parents=True)
            first = source_dir / "合同A.pdf"
            second = source_dir / "合同B.pdf"
            first.write_bytes(b"%PDF A")
            second.write_bytes(b"%PDF B")

            run_id = "test_archive_events_not_business_facts"
            run_dir = Path(tools.workspace_dir) / "runs" / run_id
            run_dir.mkdir(parents=True)
            actions = []
            for source in [first, second]:
                target = Path(tools.project_files_dir) / "项目执行" / project_name / "原始文件" / source.name
                actions.append({
                    "schema_version": "archive_action.v1",
                    "run_id": run_id,
                    "status": "ready",
                    "source_file": str(source),
                    "project_name": project_name,
                    "document_type": "合同",
                    "target_dir": str(target.parent),
                    "target_path": str(target),
                    "blockers": [],
                    "archive_decision": {
                        "archive_phase": "项目执行",
                    },
                })
            with open(run_dir / "planned_archive_actions.json", "w", encoding="utf-8") as f:
                json.dump({"schema_version": "archive_plan.v1", "run_id": run_id, "actions": actions}, f, ensure_ascii=False, indent=2)

            executed = tools.execute_archive_plan(run_id, confirmed=True)

            self.assertEqual(executed["status"], "success")
            asset_dir = Path(tools.project_files_dir) / "项目执行" / project_name / "数字资产"
            state = json.loads((asset_dir / "project_ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current_facts"]["lifecycle_stage"], "execution")
            self.assertEqual(state["business_judgement"]["display_status"], "已中标")
            self.assertNotIn("last_archived_file", state["current_facts"])
            self.assertNotIn("archive_status", state["current_facts"])
            self.assertFalse(any(item["field"] == "last_archived_file" for item in state["conflicts"]))
            # archive_events.jsonl 不再生成（2026-07-15 用户决策）
            self.assertFalse((asset_dir / "archive_events.jsonl").exists())
            # archive_manifest.json 仍生成
            self.assertTrue((asset_dir / "archive_manifest.json").exists())
            # 项目总览.md 仍生成
            self.assertTrue((asset_dir / "项目总览.md").exists())

    def test_default_tool_paths_use_workspace_config(self):
        from common.workspace_config import resolve_workspace_config
        from tools.data_cleaning_tools import DataCleaningTools
        from tools.project_tools import ProjectTools
        from tools.opportunity_tools import OpportunityManagerTools

        config = resolve_workspace_config(config_file="")

        self.assertEqual(DataCleaningTools().workspace_dir, str(config.data_cleaning_workspace))
        self.assertEqual(ProjectTools().base_dir, str(config.project_files_dir))
        self.assertEqual(OpportunityManagerTools().opportunity_dir, str(config.opportunity_dir))

    def test_markdown_project_record_extracts_clean_lost_project_fields(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        text = '# 项目记录：合成项目014\n\n## 基本信息\n- **项目名称**：合成项目014\n- **CRM 编号**：SYN-PROJECT-005\n- **招标人/客户**：SYN-CUSTOMER-001\n- **负责销售**：虚构乙\n\n## 项目状态\n- 报名状态：已弃标\n- 中标状态：已弃标\n- 签约状态：未签约\n'

        fields = tools._extract_fields(text)

        self.assertEqual(fields["project_name"], '合成项目014')
        self.assertEqual(fields["project_code"], "SYN-PROJECT-005")
        self.assertEqual(fields["customer_name"], "SYN-CUSTOMER-001")
        self.assertEqual(fields["sales_owner"], '虚构乙')
        self.assertEqual(fields["registration_status"], "已弃标")
        self.assertEqual(fields["bid_status"], "已弃标")
        self.assertEqual(fields["closed_reason_type"], "abandoned_by_us")
        self.assertEqual(fields["lifecycle_stage"], "closed")

    def test_project_record_field_extraction_rejects_table_noise_sales_owner(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        text = '# 项目记录：合成项目010\n\n## 基本信息\n- **招标人/客户**：）合成机构021有限公司\n- **负责销售**：| | |\n'

        fields = tools._extract_fields(text)

        self.assertEqual(fields["customer_name"], "合成机构021有限公司")
        self.assertNotIn("sales_owner", fields)

    def test_project_record_field_extraction_skips_placeholder_values(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        text = '# 项目记录：合成项目010\n\n## 基本信息\n- **招标人/客户**：待确认\n- **负责销售**：待确认\n'

        fields = tools._extract_fields(text)

        self.assertNotIn("customer_name", fields)
        self.assertNotIn("sales_owner", fields)

    def test_contract_text_is_classified_as_contract_before_filename_bucket(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        document_type = tools._classify_text_document(
            '技术开发合同-合成项目01020260626-关键页扫描.pdf',
            "合同登记编号:\n技 术 开 发 合 同\n委托人: 合成机构021有限公司\n受托人: 合成机构003有限公司",
        )

        self.assertEqual(document_type, "合同")

    def test_project_record_filename_is_not_reclassified_by_embedded_invoice_text(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        document_type = tools._classify_text_document(
            "项目记录.md",
            '项目记录\n电子发票已开具\n发票号码：SYN-INVOICE-001',
        )

        self.assertEqual(document_type, "项目记录")

    def test_invoice_text_is_classified_as_invoice_before_project_bucket(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        document_type = tools._classify_text_document(
            "电子发票-普华和诚至华胜天成-20260703-48000元.pdf",
            '电子发票（增值税专用发票）\n发票号码：SYN-INVOICE-001',
        )

        self.assertEqual(document_type, "发票")

    def test_markdown_project_record_distinguishes_lost_from_abandoned(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        text = '# 项目记录：合成项目022\n\n## 项目状态\n- 报名状态：已报名\n- 中标状态：未中标\n'

        fields = tools._extract_fields(text)

        self.assertEqual(fields["registration_status"], "已报名")
        self.assertEqual(fields["bid_status"], "已丢标")
        self.assertEqual(fields["closed_reason_type"], "lost_to_competitor")
        self.assertEqual(fields["lifecycle_stage"], "closed")

    def test_project_lost_directory_context_overrides_record_status_and_keeps_outputs_unique(self):
        import json
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "项目文件" / "项目丢标"
            first = root / '合成项目014'
            second = root / '合成项目023'
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "项目记录.md").write_text(
                '# 项目记录：合成项目014\n\n## 项目状态\n- 报名状态：待报名\n- 中标状态：待开标\n',
                encoding="utf-8",
            )
            (second / "项目记录.md").write_text(
                '# 项目记录：合成项目023\n\n## 项目状态\n- 报名状态：待报名\n- 中标状态：待开标\n',
                encoding="utf-8",
            )

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            result = tools.prepare_file_organization_run([
                str(first / "项目记录.md"),
                str(second / "项目记录.md"),
            ])

            self.assertEqual(result["status"], "success")
            self.assertEqual(len(result["structured_outputs"]), 2)
            self.assertEqual(len(set(result["structured_outputs"])), 2)
            for output_path in result["structured_outputs"]:
                structured = json.loads(Path(output_path).read_text(encoding="utf-8"))
                self.assertEqual(structured["extraction"]["fields"]["lifecycle_stage"], "closed")
                self.assertEqual(structured["extraction"]["fields"]["bid_status"], "已弃标")
                self.assertEqual(structured["extraction"]["fields"]["registration_status"], "已弃标")
                self.assertEqual(structured["extraction"]["fields"]["closed_reason_type"], "abandoned_by_us")
                self.assertEqual(structured["business_judgement"]["business_stage"], "closed")
            for action in result["archive_actions"]:
                self.assertEqual(action["status"], "ready")
                self.assertEqual(action["archive_decision"]["archive_phase"], "项目丢标")
                self.assertEqual(action["business_judgement"]["business_stage"], "closed")

    def test_project_lost_directory_context_overrides_existing_pending_ledger_fact(self):
        import json
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目023'
            bidding_dir = Path(td) / "项目文件" / "项目投标" / project_name
            lost_dir = Path(td) / "项目文件" / "项目丢标" / project_name
            bidding_dir.mkdir(parents=True)
            lost_dir.mkdir(parents=True)
            record_text = f"# 项目记录：{project_name}\n\n## 项目状态\n- 报名状态：待报名\n- 中标状态：待开标\n"
            (bidding_dir / "项目记录.md").write_text(record_text, encoding="utf-8")
            (lost_dir / "项目记录.md").write_text(record_text, encoding="utf-8")

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            tools.prepare_file_organization_run([str(bidding_dir / "项目记录.md")])
            result = tools.prepare_file_organization_run([str(lost_dir / "项目记录.md")])

            self.assertEqual(result["archive_actions"][0]["business_judgement"]["business_stage"], "closed")
            ledger_path = Path(result["structured_outputs"][0]).read_text(encoding="utf-8")
            structured = json.loads(ledger_path)
            self.assertEqual(structured["business_judgement"]["business_stage"], "closed")
            self.assertEqual(structured["extraction"]["fields"]["bid_status"], "已弃标")

    def test_project_lost_nested_material_uses_parent_project_name(self):
        import os
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目023'
            material_dir = Path(td) / "项目文件" / "项目丢标" / project_name / "报名材料"
            material_dir.mkdir(parents=True)
            material = material_dir / "合成银行025报名材料.md"
            material.write_text("# 报名材料\n\n单位名称：合成机构026有限公司\n", encoding="utf-8")

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            result = tools.prepare_file_organization_run([str(material)])

            self.assertEqual(result["status"], "success")
            action = result["archive_actions"][0]
            self.assertEqual(action["status"], "ready")
            self.assertEqual(action["project_name"], project_name)
            self.assertEqual(action["archive_decision"]["archive_phase"], "项目丢标")
            self.assertEqual(action["blockers"], [])
            self.assertIn(project_name, action["target_path"])

    def test_already_archived_source_file_does_not_create_target_exists_review(self):
        import os
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目024'
            project_dir = Path(td) / "workspace" / "项目文件" / "项目丢标" / project_name / "原始文件"
            project_dir.mkdir(parents=True)
            record = project_dir / "项目记录.md"
            record.write_text(f"# 项目记录：{project_name}\n\n报名状态：已弃标\n", encoding="utf-8")

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            result = tools.prepare_file_organization_run([str(record)])

            action = result["archive_actions"][0]
            self.assertEqual(action["status"], "already_archived")
            self.assertEqual(action["blockers"], [])
            self.assertNotIn("target_exists", action["blockers"])
            review_items = result["review_queue"]["items"]
            self.assertFalse(any(item.get("type") == "archive_action_review" for item in review_items))

    def test_project_lost_unsupported_nested_file_gets_metadata_archive_action(self):
        import os
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目025'
            material_dir = Path(td) / "项目文件" / "项目丢标" / project_name / "招标文件"
            material_dir.mkdir(parents=True)
            material = material_dir / "技术规范书附件.zip"
            material.write_bytes(b"zip placeholder")

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            result = tools.prepare_file_organization_run([str(material)])

            self.assertEqual(result["status"], "success")
            action = result["archive_actions"][0]
            self.assertEqual(action["status"], "ready")
            self.assertEqual(action["project_name"], project_name)
            self.assertEqual(action["archive_decision"]["archive_phase"], "项目丢标")
            self.assertEqual(action["blockers"], [])
            self.assertIn(os.path.join("项目丢标", project_name, "原始文件"), action["target_path"])
            self.assertIn(project_name, action["target_path"])
            self.assertEqual(result["failures"], [])
            self.assertFalse((Path(tools.project_files_dir) / project_name / "数字资产" / "project_ledger.json").exists())

    def test_known_phase_pdf_gets_archive_plan_without_blocking_on_ocr(self):
        import os
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目026'
            material_dir = Path(td) / "项目文件" / "项目执行" / project_name / "合同文件"
            material_dir.mkdir(parents=True)
            material = material_dir / "盖章扫描件.pdf"
            material.write_bytes(b"%PDF placeholder")

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            result = tools.prepare_file_organization_run([str(material)])

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["failures"], [])
            action = result["archive_actions"][0]
            self.assertEqual(action["status"], "ready")
            self.assertEqual(action["project_name"], project_name)
            self.assertEqual(action["archive_decision"]["archive_phase"], "项目执行")
            self.assertIn(os.path.join("项目执行", project_name, "原始文件"), action["target_path"])
            self.assertEqual(result["review_queue"]["items"][0]["type"], "extraction_quality_review")
            self.assertEqual(result["review_queue"]["items"][0]["reason"], "metadata_passthrough_archive_only")

    def test_project_execution_directory_context_marks_execution_stage(self):
        import os
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目027'
            project_dir = Path(td) / "项目文件" / "项目执行" / project_name
            project_dir.mkdir(parents=True)
            record = project_dir / "项目记录.md"
            record.write_text(
                f"# 项目记录：{project_name}\n\n客户名称：合成科技有限公司\n负责销售：虚构人员补充\n",
                encoding="utf-8",
            )

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            result = tools.prepare_file_organization_run([str(record)])

            structured = json.loads(Path(result["structured_outputs"][0]).read_text(encoding="utf-8"))
            self.assertEqual(structured["extraction"]["fields"]["lifecycle_stage"], "execution")
            self.assertEqual(structured["business_judgement"]["business_stage"], "execution")
            self.assertEqual(structured["business_judgement"]["display_status"], "已中标")

    def test_structured_output_contains_business_case_for_status_conflict(self):
        import os
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目004'
            material_dir = Path(td) / "项目文件" / "项目执行" / project_name
            material_dir.mkdir(parents=True)
            record = material_dir / "项目记录.md"
            record.write_text("# 项目记录\n", encoding="utf-8")
            extracted = {
                "filename": "项目记录.md",
                "file_type": ".md",
                "document_type": "项目记录",
                "extracted_text": '项目名称：合成项目004\n中标状态：已弃标',
                "fields": {
                    "project_name": project_name,
                    "bid_status": "已弃标",
                    "lifecycle_stage": "execution",
                },
                "extract_method": "unit_test",
            }

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            with patch.object(tools, "_use_archive_metadata_passthrough", return_value=False):
                with patch.object(tools, "extract_document", return_value=extracted):
                    result = tools.prepare_file_organization_run([str(record)])

            structured = json.loads(Path(result["structured_outputs"][0]).read_text(encoding="utf-8"))
            cases = structured["business_cases"]

            self.assertEqual(cases[0]["schema_version"], "business_case.v1")
            self.assertEqual(cases[0]["case_type"], "status_conflict")
            self.assertEqual(cases[0]["output"]["decision"], "needs_review")
            self.assertIn("不自动合并", cases[0]["output"]["reason"])
            self.assertEqual(cases[0]["entities"][0]["project_name"], project_name)

    def test_low_quality_ocr_does_not_write_business_ledger_facts(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目028'
            material_dir = Path(td) / "项目文件" / "项目丢标" / project_name / "图片材料"
            material_dir.mkdir(parents=True)
            material = material_dir / "扫描件.png"
            material.write_bytes(b"fake image")

            low_quality_extraction = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": str(material),
                "filename": material.name,
                "file_type": ".png",
                "document_type": "项目投标",
                "extract_method": "easyocr",
                "text_length": 12,
                "extracted_text": '合成项目029 合成客户004',
                "fields": {"project_name": '合成项目029', "customer_name": '合成客户004'},
                "needs_human_review": True,
                "ocr": {
                    "quality": {
                        "needs_human_review": True,
                        "flags": ["low_confidence"],
                    }
                },
            }

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            with patch.object(tools, "_use_archive_metadata_passthrough", return_value=False):
                with patch.object(tools, "extract_document", return_value=low_quality_extraction):
                    result = tools.prepare_file_organization_run([str(material)])

            self.assertEqual(result["status"], "success")
            action = result["archive_actions"][0]
            self.assertEqual(action["status"], "ready")
            self.assertEqual(action["project_name"], project_name)
            self.assertIn("low_quality_extraction", action["archive_decision"]["reasons"])
            self.assertFalse((Path(tools.project_files_dir) / project_name / "数字资产" / "project_ledger.json").exists())
            self.assertEqual(result["review_queue"]["status"], "needs_review")
            self.assertEqual(result["review_queue"]["items"][0]["type"], "extraction_quality_review")

    def test_field_quality_rejects_noisy_fields_before_ledger_write(self):
        import json
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目030'
            material_dir = Path(td) / "项目文件" / "项目执行" / project_name / "验收材料"
            material_dir.mkdir(parents=True)
            material = material_dir / "验收单.png"
            material.write_bytes(b"fake image")

            noisy_extraction = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": str(material),
                "filename": material.name,
                "file_type": ".png",
                "document_type": "验收材料",
                "extract_method": "easyocr",
                "text_length": 200,
                "extracted_text": '合成噪声字段销售负责人\n合成客户005',
                "fields": {
                    "sales_owner": '合成噪声字段销售负责人',
                    "customer_name": '合成客户005',
                },
                "needs_human_review": False,
                "ocr": {"quality": {"needs_human_review": False, "flags": []}},
            }

            tools = DataCleaningTools(workspace_dir=os.path.join(td, "workspace"))
            with patch.object(tools, "_use_archive_metadata_passthrough", return_value=False):
                with patch.object(tools, "extract_document", return_value=noisy_extraction):
                    result = tools.prepare_file_organization_run([str(material)])

            structured = json.loads(Path(result["structured_outputs"][0]).read_text(encoding="utf-8"))
            accepted = structured["accepted_business_facts"]
            self.assertEqual(accepted["project_name"], project_name)
            self.assertEqual(accepted["lifecycle_stage"], "execution")
            self.assertNotIn("sales_owner", accepted)
            self.assertNotIn("customer_name", accepted)
            rejected_fields = {item["field"] for item in structured["field_quality"]["rejected_fields"]}
            self.assertEqual(rejected_fields, {"sales_owner", "customer_name"})

            ledger_path = Path(structured["ledger_artifacts"]["project_ledger_json"])
            state = json.loads(ledger_path.read_text(encoding="utf-8"))
            self.assertNotIn("sales_owner", state["current_facts"])
            self.assertNotIn("customer_name", state["current_facts"])
            self.assertEqual(state["business_judgement"]["business_stage"], "execution")

    def test_bid_progress_win_rate_excludes_abandoned_projects(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        projects = [
            {"project_name": '合成项目031', "overview_status": "已中标"},
            {"project_name": '合成项目032', "overview_status": "已丢标"},
            {"project_name": '合成项目033', "overview_status": "已弃标"},
        ]
        groups = {
            "已中标": [projects[0]],
            "已丢标": [projects[1]],
            "已弃标": [projects[2]],
            "参与中": [],
        }

        html = tools._render_bid_progress_html(projects, groups)

        self.assertIn("50.0%", html)

    def test_sales_stats_show_hardcoded_leader_name_win_rate_as_sales_owner(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())
        html = tools._render_sales_stats([
            {"sales_owner": '虚构丁', "overview_status": "已中标"},
            {"sales_owner": '虚构丁', "overview_status": "已丢标"},
            {"sales_owner": '虚构丁', "overview_status": "已弃标"},
            {"sales_owner": '虚构乙', "overview_status": "已中标"},
        ])

        self.assertIn("<th>中标率</th>", html)
        self.assertIn("<td>虚构丁</td><td>3</td><td>1</td><td>1</td><td>1</td><td>0</td><td>50.0%</td>", html)
        self.assertIn('<td>虚构乙</td><td>1</td><td>1</td><td>0</td><td>0</td><td>0</td><td>100.0%</td>', html)

    def test_extract_structured_business_output_is_read_only_loop(self):
        import json
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            project_name = '合成项目010'
            source_dir = Path(td) / "business" / "项目文件" / "项目执行" / project_name / "原始文件"
            source_dir.mkdir(parents=True)
            current = source_dir / "项目记录.md"
            current.write_text(
                '# 项目记录：合成项目010\n'
                "- **招标人/客户**：合成机构021有限公司\n"
                '电子发票已开具\n发票号码：SYN-INVOICE-001\n',
                encoding="utf-8",
            )
            old_lost = source_dir / '项目记录__from_项目丢标_合成项目003.md'
            old_lost.write_text(
                '# 项目记录：合成项目010\n'
                "- 报名状态：已弃标\n"
                "- 中标状态：已弃标\n"
                "- 签约状态：未签约\n"
                "- 负责销售：虚构人员补充\n",
                encoding="utf-8",
            )
            backup = source_dir / "项目记录.md.bak_state_consistency"
            backup.write_text("backup", encoding="utf-8")

            workspace = Path(td) / "workspace"
            tools = DataCleaningTools(workspace_dir=str(workspace))
            result = tools.extract_structured_business_output(source_dir=str(source_dir))

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["processed"], 2)
            self.assertEqual(len(result["business_cases"]), 1)
            self.assertTrue(result["boundary"]["updated_external_ledger"] is False)
            self.assertTrue(result["boundary"]["archive_plan_created"] is False)

            output_path = Path(result["artifacts"]["structured_business_output"])
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "business_structured_output.run.v1")
            self.assertEqual(payload["summary"]["skipped_total"], 1)
            self.assertEqual(payload["documents"][0]["document_type"], "项目记录")
            self.assertNotIn("sales_owner", payload["documents"][1]["accepted_business_facts"])
            self.assertEqual(payload["business_cases"][0]["case_type"], "status_conflict")
            self.assertFalse(list(workspace.rglob("project_ledger.json")))
            self.assertFalse("archive_actions" in payload)

    def test_semantic_structure_document_keeps_only_evidence_backed_fields(self):
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        def fake_semantic_adapter(evidence_pack):
            return {
                "document_type": "合同",
                "business_fields": {
                    "project_name": {
                        "value": '合成项目034',
                        "confidence": 0.93,
                        "evidence_refs": ["text:0"],
                    },
                    "customer_name": {
                        "value": '合成客户006',
                        "confidence": 0.92,
                        "evidence_refs": [],
                    },
                    "payment_amount": {
                        "value": "48000.00",
                        "confidence": 0.42,
                        "evidence_refs": ["text:1"],
                    },
                },
                "uncertain_fields": [],
                "review_reasons": [],
            }

        with tempfile.TemporaryDirectory() as td:
            source_path = Path(td) / "合同.md"
            source_path.write_text('项目名称：合成项目034\n合同金额：48000.00\n', encoding="utf-8")
            tools = DataCleaningTools(
                workspace_dir=str(Path(td) / "workspace"),
                semantic_adapter=fake_semantic_adapter,
            )
            evidence_pack = tools.build_evidence_pack(str(source_path))
            result = tools.semantic_structure_document(evidence_pack)

            self.assertEqual(result["schema_version"], "semantic_document.v1")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["accepted_business_facts"], {"project_name": '合成项目034'})
            rejected = {item["field"]: item["reason"] for item in result["semantic_guardrail"]["rejected_fields"]}
            self.assertEqual(rejected["customer_name"], "missing_evidence_refs")
            self.assertEqual(rejected["payment_amount"], "semantic_confidence_too_low")

    def test_extract_structured_business_output_attaches_semantic_json_without_side_effects(self):
        import json
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        def fake_semantic_adapter(evidence_pack):
            return {
                "document_type": "合同",
                "business_fields": {
                    "project_name": {
                        "value": '合成项目035',
                        "confidence": 0.91,
                        "evidence_refs": ["path:project_name"],
                    },
                    "customer_name": {
                        "value": '合成客户006',
                        "confidence": 0.9,
                        "evidence_refs": ["text:0"],
                    },
                },
                "uncertain_fields": [],
                "review_reasons": [],
            }

        with tempfile.TemporaryDirectory() as td:
            source_dir = Path(td) / "business" / "项目文件" / "项目执行" / '合成项目035' / "原始文件"
            source_dir.mkdir(parents=True)
            record = source_dir / "合同.md"
            record.write_text(
                '# 项目记录：合成项目035\n招标人/客户：合成客户006\n',
                encoding="utf-8",
            )

            workspace = Path(td) / "workspace"
            tools = DataCleaningTools(workspace_dir=str(workspace), semantic_adapter=fake_semantic_adapter)
            result = tools.extract_structured_business_output(source_dir=str(source_dir))
            payload = json.loads(Path(result["artifacts"]["structured_business_output"]).read_text(encoding="utf-8"))
            document = payload["documents"][0]

            self.assertEqual(document["semantic_structure"]["status"], "success")
            self.assertEqual(document["semantic_structure"]["document_type"], "合同")
            self.assertEqual(document["semantic_structure"]["accepted_business_facts"]["project_name"], '合成项目035')
            self.assertTrue(result["boundary"]["updated_external_ledger"] is False)
            self.assertTrue(result["boundary"]["archive_plan_executed"] is False)
            self.assertFalse(list(workspace.rglob("project_ledger.json")))

    def test_semantic_structure_document_is_registered_read_only_tool(self):
        from main import _build_registry_for_skill

        reg = _build_registry_for_skill("data_cleaning_file_organization")
        tools = reg.list_tools()

        self.assertIn("semantic_structure_document", tools)
        schema = reg.get("semantic_structure_document").to_schema()
        self.assertIn("只读", schema["description"])
        self.assertEqual(schema["parameters"]["properties"]["evidence_pack"]["type"], "object")

    def test_extract_structured_business_output_keeps_per_file_project_name_with_aggregate_label(self):
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_dir = Path(td) / "business" / "项目文件" / "项目执行" / '合成项目036' / "原始文件"
            source_dir.mkdir(parents=True)
            record = source_dir / "项目记录.md"
            record.write_text(
                '# 项目记录：合成项目036\n'
                "- 报名状态：已弃标\n"
                "- 中标状态：已弃标\n"
                "- 签约状态：未签约\n",
                encoding="utf-8",
            )

            tools = DataCleaningTools(workspace_dir=str(Path(td) / "workspace"))
            result = tools.extract_structured_business_output(
                source_dir=str(source_dir),
                project_name='合成项目037',
            )

            self.assertEqual(result["business_cases"][0]["entities"][0]["project_name"], '合成项目036')

    def test_extract_structured_business_output_ocr_reads_known_phase_images(self):
        import json
        import tempfile
        from pathlib import Path
        from tools.data_cleaning_tools import DataCleaningTools

        def fake_ocr(path):
            return {
                "status": "success",
                "engine": "fake_ocr",
                "text": '项目名称：合成项目038\n委托人：合成客户006',
                "pages": [{"page": 1, "text": '项目名称：合成项目038\n委托人：合成客户006'}],
            }

        with tempfile.TemporaryDirectory() as td:
            source_dir = Path(td) / "business" / "项目文件" / "项目执行" / '合成项目038' / "原始文件"
            source_dir.mkdir(parents=True)
            image = source_dir / "合同扫描.png"
            image.write_bytes(b"fake image")

            tools = DataCleaningTools(workspace_dir=str(Path(td) / "workspace"), ocr_adapter=fake_ocr)
            result = tools.extract_structured_business_output(source_dir=str(source_dir))

            payload = json.loads(Path(result["artifacts"]["structured_business_output"]).read_text(encoding="utf-8"))
            document = payload["documents"][0]
            self.assertEqual(document["extract_method"], "ocr")
            self.assertGreater(document["text_length"], 0)
            self.assertNotEqual(document["document_type"], "项目执行")
            self.assertEqual(document["fields"]["customer_name"], '合成客户006')

    def test_execution_phase_document_type_uses_content_and_filename(self):
        import tempfile
        from tools.data_cleaning_tools import DataCleaningTools

        tools = DataCleaningTools(workspace_dir=tempfile.mkdtemp())

        self.assertEqual(
            tools._classify_text_document(
                'synthetic/contracts/customer-contract.docx',
                '买受人：合成科技有限公司\n出卖人：合成科技有限公司\n合同编号：SYN-CONTRACT-001\n开户银行：合成银行\n账号：SYN-ACCOUNT-001',
            ),
            "合同",
        )
        self.assertEqual(
            tools._classify_text_document(
                'synthetic/finance/payment-details.docx',
                "账户名称：合成科技有限公司\n开户银行：合成银行\n账号：SYN-ACCOUNT-001",
            ),
            "账户凭证",
        )
        self.assertEqual(
            tools._classify_text_document(
                'synthetic/execution/payment-request.docx',
                "工程款支付申请表\n申请金额：10000元\n收款单位：合成科技有限公司",
            ),
            "付款申请",
        )

    def test_structured_output_isolates_low_confidence_ocr_business_fields(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_dir = Path(td) / "business" / "项目文件" / "项目执行" / "低置信OCR项目" / "原始文件"
            source_dir.mkdir(parents=True)
            image = source_dir / "付款凭证.png"
            image.write_bytes(b"fake image")

            low_quality_extraction = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": str(image),
                "filename": image.name,
                "file_type": ".png",
                "document_type": "付款凭证",
                "extract_method": "ocr",
                "text_length": 20,
                "extracted_text": "付款人：噪声客户\n交易金额：48OOO",
                "fields": {
                    "project_name": '合成项目039',
                    "customer_name": '合成客户004',
                    "payment_amount": "48000.00",
                },
                "needs_human_review": True,
                "ocr": {"quality": {"needs_human_review": True, "flags": ["low_confidence"]}},
            }

            tools = DataCleaningTools(workspace_dir=str(Path(td) / "workspace"))
            with patch.object(tools, "extract_document", return_value=low_quality_extraction):
                result = tools.extract_structured_business_output(source_dir=str(source_dir))

            payload = json.loads(Path(result["artifacts"]["structured_business_output"]).read_text(encoding="utf-8"))
            document = payload["documents"][0]
            self.assertEqual(document["fields"]["project_name"], "低置信OCR项目")
            self.assertNotIn("customer_name", document["accepted_business_facts"])
            self.assertNotIn("payment_amount", document["accepted_business_facts"])
            self.assertEqual(document["field_quality"]["extraction_trust"]["trusted"], False)

    def test_xlsx_and_xml_documents_extract_structured_text_in_business_phase(self):
        import json
        import tempfile
        from pathlib import Path
        from openpyxl import Workbook
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_dir = Path(td) / "business" / "项目文件" / "项目执行" / "表格发票项目" / "原始文件"
            source_dir.mkdir(parents=True)

            workbook_path = source_dir / "报价表.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.append(["项目名称", "客户名称", "报价"])
            ws.append(["表格发票项目", '合成客户006', "48000"])
            wb.save(workbook_path)

            xml_path = source_dir / "电子发票.xml"
            xml_path.write_text(
                '<Invoice><BuyerName>合成客户006</BuyerName><InvoiceNo>2611200000</InvoiceNo><TotalAmount>48000.00</TotalAmount></Invoice>',
                encoding="utf-8",
            )

            tools = DataCleaningTools(workspace_dir=str(Path(td) / "workspace"))
            result = tools.extract_structured_business_output(source_dir=str(source_dir))
            payload = json.loads(Path(result["artifacts"]["structured_business_output"]).read_text(encoding="utf-8"))
            by_name = {doc["filename"]: doc for doc in payload["documents"]}

            self.assertEqual(by_name["报价表.xlsx"]["extract_method"], "xlsx_table")
            self.assertGreater(by_name["报价表.xlsx"]["text_length"], 0)
            self.assertEqual(by_name["电子发票.xml"]["extract_method"], "xml_text")
            self.assertEqual(by_name["电子发票.xml"]["document_type"], "发票")
            self.assertEqual(payload["summary"]["skipped_total"], 0)

    def test_prepare_file_organization_run_blocks_unreadable_source(self):
        import tempfile
        from unittest.mock import patch
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_path = os.path.join(td, "cloud-placeholder.pdf")
            with open(source_path, "wb") as f:
                f.write(b"placeholder")

            blocked_probe = {
                "status": "blocked",
                "path": source_path,
                "exists": True,
                "is_file": True,
                "readable": False,
                "blocked_reason": "cloud_placeholder_or_sync_failure",
                "error": "The cloud operation was unsuccessful.",
            }
            with patch("tools.data_cleaning_tools.probe_readable_file", return_value=blocked_probe):
                tools = DataCleaningTools(workspace_dir=td)
                result = tools.prepare_file_organization_run([source_path])

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["failures"][0]["stage"], "source_readiness")
            self.assertEqual(result["failures"][0]["error"], "source_not_local_or_unreadable")
            self.assertEqual(result["failures"][0]["blocked_reason"], "cloud_placeholder_or_sync_failure")
            self.assertTrue(os.path.exists(result["artifacts"]["input_manifest"]))
            self.assertTrue(os.path.exists(result["artifacts"]["review_queue"]))
            self.assertTrue(os.path.exists(source_path))

    def test_execute_archive_plan_blocks_unreadable_source_before_move(self):
        import tempfile
        from unittest.mock import patch
        from docx import Document
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_dir = os.path.join(td, "source")
            os.makedirs(source_dir)
            doc_path = os.path.join(source_dir, "采购公告.docx")
            doc = Document()
            doc.add_paragraph('项目名称：合成项目040')
            doc.save(doc_path)

            tools = DataCleaningTools(workspace_dir=td)
            prepared = tools.prepare_file_organization_run([doc_path], project_name='合成项目040')
            tools.apply_human_review([
                {
                    "project_name": '合成项目040',
                    "facts": {"project_name": '合成项目040'},
                    "reason": "测试归档前源文件可读性二次校验",
                }
            ], run_id=prepared["run_id"])

            blocked_probe = {
                "status": "blocked",
                "path": doc_path,
                "exists": True,
                "is_file": True,
                "readable": False,
                "blocked_reason": "cloud_placeholder_or_sync_failure",
                "error": "The cloud operation was unsuccessful.",
            }
            with patch("tools.data_cleaning_tools.probe_readable_file", return_value=blocked_probe):
                executed = tools.execute_archive_plan(prepared["run_id"], confirmed=True)

            self.assertEqual(executed["status"], "failed")
            self.assertEqual(executed["moved"], 0)
            self.assertEqual(executed["results"][0]["status"], "blocked")
            self.assertIn("source_not_local_or_unreadable", executed["results"][0]["blockers"])
            self.assertTrue(os.path.exists(doc_path))


# ────────────────────────────────────────────
# 测试 3：LoopEngine 核心循环
# ────────────────────────────────────────────

class TestLoopEngine(unittest.TestCase):
    """验证 LoopEngine 的完整循环逻辑"""
    
    def setUp(self):
        self.tools = {
            "scan_projects": lambda: {"projects": [{"name": '合成项目001'}], "count": 1},
            "check_milestones": lambda: {"overdue": [], "upcoming": [], "summary": {"total_checked": 1}},
            "write_response": lambda content, filename: f"Saved: {filename}",
        }
    
    def test_simple_loop_completion(self):
        """简单场景：Planner 直接返回 Final Answer，循环立即完成"""
        mock_llm = MockLLMAdapter()
        mock_llm.set_mock_responses([
            "Final Answer: 合成项目004完成"
        ])
        
        engine = LoopEngine(agent_name="test", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="合成项目004",
            system_prompt="你是合成项目004 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        self.assertEqual(trace.status, "completed")
        self.assertEqual(len(trace.rounds), 1)
        self.assertEqual(trace.final_result, "Final Answer: 合成项目004完成")
    
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
            system_prompt="你是合成项目004 Agent",
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
            goal="合成项目004",
            system_prompt="你是合成项目004 Agent",
            llm_call=llm_call,
            tools=self.tools,
        )
        
        # 第一轮工具调用失败，但循环继续
        self.assertEqual(trace.rounds[0].status, "failed")
        self.assertIn("not found", trace.rounds[0].observation.lower())
        self.assertEqual(trace.status, "completed")

    def test_observation_evaluation_is_fed_back_to_next_planner_round(self):
        """工具 blocked/错误输出应被结构化评估后喂回下一轮 planner"""
        captured_messages = []
        responses = [
            "Thought: 尝试 OCR\nAction: extract_document\nAction Input: {\"file_path\": \"scan.png\"}",
            "Final Answer: OCR 被阻塞，需要提供 OCR adapter 或 sidecar 文本",
        ]

        def blocked_tool(file_path):
            return {
                "schema_version": "document.extract.v1",
                "status": "blocked",
                "blocked_reason": "ocr_adapter_unavailable",
                "error": "ocr_adapter_unavailable: no OCR adapter configured",
                "next_steps": ["provide_ocr_sidecar", "configure_ocr_adapter"],
            }

        def llm_call(msgs, tools):
            captured_messages.append([dict(m) for m in msgs])
            return responses[len(captured_messages) - 1], 50

        engine = LoopEngine(agent_name="test", max_rounds=3)
        trace = engine.run(
            goal="解析扫描图片",
            system_prompt="你是合成项目004 Agent",
            llm_call=llm_call,
            tools={"extract_document": blocked_tool},
        )

        self.assertEqual(trace.rounds[0].status, "blocked")
        self.assertEqual(trace.rounds[0].observation_evaluation["status"], "blocked")
        self.assertEqual(trace.rounds[0].observation_evaluation["error_code"], "ocr_adapter_unavailable")
        self.assertFalse(trace.rounds[0].observation_evaluation["retryable"])
        second_round_text = "\n".join(m["content"] for m in captured_messages[1])
        self.assertIn("Observation Evaluation:", second_round_text)
        self.assertIn('"status": "blocked"', second_round_text)
        self.assertIn('"error_code": "ocr_adapter_unavailable"', second_round_text)
        self.assertEqual(trace.status, "completed")

    def test_observation_evaluation_classifies_needs_confirmation(self):
        """needs_confirmation 结果应明确提示下一轮需要人工确认而不是自动继续提交"""
        captured_messages = []
        responses = [
            "Thought: 填写 CRM 草稿\nAction: cloudcc_fill_draft_gated\nAction Input: {\"draft\": {\"name\": \"合成项目004\"}}",
            "Final Answer: 已停在提交前确认状态",
        ]

        def confirmation_tool(draft):
            return {
                "schema_version": "cloudcc.crm.result.v1",
                "status": "needs_confirmation",
                "pending_confirmation": {"action": "submit_opportunity"},
                "next_steps": ["ask_human_confirmation"],
            }

        def llm_call(msgs, tools):
            captured_messages.append([dict(m) for m in msgs])
            return responses[len(captured_messages) - 1], 50

        engine = LoopEngine(agent_name="test", max_rounds=3)
        trace = engine.run(
            goal="CRM 草稿填写",
            system_prompt="你是合成项目004 Agent",
            llm_call=llm_call,
            tools={"cloudcc_fill_draft_gated": confirmation_tool},
        )

        self.assertEqual(trace.rounds[0].status, "needs_confirmation")
        self.assertTrue(trace.rounds[0].observation_evaluation["needs_confirmation"])
        self.assertFalse(trace.rounds[0].observation_evaluation["retryable"])
        second_round_text = "\n".join(m["content"] for m in captured_messages[1])
        self.assertIn('"status": "needs_confirmation"', second_round_text)
        self.assertIn('"needs_confirmation": true', second_round_text)
    
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
            system_prompt="你是合成项目004 Agent",
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
            system_prompt="你是合成项目004 Agent",
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
        mock_llm.set_mock_responses(["Final Answer: 合成项目004"])
        
        engine = LoopEngine(agent_name="test", max_rounds=5)
        
        def llm_call(msgs, tools):
            resp = mock_llm.chat(msgs, tools)
            return resp.text, resp.tokens_used
        
        trace = engine.run(
            goal="合成项目004",
            system_prompt="你是合成项目004 Agent",
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
            self.assertEqual(data["goal"], "合成项目004")
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
            goal="合成项目004目标",
            tools_text="工具列表",
            memory_text="记忆内容"
        )
        self.assertIn("合成项目004目标", prompt)
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

    def test_main_run_data_cleaning_file_organization_uses_isolated_workspace(self):
        """main.run 可以把真实文件整理目标路由到数据清洗 skill，并写入隔离工作区"""
        import tempfile
        from docx import Document
        import main

        with tempfile.TemporaryDirectory() as td:
            source_dir = os.path.join(td, "source")
            workspace_dir = os.path.join(td, "workspace")
            trace_dir = os.path.join(td, "logs")
            os.makedirs(source_dir)

            doc_path = os.path.join(source_dir, "采购公告.docx")
            doc = Document()
            doc.add_paragraph("合成机构022有限公司")
            doc.add_paragraph("《采购公告》")
            doc.add_paragraph("项目名称：")
            doc.add_paragraph('合成项目006')
            doc.save(doc_path)

            result = main.run(
                f"请整理文件并归档 {doc_path}",
                planner_mode="rule",
                data_workspace_dir=workspace_dir,
                trace_dir=trace_dir,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["metadata"]["active_skill"], "data_cleaning_file_organization")
            self.assertEqual(result["rounds"][0]["action"], "prepare_file_organization_run")
            self.assertEqual(result["rounds"][0]["status"], "success")
            self.assertEqual(result["metadata"]["data_workspace_dir"], workspace_dir)
            self.assertTrue(os.path.isdir(os.path.join(workspace_dir, "runs")))
            self.assertTrue(os.path.isdir(os.path.join(workspace_dir, "项目文件")))
            self.assertTrue(os.path.isdir(trace_dir))
            self.assertTrue(os.path.exists(doc_path), "prepare step must not move source files")
            self.assertEqual(result["metadata"]["deployment"]["deployment_mode"], "local")
            self.assertEqual(result["metadata"]["adapters"], {
                "document_store": "local",
                "structure_index": "disabled",
                "projection_writer": "filesystem",
            })
            self.assertNotIn("PROJECT_MANAGER_DATABASE_DSN", json.dumps(result))


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
            goal="合成项目004",
            system_prompt="你是合成项目004 Agent",
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
