#!/usr/bin/env python3
"""
Project Manager Agent — 主 Agent 执行器

本文件由 agent.md 触发后调用，通过 Kimi Work 的 PythonRun / Cron 执行。
不独立运行，不接受命令行参数。

架构：一个 LoopEngine，多业务 Skill，运行时只注册 active skill 的工具子集
  - 项目管理工具：ProjectTools, ArchiveTools, ReportTools, MigrateTools
  - 数据清洗工具：DataCleaningTools
  - 商机管理工具：OpportunityManagerTools
  - CloudCC/CRM 工具：CloudCCCrmTools（受控、默认 fake adapter）
  - bid_files 工具：（预留）

数据流：
  agent.md (触发) → main.run() → SkillRouter → ToolRegistry(active skill tools) → Planner → LoopEngine → 返回结果

只有一个 LoopEngine；SkillRouter 先选择业务域，Planner 只能在 active skill 暴露的工具内规划。
"""
import os, sys, json, importlib.util
from typing import Dict, List, Any, Optional

sys.path.insert(0, os.path.dirname(__file__))

from common import LoopEngine, ToolRegistry, build_react_prompt, APIAdapter

SKILL_TOOL_MAP = {
    "data_cleaning_file_organization": [
        "scan_raw_files",
        "extract_pdf",
        "extract_document",
        "classify_document",
        "batch_process",
        "save_structured",
        "process_documents_to_ledger",
        "import_project_detail_workbook",
        "generate_bid_progress_html",
        "update_project_ledger",
    ],
    "project_management": [
        "scan_projects",
        "read_project_record",
        "check_milestones",
        "check_deliverables",
        "archive_files",
        "migrate_project",
        "generate_bid_overview",
        "generate_project_overview",
        "generate_report",
        "write_response",
    ],
    "opportunity_management": [
        "scan_bid_notices",
        "parse_bid_notice",
        "check_duplicate",
        "generate_bid_context",
        "create_crm_suggestion",
    ],
    "cloudcc_crm": [
        "cloudcc_session_probe",
        "cloudcc_search_record",
        "cloudcc_duplicate_check",
        "cloudcc_prepare_opportunity_draft",
        "cloudcc_fill_draft_gated",
        "cloudcc_readback_record",
    ],
}

SKILL_DESCRIPTIONS = {
    "data_cleaning_file_organization": "处理 Word、PDF、XLSX、网页导出等资料，提取候选事实并更新项目总览账本。",
    "project_management": "项目进度、风险、归档、汇总和报告生成。",
    "opportunity_management": "招标公告解析、商机上下文生成和商机查重建议。",
    "cloudcc_crm": "CloudCC/CRM 查重、草稿准备、受控填写和回读校验。",
}

# ────────────────────────────────────────────
# 动态导入所有工具模块（避免模块名冲突）
# ────────────────────────────────────────────

_module_dir = os.path.dirname(__file__)
_work_dir = os.path.join(_module_dir, "..")

# 项目管理工具（已合并到 tools/ 下）
_pm_tools_spec = importlib.util.spec_from_file_location(
    "pm_tools", os.path.join(_module_dir, "tools", "project_tools.py")
)
_pm_tools_mod = importlib.util.module_from_spec(_pm_tools_spec)
_pm_tools_spec.loader.exec_module(_pm_tools_mod)

# 项目管理规划器（本地）
_pm_planner_spec = importlib.util.spec_from_file_location("pm_planner", os.path.join(_module_dir, "planner.py"))
_pm_planner_mod = importlib.util.module_from_spec(_pm_planner_spec)
_pm_planner_spec.loader.exec_module(_pm_planner_mod)

# 数据清洗工具（已合并到 tools/ 下）
_dc_tools_spec = importlib.util.spec_from_file_location(
    "dc_tools", os.path.join(_module_dir, "tools", "data_cleaning_tools.py")
)
_dc_tools_mod = importlib.util.module_from_spec(_dc_tools_spec)
_dc_tools_spec.loader.exec_module(_dc_tools_mod)

# 商机管理工具（已合并到 tools/ 下）
_om_tools_spec = importlib.util.spec_from_file_location(
    "om_tools", os.path.join(_module_dir, "tools", "opportunity_tools.py")
)
_om_tools_mod = importlib.util.module_from_spec(_om_tools_spec)
_om_tools_spec.loader.exec_module(_om_tools_mod)

