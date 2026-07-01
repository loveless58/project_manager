---
name: loop-project-lifecycle
description: |
  项目全生命周期管理 Loop Agent 的工具注册表与工作流索引。
  
  本文件是 agent.md 的配套文件，不单独触发。被激活后由 Kimi Work 加载，为 LoopEngine 提供工具契约和场景执行路径参考。
---

# Loop Project Lifecycle — 工具与工作流

## 工具注册表

| 工具名 | 说明 | 必需参数 | 触发场景 |
|--------|------|----------|---------|
| `scan_projects` | 扫描三阶段目录，返回项目列表+状态 | — | 项目列表、查看所有项目 |
| `read_project_record` | 解析项目记录.md 为结构化数据 | `project_name` | XX项目进度 |
| `check_milestones` | 检查里程碑状态（逾期/到期） | — | 风险预警、到期检查 |
| `check_deliverables` | 扫描子目录与交付物清单对比 | `project_name` | 交付物核对 |
| `archive_files` | 内容匹配→归档到子目录→删除源文件 | — | 文件归档、文件整理 |
| `migrate_project` | 移动目录+更新状态 | `project_name` | 状态迁移、项目移动 |
| `generate_bid_overview` | 生成投标进度总览.md | — | 投标进度、投标总览 |
| `generate_project_overview` | 生成全局项目总览 | — | 项目总览、全局状态 |
| `generate_report` | 生成周报/简报/状态报告 | `project_name` | 周报、生成报告 |
| `write_response` | 将结果保存到 state/ 目录 | `content`, `filename` | （内部使用） |

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
```

## 状态管理策略

- **模式**: `sliding_window`
- **保留**: 系统提示 + 初始请求 + 最近 3 轮交互
- **自动丢弃**: 中间历史，防止长循环 token 超支

## 详细文档索引

| 文件 | 用途 | 加载时机 |
|------|------|---------|
| `agent.md` | 触发入口、身份、边界 | 系统路由阶段 |
| `references/architecture.md` | 架构设计与数据流 | 调试/扩展时 |
| `references/bid_files_integration.md` | bid-files 集成规范 | 理解数据格式时 |
| `references/project_tools.md` | 工具层详细 API | 扩展工具时 |
