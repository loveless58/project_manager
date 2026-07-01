# Project Manager Agent

基于 Loop Engineering Framework 的项目全生命周期管理 Agent。

集成项目管理、数据清洗、商机检测、CloudCC/CRM 受控自动化四个工具域。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 运行 Agent

本 Agent **不接受命令行参数**，必须通过 `agent.md` 触发后调用 `main.run()`。

#### 方式一：PythonRun（单次执行）

```python
import sys
sys.path.insert(0, "/Users/zhang/Desktop/工作文件/project_manager/project_manager")
from main import run

# 风险检查（默认 LLM 模式：有 LLM_API_KEY 则用 LLM，无则用规则）
result = run("今天有什么风险项目")

# 强制规则模式（无需 API key，用于测试）
result = run("今天有什么风险项目", planner_mode="rule")

# 强制 LLM 模式（缺 key 时会报错）
result = run("今天有什么风险项目", planner_mode="llm")
```

#### 方式二：Cron 定时调度

```json
{
  "name": "daily-risk-check",
  "trigger": {"kind": "cron", "expr": "0 9 * * 1-5"},
  "execution": {
    "kind": "local_conversation",
    "workspacePath": "/Users/zhang/Desktop/工作文件",
    "prompt": "请运行 project_manager 的风险检查，生成风险预警报告"
  }
}
```

### 3. 查看结果

```bash
ls state/
cat state/risk_summary.md
ls logs/project_manager_*.json
```

## 项目结构

```
project_manager/
├── common/                         # ReAct Loop 核心框架（已内联）
│   ├── loop_engine.py              # 循环引擎
│   ├── tool_registry.py            # 工具注册系统
│   ├── state_manager.py            # 状态管理
│   └── llm_adapter.py              # LLM 适配层
├── business_rules/                 # 业务判断规则层
│   └── bid_project_rules.py        # 投标项目阶段、风险、下一步动作判断
├── tools/                          # 业务工具层（31 个工具）
│   ├── project_tools.py            # 项目管理工具（10）
│   ├── data_cleaning_tools.py      # 数据清洗及文件整理工具（10）
│   ├── opportunity_tools.py        # 商机管理工具（5）
│   └── cloudcc_crm_tools.py        # CloudCC/CRM 受控工具（6）
├── planner.py                      # 规划器（LLM / RuleBased）
├── governance/                     # 治理契约与校验
├── references/
│   ├── architecture.md             # 架构设计
│   ├── bid_files_integration.md    # bid-files 集成规范
│   └── project_tools.md            # 工具层 API 文档
├── skills/
│   └── project_manager.md          # Skill 速查（被 agent.md 加载）
├── tests/                          # 回归测试
├── state/                          # 运行状态/输出
├── logs/                           # 执行轨迹
├── agent.md                        # 触发入口（被 Kimi Work 加载）
├── main.py                         # 主入口（不接受命令行）
├── requirements.txt
└── README.md
```

## 核心能力（31 个工具）

### 项目管理（10 个）

| 工具 | 说明 | 触发场景 |
|------|------|---------|
| `scan_projects` | 扫描三阶段目录，返回项目列表 | 查看所有项目 |
| `read_project_record` | 解析项目记录.md | XX 项目进度 |
| `check_milestones` | 检查里程碑（逾期/到期） | 风险预警、到期检查 |
| `check_deliverables` | 核对交付物 | 交付物清单 |
| `archive_files` | 智能归档文件 | 文件归档、整理 |
| `migrate_project` | 迁移项目阶段 | 状态变更 |
| `generate_bid_overview` | 生成投标进度总览 | 投标进度 |
| `generate_project_overview` | 生成全局项目总览 | 全局状态 |
| `generate_report` | 生成项目报告 | 周报、简报 |
| `write_response` | 写入响应文件 | 内部使用 |

### 数据清洗及文件整理（10 个）