# CloudCC/CRM 工具（受控工具域，默认不接真实浏览器）
_crm_tools_spec = importlib.util.spec_from_file_location(
    "crm_tools", os.path.join(_module_dir, "tools", "cloudcc_crm_tools.py")
)
_crm_tools_mod = importlib.util.module_from_spec(_crm_tools_spec)
_crm_tools_spec.loader.exec_module(_crm_tools_mod)


def _register_project_management_tools(reg: ToolRegistry) -> None:
    """Register project-management domain tools into the provided registry."""
    pt = _pm_tools_mod.ProjectTools()
    ar = _pm_tools_mod.ArchiveTools()
    rt = _pm_tools_mod.ReportTools()
    mt = _pm_tools_mod.MigrateTools()

    reg.register("scan_projects", "扫描三阶段目录返回项目列表", pt.scan_projects, {}, [])
    reg.register("read_project_record", "读取项目记录.md", pt.read_project_record,
                 {"project_name": {"type": "string"}}, ["project_name"])
    reg.register("check_milestones", "检查里程碑状态(逾期/到期)", pt.check_milestones,
                 {"project_name": {"type": "string"}, "days": {"type": "integer"}}, [])
    reg.register("check_deliverables", "核对交付物", pt.check_deliverables,
                 {"project_name": {"type": "string"}}, ["project_name"])
    reg.register("archive_files", "智能归档文件", ar.archive_files,
                 {"source_dir": {"type": "string"}}, [])
    reg.register("migrate_project", "迁移项目阶段", mt.migrate_project,
                 {"project_name": {"type": "string"}, "to_phase": {"type": "string"}, "update_status": {"type": "string"}},
                 ["project_name"])
    reg.register("generate_bid_overview", "生成投标进度总览", rt.generate_bid_overview,
                 {"output_file": {"type": "string"}}, [])
    reg.register("generate_project_overview", "生成全局项目总览", rt.generate_project_overview,
                 {"phase": {"type": "string"}}, [])
    reg.register("generate_report", "生成项目报告", rt.generate_report,
                 {"project_name": {"type": "string"}, "report_type": {"type": "string"}}, ["project_name"])
    reg.register("write_response", "写入响应文件", pt.write_response,
                 {"content": {"type": "string"}, "filename": {"type": "string"}}, ["content", "filename"])


