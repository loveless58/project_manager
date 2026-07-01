---
name: project_manager
version: 3.0.0
description: |
  Project Manager Agent 的工具索引与场景速查。

  本文件是 agent.md 的配套速查，不单独触发。被激活后由 Kimi Work 加载，为 LoopEngine 提供工具契约和场景执行路径参考。

  注意：Loop 框架已内联到 common/ 目录，无需外部依赖。
---

# Project Manager Agent — 工具与场景速查

## 工具注册表（20 个）

### 项目管理（10 个）

| 工具名 | 说明 | 必需参数 | 触发场景 |
|--------|------|----------|---------|
| `scan_projects` | 扫描三阶段目录，返回项目列表+状态 | — | 项目列表、查看所有项目 |
| `read_project_record` | 解析项目记录.md 为结构化数据 | `project_name` | XX 项目进度 |
| `check_milestones` | 检查里程碑状态（逾期/到期） | — | 风险预警、到期检查 |
| `check_deliverables` | 扫描子目录与交付物清单对比 | `project_name` | 交付物核对 |
| `archive_files` | 内容匹配→归档到子目录→删除源文件 | — | 文件归档、文件整理 |
| `migrate_project` | 移动目录+更新状态 | `project_name` | 状态迁移、项目移动 |
| `generate_bid_overview` | 生成投标进度总览.md | — | 投标进度、投标总览 |
| `generate_project_overview` | 生成全局项目总览 | — | 项目总览、全局状态 |
| `generate_report` | 生成周报/简报/状态报告 | `project_name` | 周报、生成报告 |
| `write_response` | 将结果保存到 state/ 目录 | `content`, `filename` | （内部使用） |

### 数据清洗（5 个）

| 工具名 | 说明 | 必需参数 | 触发场景 |
|--------|------|----------|---------|
| `scan_raw_files` | 扫描原始文件目录 | — | 查看有哪些文件 |
| `extract_pdf` | 提取 PDF 结构化数据 | `file_path` | 解析单个文件 |
| `classify_document` | 根据文件名/内容分类文档 | `file_path` | 分类归档 |
| `batch_process` | 批量处理目录文件 | — | 批量处理所有文件 |
| `save_structured` | 保存结构化数据 | `data`, `output_path` | （内部使用） |

### 商机管理（5 个）

| 工具名 | 说明 | 必需参数 | 触发场景 |
|--------|------|----------|---------|
| `scan_bid_notices` | 扫描招标公告目录 | — | 查看有哪些招标公告 |
| `parse_bid_notice` | 解析招标公告提取关键字段 | `file_path` | 解析单个公告 |
| `check_duplicate` | 检测商机是否重复 | `project_code` | 检测是否已有商机 |
| `generate_bid_context` | 生成 bid_context.json | `parsed_data` | 标准化输出 |
| `create_crm_suggestion` | 生成 CRM 录入建议 | `bid_context` | 准备录入 CRM |

## 典型场景执行路径

### 场景 1：风险预警
```
scan_projects → check_milestones → write_response → Final Answer
```

### 场景 2：项目进度查询
```
read_project_record(project_name) → check_milestones → check_deliverables → Final Answer
```

### 场景 3：智能归档
```
archive_files(source_dir) → migrate_project(检测) → write_response → Final Answer
```

### 场景 4：投标进度
```
generate_bid_overview → write_response → Final Answer
```

### 场景 5：项目总览
```
generate_project_overview → write_response → Final Answer
```

### 场景 6：数据清洗
```
scan_raw_files → extract_pdf → classify_document → save_structured → Final Answer
```

### 场景 7：商机检测
```
scan_bid_notices → parse_bid_notice → check_duplicate → generate_bid_context → Final Answer
```

## 状态管理策略

- **模式**: `sliding_window`
- **保留**: 系统提示 + 初始请求 + 最近 3 轮交互
- **自动丢弃**: 中间历史，防止长循环 token 超支

## 工作目录约定

```
项目文件/
├── index.json              # 机器索引（读写）
├── 投标进度总览.md         # 人类可读报告（生成）
├── 项目总览.md             # 全局状态报告（生成）
├── 项目投标/
│   └── <项目名>/
│       ├── 项目记录.md     # 核心数据源（只读）
│       ├── 招标文件/
│       ├── 报名材料/
│       ├── 投标文件/
│       └── ...
├── 项目执行/
└── 项目归档/

数据清洗工作台/
├── 00-原始文件（待处理）
├── 01-OCR输出（待清洗）
└── 02-已清洗（结构化数据）

新机会与线索/
├── 招标公告/
├── bid_contexts/
├── 商机列表.md
└── 重复检测记录.md
```

## 详细文档索引

| 文件 | 用途 | 加载时机 |
|------|------|---------|
| `agent.md` | 触发入口、身份、边界 | 系统路由阶段 |
| `references/architecture.md` | 架构设计与数据流 | 调试/扩展时 |
| `references/bid_files_integration.md` | bid-files 集成规范 | 理解数据格式时 |
| `references/project_tools.md` | 工具层详细 API | 扩展工具时 |
| `common/loop_engine.py` | ReAct 循环引擎 | 调试循环时 |
| `common/tool_registry.py` | 工具注册系统 | 调试工具时 |
| `common/state_manager.py` | 状态管理 | 调试状态裁剪时 |
| `common/llm_adapter.py` | LLM 适配层 | 调试 LLM 调用时 |

## 版本变更

- **v3.0.0**：项目名从 `loop-project-lifecycle` 改为 `project_manager`；新增数据清洗与商机管理 10 个工具；主入口改为不接受命令行参数
- **v2.1.0**：内联 Loop 框架，移除外部依赖
