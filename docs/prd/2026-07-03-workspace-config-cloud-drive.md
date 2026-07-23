# 统一业务路径配置与云盘可读性门控 PRD

> 状态：已被 `docs/superpowers/specs/2026-07-23-multi-agent-data-platform-design.md` 的跨平台部署设计取代。本文仅保留历史兼容背景，`E:\SynologyDrive` 不再是代码默认值。

## 背景

`project_manager` 当时仍有多处默认业务路径散落在工具和治理层中：

- `tools/data_cleaning_tools.py` 默认使用 `~/Desktop/工作文件/project_manager`
- `tools/project_tools.py` 默认使用 `~/Desktop/工作文件/项目文件`
- `tools/opportunity_tools.py` 默认使用 `~/Desktop/工作文件/新机会与线索`
- `governance/validate.py` 和 `governance/directory_contract.json` 维护了独立的目录契约默认值

这会导致正式处理同步业务文件时，输入、结构化输出、账本、归档计划、trace 和总览文件落在不同根目录，形成状态分裂。云盘同步目录还可能出现占位文件或同步失败文件，路径存在但读取时报 `The cloud operation was unsuccessful.`，需要在 loop 中变成显式阻断，而不是运行中途异常。

## 历史目标

- 引入统一 workspace 配置层，所有默认业务路径从同一配置对象派生。
- 允许环境变量或本地配置文件为不同机器提供业务根和运行工作区。
- 在数据清洗准备阶段检测源文件是否本地可读。
- 在归档确认执行前二次校验源文件和目标目录，避免半途移动失败。
- 遇到云盘占位或不可读文件时返回结构化 `blocked` / failure 记录，保留 run 包和审计产物。

## 已由新设计取代的约束

本文最初把某个 Windows 同步盘描述为正式默认路径，并把旧 `common.workspace_config` 视为配置入口。这两项约束均已废弃：

- 当前配置入口为 `platform_core.settings.load_app_settings`，配置优先级为显式参数、`PROJECT_MANAGER_*` 环境变量、`config/project-manager.local.json` 和跨平台默认值。
- 本地 Windows/macOS 运行使用 SQLite；群晖中心化运行使用 PostgreSQL 17，异地执行节点通过控制平面 API 工作。
- 原始文件、归档位置和节点本地挂载路径可以变化；物理路径不是文档身份。
- JSON / Markdown 仅作为可重建投影视图与审计快照，不是权威数据库。

请以 [跨平台多智能体数据平台设计](../superpowers/specs/2026-07-23-multi-agent-data-platform-design.md) 和根目录 README 为准。本文以下内容仅解释旧调用方为何保留兼容门面，不构成新部署配置。

## 历史非目标

- 不重构现有业务规则。
- 不改变 loop package 的工具暴露语义。
- 不自动移动真实源文件。
- 不强制下载或修复云盘占位文件。

## 历史成功标准

- 各业务工具的兼容构造函数参数继续有效，并优先于全局配置。
- `main.run(..., data_workspace_dir=...)` 继续有效。
- `prepare_file_organization_run(file_paths)` 在提取前执行 source readiness 检查。
- 文件存在但不可读时，单文件进入 failure，错误为 `source_not_local_or_unreadable`，阻断原因为 `cloud_placeholder_or_sync_failure` 或明确的本地文件状态原因。
- `execute_archive_plan(run_id, confirmed=True)` 移动前校验 source readable、target parent writable、target not exists、human review gate。

## 历史配置优先级

旧调用方的路径适配已迁入新的 Settings 配置层。`LOOP_PROJECT_BASE_DIR` 只保留为目录治理的兼容输入；新代码不得再从本 PRD 的示例盘符推导默认位置。

## 历史失败语义

云盘占位、同步失败、权限拒绝、目录误传为文件等情况不应抛出未处理异常。工具应返回结构化结果：

```json
{
  "status": "blocked",
  "path": "<node-local-path>",
  "exists": true,
  "is_file": true,
  "readable": false,
  "blocked_reason": "cloud_placeholder_or_sync_failure",
  "error": "The cloud operation was unsuccessful."
}
```

## 兼容策略

- 构造函数已有参数 `workspace_dir`、`base_dir`、`opportunity_dir` 继续有效，并优先于全局配置。
- `main.run(..., data_workspace_dir=...)` 继续有效。
- `LOOP_PROJECT_BASE_DIR` 可作为治理校验兼容输入，但新默认配置以 `PROJECT_MANAGER_*` 为准。
