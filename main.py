#!/usr/bin/env python3
"""
Project Manager Agent — 主 Agent 执行器

本文件由 agent.md 触发后调用，通过 Kimi Work 的 PythonRun / Cron 执行。
不独立运行，不接受命令行参数。

架构：主 Agent 统一调度，所有工具注册到同一 ToolRegistry
  - 项目管理工具：ProjectTools, ArchiveTools, ReportTools, MigrateTools
  - 数据清洗工具：DataCleaningTools
  - 商机管理工具：OpportunityManagerTools
  - bid_files 工具：（预留）

数据流：
  agent.md (触发) → main.run() → Planner → LoopEngine → ToolRegistry(所有工具) → 返回结果

只有一个 LoopEngine，Planner 根据用户意图从所有可用工具中选择调用链。
"""
import os, sys, json, importlib.util
from typing import Dict, List, Any, Optional

sys.path.insert(0, os.path.dirname(__file__))

from common import LoopEngine, ToolRegistry, build_react_prompt, APIAdapter

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


# ────────────────────────────────────────────
# 工具注册表（合并所有子 Skill 的工具）
# ────────────────────────────────────────────

def _build_registry() -> ToolRegistry:
    """构建并注册所有可用工具（主 Agent + 三个子 Skill）。"""
    reg = ToolRegistry()

    # ─── 项目管理工具 ───
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

    # ─── 数据清洗工具 ───
    dt = _dc_tools_mod.DataCleaningTools()

    reg.register("scan_raw_files", "扫描原始文件目录", dt.scan_raw_files, {}, [])
    reg.register("extract_pdf", "提取PDF结构化数据", dt.extract_pdf,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("classify_document", "根据文件名/内容分类文档", dt.classify_document,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("batch_process", "批量处理目录文件", dt.batch_process,
                 {"source_dir": {"type": "string"}}, [])
    reg.register("save_structured", "保存结构化数据", dt.save_structured,
                 {"data": {"type": "object"}, "output_path": {"type": "string"}}, ["data", "output_path"])

    # ─── 商机管理工具 ───
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

    return reg


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
    print(f"Registered Tools: {len(_build_registry().list_tools())}")
    print(f"{'='*60}")

    # 1. 工具注册表（合并所有子 Skill）
    reg = _build_registry()
    sys_prompt = build_react_prompt(goal=goal, tools_text=reg.to_prompt_text(), memory_text="")

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
