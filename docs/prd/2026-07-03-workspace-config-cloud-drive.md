# 统一业务路径配置与云盘可读性门控 PRD

## 背景

`project_manager` 当前仍有多处默认业务路径散落在工具和治理层中：

- `tools/data_cleaning_tools.py` 默认使用 `~/Desktop/工作文件/project_manager`
- `tools/project_tools.py` 默认使用 `~/Desktop/工作文件/项目文件`
- `tools/opportunity_tools.py` 默认使用 `~/Desktop/工作文件/新机会与线索`
- `governance/validate.py` 和 `governance/directory_contract.json` 维护了独立的目录契约默认值

这会导致正式处理 `E:\SynologyDrive` 中的业务文件时，输入、结构化输出、账本、归档计划、trace 和总览文件落在不同根目录，形成状态分裂。云盘同步目录还可能出现占位文件或同步失败文件，路径存在但读取时报 `The cloud operation was unsuccessful.`，需要在 loop 中变成显式阻断，而不是运行中途异常。

## 目标

- 引入统一 workspace 配置层，所有默认业务路径从同一配置对象派生。
- 默认正式业务根为 `E:\SynologyDrive`。
- 默认运行工作区为 `E:\SynologyDrive\_project_manager_workspace`。
- 让仓库经云盘传输到其他机器后，只需设置环境变量或本地配置文件即可正常运行。
- 在数据清洗准备阶段检测源文件是否本地可读。
- 在归档确认执行前二次校验源文件和目标目录，避免半途移动失败。
- 遇到云盘占位或不可读文件时返回结构化 `blocked` / failure 记录，保留 run 包和审计产物。

## 非目标

- 不重构现有业务规则。
- 不改变 loop package 的工具暴露语义。
- 不自动移动真实源文件。
- 不引入数据库或远程状态服务。
- 不强制下载或修复云盘占位文件。

## 成功标准

- 默认业务根可配置为 `E:\SynologyDrive`。
- 默认 workspace 可配置为 `E:\SynologyDrive\_project_manager_workspace`。
- `DataCleaningTools`、`ProjectTools`、`ArchiveTools`、`ReportTools`、`MigrateTools`、`OpportunityManagerTools` 默认路径来自同一配置对象。
- `prepare_file_organization_run(file_paths)` 在提取前执行 source readiness 检查。
- 文件存在但不可读时，单文件进入 failure，错误为 `source_not_local_or_unreadable`，阻断原因为 `cloud_placeholder_or_sync_failure` 或明确的本地文件状态原因。
- `execute_archive_plan(run_id, confirmed=True)` 移动前校验 source readable、target parent writable、target not exists、human review gate。
- `main.run(..., data_workspace_dir=...)` 仍可覆盖数据清洗工作区。
- 旧 `~/Desktop/工作文件/...` 只作为兼容说明，不作为正式默认业务根。

## 配置优先级

配置解析顺序固定为：

1. 显式参数
2. 环境变量 `PROJECT_MANAGER_WORKSPACE_DIR` / `PROJECT_MANAGER_BUSINESS_ROOT`
3. repo-local 配置文件 `config/workspace.local.json`
4. 默认值 `E:\SynologyDrive` + `E:\SynologyDrive\_project_manager_workspace`
5. 开发兜底 `repo/state`

## 默认目录结构

```text
E:\SynologyDrive\_project_manager_workspace
  \runs
  \项目文件
  \新机会与线索
  \数据清洗工作台
  \state
  \logs
  \投标进度总览.html
```

## 失败语义

云盘占位、同步失败、权限拒绝、目录误传为文件等情况不应抛出未处理异常。工具应返回结构化结果：

```json
{
  "status": "blocked",
  "path": "E:\\SynologyDrive\\...",
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
