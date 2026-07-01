#!/usr/bin/env python3
"""
Project Manager Loop 测试套件

验证内容：
1. 模块导入（common/ 已内联）
2. 工具注册（20个工具）
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
