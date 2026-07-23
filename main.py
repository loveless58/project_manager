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

from common import LoopEngine, ToolRegistry, build_react_prompt, APIAdapter, resolve_workspace_config
from loop_packages import build_skill_descriptions, build_skill_registrar_map, build_skill_tool_map
from platform_core import load_app_settings
from platform_core.composition import build_runtime_adapters

SKILL_TOOL_MAP = build_skill_tool_map()
SKILL_DESCRIPTIONS = build_skill_descriptions()

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


def _register_data_cleaning_file_organization_tools(reg: ToolRegistry, workspace_dir: Optional[str] = None) -> None:
    """Register data-cleaning and project-ledger tools into the provided registry."""
    dt = _dc_tools_mod.DataCleaningTools(workspace_dir=workspace_dir)

    reg.register("scan_raw_files", "扫描原始文件目录", dt.scan_raw_files, {}, [])
    reg.register("extract_pdf", "提取PDF结构化数据", dt.extract_pdf,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("extract_document", "提取 Word/PDF/图片文件的结构化数据", dt.extract_document,
                 {"file_path": {"type": "string"}}, ["file_path"])
    reg.register("run_ocr", "对图片或扫描 PDF 执行 OCR，输出 ocr.result.v1，不写入账本",
                 dt.run_ocr,
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
    reg.register("extract_structured_business_output", "只读提取文件结构化业务证据：输出 run 级 JSON，不写账本、不生成归档计划、不移动文件",
                 dt.extract_structured_business_output,
                 {
                     "file_paths": {"type": "array"},
                     "source_dir": {"type": "string"},
                     "project_name": {"type": "string"},
                     "output_dir": {"type": "string"},
                     "skip_backups": {"type": "boolean"},
                 }, [])
    reg.register("semantic_structure_document", "只读语义结构化 evidence pack：输出 semantic_document.v1，不写账本、不生成归档计划、不移动文件",
                 dt.semantic_structure_document,
                 {
                     "evidence_pack": {"type": "object"},
                 }, ["evidence_pack"])
    reg.register("prepare_file_organization_run", "准备文件整理闭环运行包：内部已完成结构化提取+分类+归档计划，返回待确认清单。注意：不要额外调用 extract_document，此工具已包含提取。",
                 dt.prepare_file_organization_run,
                 {
                     "file_paths": {"type": "array"},
                     "project_name": {"type": "string"},
                  }, ["file_paths"])
    reg.register("verify_file_organization_run", "只读验证已生成的文件整理运行包：审查提取字段、OCR质量和归档计划，写入 adversarial_verification.json，不移动文件、不写账本",
                 dt.verify_file_organization_run,
                 {"run_id": {"type": "string"}}, ["run_id"])
    reg.register("audit_file_organization_run", "只读审计已生成并验证的文件整理运行包：检查必需 artifact、验证报告和人工确认状态，写入 audit_review.json，不移动文件、不写账本",
                 dt.audit_file_organization_run,
                 {"run_id": {"type": "string"}}, ["run_id"])
    reg.register("prepare_feedback_form", "为文件整理 run 生成可编辑反馈表 feedback_form.json 和人读 Markdown；不移动文件、不写账本",
                 dt.prepare_feedback_form,
                 {"run_id": {"type": "string"}}, ["run_id"])
    reg.register("apply_feedback_form", "应用已填写的 feedback_form.json，转换为标准反馈并可生成候选测试草案；不执行归档",
                 dt.apply_feedback_form,
                 {
                     "run_id": {"type": "string"},
                     "feedback_form": {"type": "object"},
                     "feedback_form_path": {"type": "string"},
                     "generate_tests": {"type": "boolean"},
                 }, ["run_id"])
    reg.register("apply_feedback_decisions", "标准化并落盘人工批量反馈，生成 feedback_events、rule_candidates 和 parser_test_candidates；不移动文件、不写账本、不执行归档",
                 dt.apply_feedback_decisions,
                 {
                     "run_id": {"type": "string"},
                     "feedback_decisions": {"type": "array"},
                 }, ["run_id", "feedback_decisions"])
    reg.register("generate_candidate_tests", "将人工反馈生成的 parser/rule candidates 转成 run 包内可运行的测试草案；不修改仓库 tests",
                 dt.generate_candidate_tests,
                 {"run_id": {"type": "string"}}, ["run_id"])
    reg.register("apply_human_review", "应用人工复核修正，将复核结果写入项目总览账本并生成规则候选",
                 dt.apply_human_review,
                 {
                     "review_decisions": {"type": "array"},
                     "run_id": {"type": "string"},
                 }, ["review_decisions"])
    reg.register("execute_archive_plan", "确认后执行归档计划：重命名移动原文件并更新项目总览账本",
                 dt.execute_archive_plan,
                 {
                     "run_id": {"type": "string"},
                     "confirmed": {"type": "boolean"},
                 }, ["run_id"])
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


SKILL_REGISTRARS = build_skill_registrar_map(sys.modules[__name__])


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
    from loop_packages import route_skill_from_packages

    return route_skill_from_packages(goal)


def _build_registry_for_skill(skill_name: str, data_workspace_dir: Optional[str] = None) -> ToolRegistry:
    """Level 1/2 progressive disclosure: expose only tools owned by the active skill."""
    register_tools = SKILL_REGISTRARS.get(skill_name)
    if register_tools is None:
        raise ValueError(f"Unknown skill: {skill_name}")

    reg = ToolRegistry()
    if skill_name == "data_cleaning_file_organization":
        register_tools(reg, workspace_dir=data_workspace_dir)
    else:
        register_tools(reg)
    expected = _expected_tools_for_skill(skill_name)
    actual = reg.list_tools()
    if actual != expected:
        raise ValueError(f"Skill '{skill_name}' registry drift: expected {expected}, got {actual}")
    return reg


def _expected_tools_for_skill(skill_name: str) -> List[str]:
    """Resolve runtime tool contract from a loop package when one exists."""
    try:
        from loop_packages import get_loop_package

        return get_loop_package(skill_name).expected_tools
    except KeyError:
        return SKILL_TOOL_MAP[skill_name]


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


def run(
    goal: str,
    planner_mode: str = "auto",
    data_workspace_dir: Optional[str] = None,
    trace_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Agent 执行器入口。由 agent.md 触发后调用。

    Args:
        goal: 用户原始请求文本
        planner_mode: 规划器模式
            - "auto"（默认）：检测 LLM_API_KEY 环境变量，有则用 LLMPlanner，无则用 RuleBasedPlanner
            - "llm"：强制使用 LLM 实时规划（需要 LLM_API_KEY）
            - "rule"：强制使用规则规划器（测试/演示用，无需 API key）
        data_workspace_dir: 可选。数据清洗及文件整理 skill 的隔离工作区。
            不传时沿用项目内 state/ 默认目录。
        trace_dir: 可选。Loop trace 输出目录。不传时沿用项目内 logs/ 默认目录。

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

    app_settings = load_app_settings()
    runtime_adapters = build_runtime_adapters(app_settings)
    workspace_config = resolve_workspace_config(app_settings=app_settings)
    effective_data_workspace_dir = data_workspace_dir
    if active_skill == "data_cleaning_file_organization" and effective_data_workspace_dir is None:
        effective_data_workspace_dir = str(workspace_config.data_cleaning_workspace)

    # 1. 渐进式披露：先路由 Skill，再只暴露该 Skill 的工具。
    reg = _build_registry_for_skill(
        active_skill,
        data_workspace_dir=effective_data_workspace_dir,
    )
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
    actual_trace_dir = trace_dir or str(workspace_config.logs_dir)
    os.makedirs(actual_trace_dir, exist_ok=True)
    engine = LoopEngine(
        agent_name="project_manager_agent",
        max_rounds=15,
        dedup_threshold=2,
        token_budget=20000,
        state_mode="sliding_window",
        sliding_window_size=3,
        retry_max=2,
        trace_dir=actual_trace_dir,
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
    deployment_metadata = app_settings.redacted_summary()
    deployment_metadata["database"].pop("dsn_env_var", None)
    trace.metadata["deployment"] = deployment_metadata
    trace.metadata["adapters"] = runtime_adapters.summary()
    if effective_data_workspace_dir and active_skill == "data_cleaning_file_organization":
        trace.metadata["data_workspace_dir"] = effective_data_workspace_dir

    trace.save(os.path.join(actual_trace_dir, f"project_manager_{trace.trace_id}.json"))
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
