"""
Planner — 规划层

将用户意图转化为 ReAct 格式的决策响应。
支持两种模式：
1. LLMPlanner：调用真实 LLM，每轮根据 Observation 实时决策下一步（只看到 active skill 暴露的工具）
2. RuleBasedPlanner：基于规则的 fallback，根据已执行工具和历史动态决策

职责边界：
- 不直接执行工具（那是 LoopEngine 的事）
- 只生成 Thought/Action/Action Input 格式的文本，由 LoopEngine 解析并执行

当前支持的意图领域：
  A. 项目管理：风险检查、进度查询、归档、投标总览、项目总览、报告生成
  B. 数据清洗：文件扫描、PDF提取、文档分类、批量处理
  C. 商机管理：招标公告扫描、解析、重复检测、CRM建议
  D. CloudCC/CRM：登录态探针、只读查重、商机草稿、提交前确认
"""
import os
import re
import json
from typing import List, Dict, Any, Optional, Tuple

from skill_policies import get_policy


class BasePlanner:
    """规划器基类
    
    每个规划器实现必须提供：
    - get_response(messages: List[Dict]) -> Tuple[str, int]
      返回 (ReAct 格式响应文本, token_cost)
    """
    
    def __init__(self, goal: str, tools_registry=None):
        self.goal = goal
        self.tools = tools_registry
        self._round_count = 0
    
    def get_response(self, messages: List[Dict]) -> Tuple[str, int]:
        raise NotImplementedError
    
    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数"""
        cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        en_words = len(re.findall(r'[a-zA-Z]+', text))
        return max(50, int(cn_chars * 0.5 + en_words * 0.3))
    
    def _extract_executed_tools(self, messages: List[Dict]) -> List[str]:
        """从消息历史中提取已执行的工具名"""
        executed = []
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                m = re.search(r'Action:\s*(\w+)', content, re.IGNORECASE)
                if m and m.group(1) != "final_answer":
                    executed.append(m.group(1))
        return executed
    
    def _extract_last_observation(self, messages: List[Dict]) -> Optional[str]:
        """提取最近一次的 Observation 内容"""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if content.startswith("Observation:"):
                    return content[len("Observation:"):].strip()
        return None

    def _extract_last_observation_evaluation(self, messages: List[Dict]) -> Optional[Dict[str, Any]]:
        """提取最近一次结构化 Observation Evaluation。"""
        prefix = "Observation Evaluation:"
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if content.startswith(prefix):
                    raw = content[len(prefix):].strip()
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        return None
                    if isinstance(parsed, dict):
                        return parsed
                    return None
        return None
    
    def _has_error_in_obs(self, obs: str) -> bool:
        """检查观察结果是否包含明确的错误信息。
        注意：'failed: 0' 不算错误，只匹配 'error'、'not found'、'未找到'。"""
        if not obs:
            return False
        return any(k in obs.lower() for k in ["error", "not found", "未找到"])


class LLMPlanner(BasePlanner):
    """LLM 实时规划器
    
    每轮调用真实 LLM，让它根据当前上下文（包括所有 Observation）自主决定下一步。
    这是 Agent-Loop 中"真正的规划层"——LLM 看到 Observation 后再思考，不是预编排。
    
    注意：LLM 看到的是 active skill 的工具 prompt（通过 build_react_prompt 传入），
    跨业务域切换必须先经过 SkillRouter，而不是在单轮里暴露全部工具。
    """
    
    def __init__(self, goal: str, llm_adapter, tools_registry=None):
        super().__init__(goal, tools_registry)
        self.llm = llm_adapter
    
    def get_response(self, messages: List[Dict]) -> Tuple[str, int]:
        """调用 LLM，让它根据完整上下文决策下一步"""
        try:
            r = self.llm.chat(messages, None)  # ReAct 文本模式
            return r.text, r.tokens_used
        except Exception as e:
            return f"Final Answer: [LLM ERROR] {str(e)}", 0


class RuleBasedPlanner(BasePlanner):
    """基于规则的规划器（fallback / 测试用）
    
    不是预定义整个序列，而是每轮根据"已执行了什么"和"上一轮的 Observation"动态决策。
    与旧版 _plan_steps 的区别：
    - 旧版：循环开始前就预定了 [step1, step2, step3, final]
    - 新版：每轮根据 executed_tools + last_observation 实时判断下一步
    """
    
    def __init__(self, goal: str, tools_registry=None):
        super().__init__(goal, tools_registry)
    
    def get_response(self, messages: List[Dict]) -> Tuple[str, int]:
        executed = self._extract_executed_tools(messages)
        last_obs = self._extract_last_observation(messages)
        observation_evaluation = self._extract_last_observation_evaluation(messages)

        if observation_evaluation:
            status = observation_evaluation.get("status")
            if status == "needs_confirmation" or observation_evaluation.get("needs_confirmation"):
                detail = observation_evaluation.get("error_code") or observation_evaluation.get("summary") or "上一步工具返回 needs_confirmation"
                text = f"Final Answer: 当前步骤需要人工确认后才能继续；请确认后再触发后续动作。控制信号：{detail}"
                return text, self._estimate_tokens(text)
            if status in {"blocked", "failed"}:
                detail = observation_evaluation.get("error_code") or observation_evaluation.get("summary") or json.dumps(observation_evaluation, ensure_ascii=False)
                return self._handle_error_with_tokens(executed, detail)
        
        # 如果上一轮出错，尝试跳过或返回结论
        if last_obs and self._has_error_in_obs(last_obs):
            return self._handle_error_with_tokens(executed, last_obs)
        
        # 根据 goal 意图和已执行历史，路由到对应规划逻辑
        response = self._route(executed, last_obs)
        tokens = self._estimate_tokens(response)
        return response, tokens
    
    def _route(self, executed: List[str], last_obs: Optional[str]) -> str:
        """根据用户意图和已执行历史，选择规划路径"""
        goal = self.goal
        
        # ─── 意图 B：数据清洗 ───
        if any(k in goal for k in ["数据清洗及文件整理", "项目总览", "项目账本", "ledger", "整理文件", "文件整理", "归档文件", "人工复核"]):
            return self._plan_with_policy("data_cleaning_file_organization", executed, last_obs)

        if any(k in goal for k in ["提取", "OCR", "解析文档", "PDF", "扫描文件", "查看有哪些文件"]):
            return self._plan_data_cleaning(executed, last_obs)
        
        if any(k in goal for k in ["批量", "处理", "全部", "所有"]):
            return self._plan_batch_cleaning(executed, last_obs)
        
        if any(k in goal for k in ["分类", "整理文档", "归档原始"]):
            return self._plan_classify(executed, last_obs)
        
        # ─── 意图 D：CloudCC/CRM 受控工具域 ───
        if any(k in goal for k in ["CloudCC", "cloudcc", "CRM", "crm"]):
            return self._plan_with_policy("cloudcc_crm", executed, last_obs)

        # ─── 意图 C：商机管理 ───
        if any(k in goal for k in ["招标公告", "招标", "商机", "检测商机", "重复检查"]):
            return self._plan_opportunity(executed, last_obs)
        
        if any(k in goal for k in ["CRM", "录入", "建议"]):
            return self._plan_crm(executed, last_obs)
        
        if any(k in goal for k in ["立项", "新机会", "发现商机"]):
            return self._plan_new_opportunity(executed, last_obs)
        
        # ─── 意图 A：项目管理 ───
        if any(k in goal for k in ["风险", "今天", "到期", "下周"]):
            return self._plan_risk(executed, last_obs)
        
        if "投标" in goal and "进度" in goal:
            return self._plan_bid_overview(executed, last_obs)
        
        if "进度" in goal or "怎么样" in goal:
            return self._plan_progress(executed, last_obs)
        
        if "归档" in goal or "整理" in goal:
            return self._plan_archive(executed, last_obs)
        
        if "总览" in goal or "所有项目" in goal:
            return self._plan_overview(executed, last_obs)
        
        if "周报" in goal or "报告" in goal:
            return self._plan_report(executed, last_obs)
        
        # 默认 fallback
        return "Final Answer: 请明确需求：项目管理（风险/进度/归档/报告）、数据清洗（提取/分类/批量）、商机管理（招标/检测）、CloudCC/CRM（查重/草稿/确认）。"

    def _plan_with_policy(self, skill_name: str, executed: List[str], last_obs: Optional[str]) -> str:
        policy = get_policy(skill_name)
        if policy is None:
            return f"Final Answer: 未找到 skill policy：{skill_name}"
        decision = policy.plan(self.goal, executed, last_obs)
        if decision is None:
            return f"Final Answer: {skill_name} 没有可执行的下一步。"
        return decision.text
    
    def _handle_error(self, executed: List[str], last_obs: str) -> str:
        """上一轮出错时的应急策略。返回 ReAct 格式的响应字符串。"""
        if "scan_projects" in executed and "check_milestones" not in executed:
            return f"Thought: 扫描时遇到问题，尝试直接检查里程碑。\nAction: check_milestones\nAction Input: {{}}"
        return f"Thought: 遇到错误，尝试总结已知信息。\nFinal Answer: 执行过程中遇到问题：{last_obs[:200]}"

    def _handle_error_with_tokens(self, executed: List[str], last_obs: str) -> Tuple[str, int]:
        """上一轮出错时的应急策略。返回 (text, tokens) 元组，供 get_response 直接返回。"""
        text = self._handle_error(executed, last_obs)
        return text, self._estimate_tokens(text)
    
    # ─────────── 意图 A：项目管理 ───────────
    
    def _plan_risk(self, executed: List[str], last_obs: Optional[str]) -> str:
        if "scan_projects" not in executed:
            return "Thought: 用户要求检查风险项目，先扫描所有项目。\nAction: scan_projects\nAction Input: {}"
        if "check_milestones" not in executed:
            return "Thought: 已获取项目列表，检查所有里程碑的到期状态。\nAction: check_milestones\nAction Input: {}"
        if "write_response" not in executed:
            return "Thought: 风险检查完成，生成风险摘要报告。\nAction: write_response\nAction Input: {\"content\": \"# 风险预警\\n\\n已扫描所有项目，识别逾期和即将到期项。\", \"filename\": \"risk_summary.md\"}"
        return "Final Answer: 风险检查完成，报告已保存到 state/risk_summary.md。"
    
    def _plan_bid_overview(self, executed: List[str], last_obs: Optional[str]) -> str:
        if "generate_bid_overview" not in executed:
            return "Thought: 用户要求生成投标进度总览。\nAction: generate_bid_overview\nAction Input: {}"
        return "Final Answer: 投标进度总览已生成。"
    
    def _plan_progress(self, executed: List[str], last_obs: Optional[str]) -> str:
        proj = re.search(r'([\u4e00-\u9fff]{4,})', self.goal)
        proj_name = proj.group(1) if proj else ""
        if not proj_name:
            return "Final Answer: 请提供具体项目名称以查询进度。"
        
        if "read_project_record" not in executed:
            return f"Thought: 查询 {proj_name} 的项目进度。\nAction: read_project_record\nAction Input: {{\"project_name\": \"{proj_name}\"}}"
        if "check_milestones" not in executed:
            return "Thought: 已读取项目记录，检查里程碑状态。\nAction: check_milestones\nAction Input: {}"
        return f"Final Answer: {proj_name} 的项目进度查询完成。"
    
    def _plan_archive(self, executed: List[str], last_obs: Optional[str]) -> str:
        if "archive_files" not in executed:
            return "Thought: 用户要求归档散落文件。\nAction: archive_files\nAction Input: {}"
        if "migrate_project" not in executed:
            return "Thought: 归档完成，检测是否需要状态迁移。\nAction: migrate_project\nAction Input: {\"project_name\": \"检测\", \"update_status\": \"检查\"}"
        return "Final Answer: 归档完成。"
    
    def _plan_overview(self, executed: List[str], last_obs: Optional[str]) -> str:
        if "generate_project_overview" not in executed:
            return "Thought: 生成全局项目总览。\nAction: generate_project_overview\nAction Input: {}"
        return "Final Answer: 项目总览已生成。"
    
    def _plan_report(self, executed: List[str], last_obs: Optional[str]) -> str:
        proj = re.search(r'([\u4e00-\u9fff]{4,})', self.goal)
        proj_name = proj.group(1) if proj else ""
        if not proj_name:
            return "Final Answer: 请提供具体项目名称以生成报告。"
        
        if "generate_report" not in executed:
            return f"Thought: 生成 {proj_name} 的周报。\nAction: generate_report\nAction Input: {{\"project_name\": \"{proj_name}\", \"report_type\": \"weekly\"}}"
        return "Final Answer: 报告已生成。"
    
    # ─────────── 意图 B：数据清洗 ───────────
    
    def _plan_data_cleaning(self, executed: List[str], last_obs: Optional[str]) -> str:
        """处理文件扫描、提取、分类"""
        path = self._extract_file_path()
        
        if path and "extract_pdf" not in executed:
            return f"Thought: 提取文件结构化数据。\nAction: extract_pdf\nAction Input: {{\"file_path\": \"{path}\"}}"
        if path and "classify_document" not in executed:
            return f"Thought: 对文件进行分类。\nAction: classify_document\nAction Input: {{\"file_path\": \"{path}\"}}"
        if "scan_raw_files" not in executed:
            return "Thought: 未指定文件或文件已处理，先扫描目录。\nAction: scan_raw_files\nAction Input: {}"
        return "Final Answer: 数据清洗操作完成。"
    
    def _plan_batch_cleaning(self, executed: List[str], last_obs: Optional[str]) -> str:
        if "batch_process" not in executed:
            return "Thought: 批量处理所有原始文件。\nAction: batch_process\nAction Input: {}"
        return "Final Answer: 批量处理完成。"
    
    def _plan_classify(self, executed: List[str], last_obs: Optional[str]) -> str:
        path = self._extract_file_path()
        if path and "classify_document" not in executed:
            return f"Thought: 对文件进行分类归档。\nAction: classify_document\nAction Input: {{\"file_path\": \"{path}\"}}"
        return "Final Answer: 分类完成。"

    # ─────────── 意图 C：商机管理 ───────────
    
    def _plan_opportunity(self, executed: List[str], last_obs: Optional[str]) -> str:
        """招标公告扫描、解析、重复检测"""
        path = self._extract_file_path()
        
        if path and "parse_bid_notice" not in executed:
            return f"Thought: 解析招标公告提取关键字段。\nAction: parse_bid_notice\nAction Input: {{\"file_path\": \"{path}\"}}"
        if "check_duplicate" not in executed and last_obs and "project_code" in last_obs:
            code = self._extract_project_code(last_obs)
            if code:
                return f"Thought: 检测商机是否重复。\nAction: check_duplicate\nAction Input: {{\"project_code\": \"{code}\"}}"
        if "scan_bid_notices" not in executed:
            return "Thought: 扫描招标公告目录。\nAction: scan_bid_notices\nAction Input: {}"
        return "Final Answer: 商机检测完成。"
    
    def _plan_crm(self, executed: List[str], last_obs: Optional[str]) -> str:
        if "create_crm_suggestion" not in executed:
            return "Thought: 生成 CRM 录入建议。\nAction: create_crm_suggestion\nAction Input: {\"bid_context\": {}}"
        return "Final Answer: CRM 建议已生成。"

    def _plan_new_opportunity(self, executed: List[str], last_obs: Optional[str]) -> str:
        path = self._extract_file_path()
        if path and "parse_bid_notice" not in executed:
            return f"Thought: 解析新招标公告。\nAction: parse_bid_notice\nAction Input: {{\"file_path\": \"{path}\"}}"
        if "generate_bid_context" not in executed and last_obs and "fields" in last_obs:
            return "Thought: 生成标准化的 bid_context.json。\nAction: generate_bid_context\nAction Input: {\"parsed_data\": {}}"
        return "Final Answer: 新机会处理完成。"
    
    # ─────────── 辅助方法 ───────────
    
    def _extract_file_path(self) -> Optional[str]:
        """从 goal 中提取可能的文件路径"""
        paths = self._extract_file_paths()
        if paths:
            return paths[0]
        m = re.search(r'([\w\-\u4e00-\u9fff]+\.\w+)', self.goal)
        if m:
            return m.group(1)
        return None

    def _extract_file_paths(self) -> List[str]:
        """从 goal 中提取一个或多个 Windows/Unix 文件路径"""
        pattern = r'(?:[A-Za-z]:\\[^"\'\s]+|[\/~][^"\'\s]+)\.(?:docx|pdf|xlsx|xls|md|html|png|jpg|jpeg)'
        return re.findall(pattern, self.goal, flags=re.IGNORECASE)
    
    def _extract_project_code(self, obs: Optional[str]) -> Optional[str]:
        """从 Observation 中提取招标编号"""
        if not obs:
            return None
        m = re.search(r'project_code["\']?\s*[:\s]+["\']?([A-Z0-9\-]+)', obs)
        if m:
            return m.group(1)
        return None
