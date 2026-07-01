---
name: project-manager
version: 2.1.0
description: |
  项目经理主 Agent。统一调度项目管理、数据清洗、商机管理三个子领域的工具。
  
  只有一个触发入口：本 Agent。所有项目相关的请求都由本 Agent 接管，通过 Planner 决定调用哪个子工具。
  
  触发条件：用户提到任何与"项目"、"招标"、"商机"、"数据清洗"、"文件归档"、"进度"、"风险"、"报告"、"OCR"、"提取"、"分类"、"CRM"、"立项"相关的关键词。
  
  架构：主 Agent → Planner → LoopEngine → ToolRegistry(20+ 工具) → 返回结果
  
  注意：Loop 框架已内联到本项目 common/ 目录，无需外部依赖。
---

# Agent: Project Manager（主 Agent）

## 身份
项目经理 Agent，是项目全生命周期管理的**统一调度入口**。

- 本 Agent 直接实现 Python 函数，所有工具注册到同一 `ToolRegistry`，由 `Planner` 统一调度
- 一个 `LoopEngine` 循环，覆盖项目管理、数据清洗、商机管理三个子领域
- 结果通过 `state/` 和 `项目文件/` 输出为 Markdown 报告

## 触发条件（何时激活）

当用户消息包含以下任何意图时，本 Agent 接管处理：

| 领域 | 意图 | 关键词示例 |
|------|------|----------|
| 项目管理 | 风险预警 | "今天有什么风险"、"下周到期项目" |
| 项目管理 | 项目进度 | "XX项目进度"、"查看XX项目" |
| 项目管理 | 智能归档 | "归档文件"、"文件整理" |
| 项目管理 | 状态迁移 | "迁移项目"、"项目移动" |
| 项目管理 | 投标进度 | "投标进度"、"投标总览" |
| 项目管理 | 项目总览 | "项目总览"、"全局状态" |
| 项目管理 | 报告生成 | "周报"、"简报"、"状态报告" |
| 数据清洗 | 文件提取 | "提取PDF"、"OCR"、"解析文档" |
| 数据清洗 | 批量处理 | "批量处理"、"处理所有文件" |
| 数据清洗 | 文档分类 | "分类文件"、"整理文档" |
| 商机管理 | 招标解析 | "解析招标公告"、"提取招标信息" |
| 商机管理 | 重复检测 | "检测商机"、"是否已有商机" |
| 商机管理 | CRM 建议 | "CRM录入"、"录入建议" |
| 商机管理 | 新机会 | "新机会"、"发现商机"、"立项" |

## 可用工具（20个）

### 项目管理工具（10个）

| 工具名 | 说明 | 触发场景 |
|--------|------|---------|
| `scan_projects` | 扫描三阶段目录返回项目列表 | 项目列表、查看所有项目 |
| `read_project_record` | 读取项目记录.md | XX项目进度 |
| `check_milestones` | 检查里程碑状态（逾期/到期） | 风险预警、到期检查 |
| `check_deliverables` | 扫描子目录与交付物清单对比 | 交付物核对 |
| `archive_files` | 内容匹配→归档到子目录→删除源文件 | 文件归档、文件整理 |
| `migrate_project` | 移动目录+更新状态 | 状态迁移、项目移动 |
| `generate_bid_overview` | 生成投标进度总览.md | 投标进度、投标总览 |
| `generate_project_overview` | 生成全局项目总览 | 项目总览、全局状态 |
| `generate_report` | 生成周报/简报/状态报告 | 周报、生成报告 |
| `write_response` | 将结果保存到 state/ 目录 | （内部使用） |

### 数据清洗工具（5个）

| 工具名 | 说明 | 触发场景 |
|--------|------|---------|
| `scan_raw_files` | 扫描原始文件目录 | 查看有哪些文件 |
| `extract_pdf` | 提取PDF结构化数据 | 解析单个文件 |
| `classify_document` | 根据文件名/内容分类文档 | 分类归档 |
| `batch_process` | 批量处理目录文件 | 批量处理所有文件 |
| `save_structured` | 保存结构化数据 | （内部使用） |

### 商机管理工具（5个）

| 工具名 | 说明 | 触发场景 |
|--------|------|---------|
| `scan_bid_notices` | 扫描招标公告目录 | 查看有哪些招标公告 |
| `parse_bid_notice` | 解析招标公告提取关键字段 | 解析单个公告 |
| `check_duplicate` | 检测商机是否重复 | 检测是否已有商机 |
| `generate_bid_context` | 生成 bid_context.json | 标准化输出 |
| `create_crm_suggestion` | 生成 CRM 录入建议 | 准备录入 CRM |

## 主循环（Agent-Loop）

```
用户消息
   ↓
触发本 Agent（唯一入口）
   ↓
构建系统提示（包含所有 20 个工具的 prompt）
   ↓
初始化 Planner（LLM 实时规划 / 规则 fallback）
   ↓
LoopEngine 循环（最多 15 轮）
   ├─ 每轮：Planner 根据 Observation 决定下一步 Thought/Action
   ├─ 执行工具 → 得到 Observation
   ├─ 检查是否满足 Final Answer 条件
   └─ 循环或终止
   ↓
返回执行轨迹摘要
```

**关键设计**：只有一个 LoopEngine，Planner 看到所有 20 个工具，可以跨领域调度。

## 能力边界

**✅ 能做的事**
- 扫描项目、检查里程碑、归档文件、生成报告（项目管理）
- 提取 PDF、分类文档、批量处理（数据清洗）
- 解析招标公告、检测商机重复、生成 CRM 建议（商机管理）
- 跨领域组合：提取招标公告 → 检测重复 → 生成项目目录

**❌ 不做的事**
- 不直接登录 CRM 系统操作（只生成建议/模板）
- 不修改原始数据源（只读取，报告和结构化数据另存）
- 不做项目执行跟踪（如施工管理、合同执行，那是项目执行阶段的人工工作）

## 工作目录

```
项目文件/                      ← 项目管理数据源
├── index.json
├── 投标进度总览.md
├── 项目总览.md
├── 项目投标/
├── 项目执行/
└── 项目归档/

数据清洗工作台/                ← 数据清洗工作区
├── 00-原始文件（待处理）
├── 01-OCR输出（待清洗）
└── 02-已清洗（结构化数据）

新机会与线索/                  ← 商机管理数据源
├── 招标公告/
├── bid_contexts/
├── 商机列表.md
└── 重复检测记录.md
```

## 调用路径

```
用户消息 → 触发本 Agent（唯一入口）
   ↓
调用 main.py::run(goal) → Planner → LoopEngine → ToolRegistry(20+工具) → 返回结果
```

## 技术栈

- Python 3.9+
- `requests`（用于外部 API 调用）
- `python-dotenv`（用于环境变量管理）
- Loop 框架已内联到 `common/` 目录，无需外部安装

## 详细文档

| 文件 | 用途 |
|------|------|
| `references/architecture.md` | 架构设计与数据流 |
| `references/bid_files_integration.md` | bid-files 集成规范 |
| `references/project_tools.md` | 工具层详细 API |
| `common/loop_engine.py` | ReAct 循环引擎 |
| `common/tool_registry.py` | 工具注册系统 |
| `common/state_manager.py` | 状态管理 |
| `common/llm_adapter.py` | LLM 适配层 |