| 工具 | 说明 | 触发场景 |
|------|------|---------|
| `scan_raw_files` | 扫描原始文件目录 | 查看有哪些文件 |
| `extract_pdf` | 提取 PDF 结构化数据 | 解析单个文件 |
| `extract_document` | 提取 Word/PDF/图片文件的结构化数据 | 真实文件抽取 |
| `classify_document` | 文档分类 | 分类归档 |
| `batch_process` | 批量处理 | 批量处理所有文件 |
| `save_structured` | 保存结构化数据 | 内部使用 |
| `process_documents_to_ledger` | 多文件抽取并更新项目账本 | Word/PDF 到项目总览 |
| `import_project_detail_workbook` | 导入项目明细表并更新多个项目账本 | Excel 到项目账本 |
| `generate_bid_progress_html` | 从项目账本生成投标进度总览 HTML | 展示层派生产物 |
| `update_project_ledger` | 写入候选事实、证据和冲突决议 | 账本治理入口 |

### 商机管理（5 个）

| 工具 | 说明 | 触发场景 |
|------|------|---------|
| `scan_bid_notices` | 扫描招标公告目录 | 查看有哪些公告 |
| `parse_bid_notice` | 解析公告提取关键字段 | 解析单个公告 |
| `check_duplicate` | 检测商机是否重复 | 查重 |
| `generate_bid_context` | 生成 bid_context.json | 标准化输出 |
| `create_crm_suggestion` | 生成 CRM 录入建议 | 准备录入 CRM |

### CloudCC/CRM 受控工具域（6 个）

| 工具 | 说明 | 安全边界 |
|------|------|---------|
| `cloudcc_session_probe` | 检查 CloudCC 登录态和浏览器适配器 | 只读，失败返回 blocked |
| `cloudcc_search_record` | 查询 CRM 对象记录 | 只读，不能把 blocked 当成未查到 |
| `cloudcc_duplicate_check` | 基于证据检查商机重复 | 只读，必须保留 evidence |
| `cloudcc_prepare_opportunity_draft` | 准备 CRM 商机草稿 | 本地草稿，不写 CRM |
| `cloudcc_fill_draft_gated` | 填充草稿并停在提交前 | 必须 needs_confirmation |
| `cloudcc_readback_record` | 写入后回读校验 | 只在确认提交后使用 |

## 与 bid-files 的关系

本 Agent 是 `bid-files` 技能的智能编排层：
- **数据互通**：直接复用 `项目文件/` 目录结构和 `index.json`
- **渐进式**：当前通过脚本调用 bid-files 功能，未来可内联化
- **不替代**：`bid-files` 脚本体系继续保留，Agent 提供自然语言入口

## 业务判断层

`business_rules/BidProjectRuleEngine` 在项目账本每次更新后运行，输出 `business_judgement`，包括：

- `business_stage`：投标项目业务阶段
- `display_status`：HTML 总览使用的展示状态
- `risk_level` / `risk_reasons`：风险等级和原因
- `next_actions`：下一步动作建议
- `missing_fields` / `data_quality_flags`：数据质量问题
- `human_review_required` / `crm_required` / `bpm_required`：人工、CRM、BPM 后续动作标记

第一版只做确定性规则判断，不直接调用 LLM、CRM 或 BPM。

## 依赖

- Python 3.9+
- `requests` 库（用于 API 调用）
- `项目文件/` 目录结构已初始化（项目投标/项目执行/项目归档）
- 数据清洗工作台目录（`数据清洗工作台/00-原始文件（待处理）`）
- 新机会目录（`新机会与线索/招标公告/`）
- CloudCC/CRM 默认使用 fake adapter；真实浏览器接入必须先通过登录态探针、证据采集和提交前确认。

## 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_API_KEY` | 是（LLM 模式） | — | API 密钥 |
| `LLM_BASE_URL` | 否 | `http://172.18.125.202:9990/v1` | API 端点 |
| `LLM_MODEL` | 否 | `minimax-m3-mxfp8` | 模型名 |

无 `LLM_API_KEY` 时自动降级为 RuleBasedPlanner（基于关键词的演示模式）。

## License

MIT