def _register_data_cleaning_file_organization_tools(reg: ToolRegistry) -> None:
    """Register data-cleaning and project-ledger tools into the provided registry."""
    dt = _dc_tools_mod.DataCleaningTools(workspace_dir=os.path.join(_module_dir, "state"))

    reg.register("scan_raw_files", "扫描原始文件目录", dt.scan_raw_files, {}, [])
    reg.register("extract_pdf", "提取PDF结构化数据", dt.extract_pdf,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("extract_document", "提取 Word/PDF/图片文件的结构化数据", dt.extract_document,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("classify_document", "根据文件名/内容分类文档", dt.classify_document,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("batch_process", "批量处理目录文件", dt.batch_process,
                 {"source_dir": {"type": "string"}}, [])
    reg.register("save_structured", "保存结构化数据", dt.save_structured,
                 {"data": {"type": "object"}, "output_path": {"type": "string"}}, ["data", "output_path"])
    reg.register("process_documents_to_ledger", "读取多个源文件，输出结构化提取结果并更新项目总览账本",
                 dt.process_documents_to_ledger,
                 {
                     "file_paths": {"type": "array"},
                     "project_name": {"type": "string"},
                 }, ["file_paths"])
    reg.register("import_project_detail_workbook", "读取项目明细表.xlsx，标准化项目字段并更新多个项目总览账本",
                 dt.import_project_detail_workbook,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("generate_bid_progress_html", "从项目账本生成投标进度总览 HTML 展示页",
                 dt.generate_bid_progress_html,
                 {"output_file": {"type": "string"}}, [])
    reg.register("update_project_ledger", "数据清洗及文件整理：将候选事实写入项目总览.md账本并执行规则决议",
                 dt.update_project_ledger,
                 {
                     "project_name": {"type": "string"},
                     "facts": {"type": "object"},
                     "evidence": {"type": "array"},
                     "source_type": {"type": "string"},
                 }, [])


def _register_opportunity_management_tools(reg: ToolRegistry) -> None:
    """Register opportunity-management tools into the provided registry."""
    ot = _om_tools_mod.OpportunityManagerTools()

    reg.register("scan_bid_notices", "扫描招标公告目录", ot.scan_bid_notices, {}, [])
    reg.register("parse_bid_notice", "解析招标公告提取关键字段", ot.parse_bid_notice,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("check_duplicate", "检测商机是否重复", ot.check_duplicate,
                 {"project_code": {"type": "string"}}, ["project_code"])
    reg.register("generate_bid_context", "生成 bid_context.json", ot.generate_bid_context,
                 {"parsed_data": {"type": "object"}}, ["parsed_data"])
    reg.register("create_crm_suggestion", "生成 CRM 录入建议", ot.create_crm_suggestion,
                 {"bid_context": {"type": "object"}}, ["bid_context"])


def _register_cloudcc_crm_tools(reg: ToolRegistry) -> None:
    """Register controlled CloudCC/CRM tools into the provided registry."""
    crm = _crm_tools_mod.CloudCCCrmTools()

    reg.register("cloudcc_session_probe", "检查 CloudCC 登录态和浏览器适配器可用性", crm.cloudcc_session_probe, {}, [])
    reg.register("cloudcc_search_record", "只读查询 CloudCC CRM 对象记录", crm.cloudcc_search_record,
                 {"object_type": {"type": "string"}, "query": {"type": "string"}}, ["object_type", "query"])
    reg.register("cloudcc_duplicate_check", "基于证据检查 CloudCC 商机是否重复", crm.cloudcc_duplicate_check,
                 {
                     "project_code": {"type": "string"},
                     "project_name": {"type": "string"},
                     "customer": {"type": "string"},
                 }, [])
    reg.register("cloudcc_prepare_opportunity_draft", "根据 bid_context 准备 CRM 商机草稿（不写入）",
                 crm.cloudcc_prepare_opportunity_draft,
                 {"bid_context": {"type": "object"}}, ["bid_context"])
    reg.register("cloudcc_fill_draft_gated", "受控填充 CRM 草稿并在提交前停止", crm.cloudcc_fill_draft_gated,
                 {"draft": {"type": "object"}}, ["draft"])
    reg.register("cloudcc_readback_record", "提交后回读 CloudCC CRM 记录并校验字段", crm.cloudcc_readback_record,
                 {"record_id": {"type": "string"}, "record_url": {"type": "string"}}, [])


SKILL_REGISTRARS = {
    "data_cleaning_file_organization": _register_data_cleaning_file_organization_tools,
    "project_management": _register_project_management_tools,
    "opportunity_management": _register_opportunity_management_tools,
    "cloudcc_crm": _register_cloudcc_crm_tools,
}


# ────────────────────────────────────────────
# 工具注册表（debug/all-tools 与 active-skill 两条构建路径）
# ────────────────────────────────────────────

def _build_registry() -> ToolRegistry:
    """构建 debug/all-tools 注册表，仅用于治理校验和调试。"""
    reg = ToolRegistry()
    for register_tools in SKILL_REGISTRARS.values():
        register_tools(reg)
    return reg


def _route_skill(goal: str) -> str:
    """Level 0 progressive disclosure: choose the active skill from the user goal."""
    if any(k in goal for k in ["数据清洗及文件整理", "项目总览", "项目账本", "ledger", "扫描文件", "解析文档", "PDF", "OCR"]):
        return "data_cleaning_file_organization"
    if any(k in goal for k in ["CloudCC", "cloudcc", "CRM", "crm"]):
        return "cloudcc_crm"
    if any(k in goal for k in ["招标公告", "招标", "商机", "检测商机", "重复检查"]):
        return "opportunity_management"
    return "project_management"


def _build_registry_for_skill(skill_name: str) -> ToolRegistry:
    """Level 1/2 progressive disclosure: expose only tools owned by the active skill."""
    register_tools = SKILL_REGISTRARS.get(skill_name)
    if register_tools is None:
        raise ValueError(f"Unknown skill: {skill_name}")

    reg = ToolRegistry()
    register_tools(reg)
    expected = SKILL_TOOL_MAP[skill_name]
    actual = reg.list_tools()
    if actual != expected:
        raise ValueError(f"Skill '{skill_name}' registry drift: expected {expected}, got {actual}")
    return reg


def _skills_prompt_text() -> str:
    """Compact Level 0 skill index for docs/debug prompts."""
    lines = ["## Available Skills"]
    for name, description in SKILL_DESCRIPTIONS.items():
        lines.append(f"- {name}: {description}")
    return "\n".join(lines)


def _make_api_llm():
    """构建真实 LLM 适配器。需要环境变量 LLM_API_KEY。"""
    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL", "http://172.18.125.202:9990/v1")
    model = os.getenv("LLM_MODEL", "minimax-m3-mxfp8")
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY 未设置。如需真实 LLM 驱动，请设置环境变量；"
            "如需测试，请使用 planner_mode='rule'。"
        )
    return APIAdapter(
        model_name=model,
        api_key=api_key,
        base_url=base_url,
        provider="openai",
    )


def run(goal: str, planner_mode: str = "auto") -> Dict[str, Any]:
    """
    Agent 执行器入口。由 agent.md 触发后调用。

    Args:
        goal: 用户原始请求文本
        planner_mode: 规划器模式
            - "auto"（默认）：检测 LLM_API_KEY 环境变量，有则用 LLMPlanner，无则用 RuleBasedPlanner
            - "llm"：强制使用 LLM 实时规划（需要 LLM_API_KEY）
            - "rule"：强制使用规则规划器（测试/演示用，无需 API key）

    Returns:
        LoopTrace 的执行摘要，包含状态、轮数、结果、token 消耗等。
    """
    print(f"\n{'='*60}")
    print(f"🎯 Project Manager Agent 执行器（主 Agent）")
    print(f"Goal: {goal}")
    print(f"Planner: {planner_mode}")
    active_skill = _route_skill(goal)
    print(f"Active Skill: {active_skill}")
    print(f"Disclosure: progressive")
    print(f"{'='*60}")

    # 1. 渐进式披露：先路由 Skill，再只暴露该 Skill 的工具。
    reg = _build_registry_for_skill(active_skill)
    memory_text = _skills_prompt_text() + f"\n\nActive Skill: {active_skill}\n"
    sys_prompt = build_react_prompt(goal=goal, tools_text=reg.to_prompt_text(), memory_text=memory_text)

    # 2. 初始化 Planner
    has_api_key = bool(os.getenv("LLM_API_KEY"))
    if planner_mode == "llm" or (planner_mode == "auto" and has_api_key):
        adapter = _make_api_llm()
        planner = _pm_planner_mod.LLMPlanner(goal=goal, llm_adapter=adapter, tools_registry=reg)
        print(f"🧠 Planner: LLMPlanner (model={adapter.model_name})")
    else:
        planner = _pm_planner_mod.RuleBasedPlanner(goal=goal, tools_registry=reg)
        print(f"🧠 Planner: RuleBasedPlanner (fallback, no API key)")

    # 3. LoopEngine 的 LLM 调用委托给 Planner
    def llm_call(msgs, tools):
        return planner.get_response(msgs)

    # 4. 运行循环
    trace_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(trace_dir, exist_ok=True)
    engine = LoopEngine(
        agent_name="project_manager_agent",
        max_rounds=15,
        dedup_threshold=2,
        token_budget=8000,
        state_mode="sliding_window",
        sliding_window_size=3,
        retry_max=2,
        trace_dir=trace_dir,
    )

    trace = engine.run(
        goal=goal,
        system_prompt=sys_prompt,
        llm_call=llm_call,
        tools={name: reg[name].func for name in reg.list_tools()},
    )
    trace.metadata["active_skill"] = active_skill
    trace.metadata["disclosure_mode"] = "progressive"
    trace.metadata["exposed_tools"] = reg.list_tools()

    trace.save(os.path.join(trace_dir, f"project_manager_{trace.trace_id}.json"))
    trace.print_summary()
    return trace.to_dict()


if __name__ == "__main__":
    print(
        "\n❌ Project Manager Agent 不接受命令行参数。\n"
        "本文件由 agent.md 触发后通过 Kimi Work 调用。\n"
        "\n"
        "正确用法（PythonRun）：\n"
        "  from main import run\n"
        "  result = run('今天有什么风险项目')\n"
        "\n"
        "正确用法（Cron）：\n"
        "  配置 execution.kind='local_conversation' 触发本 workspace。\n"
    )
    sys.exit(1)
