# Project Manager Agent

`project_manager` 是一个本地优先的项目管理与办公自动化 Agent 平台。当前仍以一个 `LoopEngine` 和按业务 Skill 暴露工具的方式工作；平台基础层已将路径、配置和可替换能力收口到显式 Settings 与适配器组合根。

当前业务能力覆盖：

- 项目状态、风险、里程碑、报告和本地项目视图；
- 数据清理、文档整理、结构化提取和归档计划；
- 商机/投标资料解析与本地上下文准备；
- 受确认门控的 CloudCC/CRM 只读检查、草稿和回读。

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

在仓库根目录调用：

```python
from main import run

result = run("今天有什么风险项目", planner_mode="rule")
```

需要整理文件时，请为本次运行显式传入临时工作区；归档仍需经过 `execute_archive_plan(..., confirmed=True)` 的人工确认门：

```python
result = run(
    r"请整理文件并归档 C:\path\to\采购公告.docx",
    planner_mode="rule",
    data_workspace_dir=r"C:\tmp\project_manager_data_workspace",
    trace_dir=r"C:\tmp\project_manager_traces",
)
```

## 跨平台配置

配置优先级为：显式参数 → `PROJECT_MANAGER_*` 环境变量 → `config/project-manager.local.json` → 跨平台默认值。

Windows、macOS 和群晖挂载路径都通过本地配置提供。只有 `PROJECT_MANAGER_BUSINESS_ROOT` 可以指向 Synology Drive、群晖挂载目录或其他同步业务目录。`PROJECT_MANAGER_WORKSPACE_DIR`、`PROJECT_MANAGER_SQLITE_PATH` 和派生运行态必须放在当前执行节点的本机非同步目录。

Windows PowerShell 示例：

```powershell
$env:PROJECT_MANAGER_BUSINESS_ROOT = "E:\SynologyDrive"
$env:PROJECT_MANAGER_WORKSPACE_DIR = Join-Path $env:LOCALAPPDATA "ProjectManager"
$env:PROJECT_MANAGER_SQLITE_PATH = Join-Path $env:LOCALAPPDATA "ProjectManager\state\project_manager.sqlite3"
```

macOS shell 示例：

```bash
export PROJECT_MANAGER_BUSINESS_ROOT="$HOME/SynologyDrive"
export PROJECT_MANAGER_WORKSPACE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/project_manager"
export PROJECT_MANAGER_SQLITE_PATH="$PROJECT_MANAGER_WORKSPACE_DIR/state/project_manager.sqlite3"
```

Settings 会拒绝位于业务根内部的 SQLite 文件和明显的 UNC/network URL。Windows 映射网络盘无法仅凭路径字符串可靠识别，运维人员必须确认运行工作区与 SQLite 实际落在本机磁盘，而不是同步盘、NAS 或网络挂载点。

可从 [config/project-manager.example.json](config/project-manager.example.json) 复制配置样例并改名为 `config/project-manager.local.json`。样例只保存 `PROJECT_MANAGER_DATABASE_DSN` 这个环境变量名，不保存实际 DSN、口令、令牌或私钥；实际 DSN 由节点的运行环境注入。

## SQLite 数据库运维

SQLite 数据库内核已经提供显式状态检查、完整性检查、forward-only migration、一致性备份和候选库恢复。普通 `main.run()` 不会导入数据库内核，也不会在启动时自动迁移；正式 `migrations/sqlite/` 当前没有业务 migration，目标版本为 `0`。第一个实际业务 Repository 接入后才会加入对应业务表和局部门禁。

运维入口是独立 CLI。显式 `--database` 路径优先于环境变量、本地配置和默认值；以下路径只是环境中立示例，实际路径由每个执行节点配置：

```text
python -m infrastructure.database.cli --database runtime/state.sqlite3 --json status
python -m infrastructure.database.cli --database runtime/state.sqlite3 --json check
python -m infrastructure.database.cli --database runtime/state.sqlite3 --json migrate --backup-dir runtime/backups
python -m infrastructure.database.cli --database runtime/state.sqlite3 --json backup --output runtime/backups/state.sqlite3
python -m infrastructure.database.cli --database runtime/state.sqlite3 --json verify-restore --backup runtime/backups/state.sqlite3 --manifest runtime/backups/state.sqlite3.manifest.json --target runtime/restore-candidates/state.sqlite3
```

`--json` 输出稳定的结构化 JSON，包含 `schema_version`、`command`、`status`、`error_code` 和 `details`，并隐藏数据库绝对路径、SQL、DSN、底层异常和其他敏感内容。CLI 使用以下固定退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 操作成功，或 schema 已是 `current`。 |
| `2` | 需要显式运维动作，例如存在待执行 migration 或数据库尚未初始化。 |
| `3` | 配置、provider、catalog、校验和或 schema 版本不兼容。 |
| `4` | 数据库忙或锁等待超时，可以在排除并发写入后重试。 |
| `5` | 备份、恢复、迁移或其他数据库操作失败。 |

运维边界如下：

- `status` 与 `check` 都会读取 schema 状态并执行数据库完整性验证；`check` 用于表达运维检查意图，两者都不执行 migration。
- `migrate` 只向前执行。存在待执行项时，迁移前必须先创建并验证一致性备份；任何调用底层 `apply_pending_migrations()` 的代码也必须提供匹配当前版本和目标版本的已验证备份清单。
- `before_record_insert` 只是 `apply_pending_migrations()` 的关键字测试故障注入参数，用于证明 migration SQL 与迁移记录原子回滚；它不是单独导出的生产 API。
- 备份使用 SQLite Backup API，而不是直接复制活跃数据库文件。备份清单记录 SHA-256 和字节大小，并同时记录 schema 版本、catalog 目标版本、SQLite 版本和完整性结果。
- 备份和恢复都不覆盖已存在的目标路径。目标文件、对应 `.manifest.json` 或恢复候选库已存在时，操作会失败，不会静默替换。
- 恢复顺序为：先验证 manifest、SHA-256 和字节大小；再用 SQLite Backup API 物化临时候选库；随后在临时库执行 integrity、外键和 schema 验证；全部通过后才以 no-overwrite 方式发布最终候选路径。恢复只创建候选数据库，不会修改配置或替换活跃库；发布完成后必须由运维人员显式切换，并保留回退方案。

SQLite 的运行边界是“节点本地”，而不是“仓库所在机器”：

- Windows 或 macOS 单机节点使用自己本机的非同步 SQLite 文件。
- 备份目录和恢复目标父目录必须是当前节点本机受信任目录，仅允许受信任的本地运维进程修改目录项；不要使用 Synology Drive、NAS、网络挂载、同步盘或不受信任的共享临时目录。
- 异地执行节点只能操作该节点自己的本地 SQLite，不能把远端共享文件当成活跃 SQLite，也不能用文件同步模拟多节点数据库并发。
- 示例中的 `runtime/state.sqlite3`、`runtime/backups` 和 `runtime/restore-candidates` 是逻辑示例，不是仓库常量或规定的物理归档位置。

PostgreSQL 是后续 provider；当前只预留 central 配置形状，尚未实现 PostgreSQL 连接、迁移、备份或 Repository。选择 `deployment_mode=central` / `provider=postgresql` 时会明确返回不支持，central 模式不会回退到 SQLite。未来异地执行应通过控制平面 API 与中心 PostgreSQL 协作，而不是让执行节点直接打开同一个 SQLite 文件。

数据库实现不依赖固定的 SynologyDrive、PageIndex、OCR、`document_parse` 或归档物理路径。它们继续作为节点侧存储、结构索引和解析适配器，通过稳定标识与权威业务状态协作；数据库内核不根据盘符、文件名或某一种解析策略建立强耦合。

## 当前阶段已实现

- 跨平台 `platform_core.settings.load_app_settings()`：统一显式参数、环境变量、本地配置与默认值的优先级；业务根、运行工作区和物理归档位置不写死。
- 本地 SQLite 数据库内核：显式不可变 migration catalog、schema 状态、每事务独立连接的 Unit of Work、一致性备份、候选库恢复和独立运维 CLI；生产 catalog 当前为版本 `0`，尚未接入业务 Repository 或业务表。
- PostgreSQL provider 与 `PROJECT_MANAGER_DATABASE_DSN` 环境变量名的配置预留：`deployment_mode=central` 仅验证选择 `postgresql`，是新的 Settings 入口，不是 legacy workspace 兼容入口。
- 显式适配器组合：`local` / `disabled` DocumentStore、`filesystem` ProjectionWriter、`pageindex` / `disabled` StructureIndex；没有动态任意模块加载。
- 目录和工具治理：目录路径随当前节点 Settings 解析，工具契约逐项验证名称、参数形状和必填参数。

当前 `main.run` 的实际路径为：

```text
main.run(goal)
  -> load_app_settings()
  -> build_runtime_adapters()
  -> route one active skill
  -> build active ToolRegistry
  -> choose LLMPlanner or RuleBasedPlanner
  -> LoopEngine plan / act / observe
  -> write trace and derived artifacts
```

## 规划中：中心化生产部署（Phase 2+）

群晖 PostgreSQL 17 与异地执行节点是已确定的目标方案，不是当前已可部署的生产能力。目标中心化拓扑将由控制平面管理权威状态，并让 Windows、macOS 或其他受信任节点通过 API 领取任务、定位本地存储并提交结果；PostgreSQL 不直接暴露公网。

目标群晖包标识可为 `PostgreSQL 17.19-4`，部署时必须用 `SHOW server_version` 和 `SHOW server_version_num` 验证实际服务器主版本为 17；该包标识不是容器镜像标签。

PostgreSQL Repository、连接/连接池、迁移、Unit of Work、权威 SQL 状态持久化、控制平面 API、异地执行节点的注册、领取和提交协议均尚未实现。因此当前版本不能据此部署生产 central 环境。

当前仓库已经提供本地 SQLite 数据库内核，但尚未提供生产业务 Repository；群晖 PostgreSQL 17 及其中心化控制平面仍属于后续实现。

## 数据、能力与可替换边界

业务根目录、运行工作区、原始文件物理位置和归档位置都不是仓库常量。业务数据通过稳定的文档 ID、逻辑 URI、内容哈希和节点侧存储映射识别；当前 DocumentStore 适配器只在实际执行的节点把逻辑位置解析为本地路径。文件可以移动、缺失或等待归档，不能仅凭某个盘符或文件名推断身份。

OCR、`document_parse`、PageIndex 和投影器都是可替换的服务/适配器，而不是固定的业务规则或独立业务 Agent。处理策略由内容和能力决定：文本型 PDF、DOCX、XLSX、CSV、JSON/XML 与网页 DOM 不默认 OCR；只有具备文本、章节结构和局部查询需求的长文档才适合 PageIndex。

在 Phase 2+ 目标架构中，SQL 数据库将保存权威业务状态、文档身份/版本/位置、审批、任务和审计元数据。原始文件和大型产物属于 DocumentStore；PageIndex 负责文档章节树、页码定位和局部检索；JSON / Markdown / HTML 是可重建的投影视图和审计快照，不是主存储或第二权威源。

## 安全与运行规则

- 只有当前激活 Skill 的工具对 Loop 暴露。
- `blocked` 不是成功，也不是负面业务结论。
- `needs_confirmation` 会在人工边界停止。
- CRM/CloudCC 写入必须保持确认门控。
- 文件移动、重命名、覆盖和删除必须先生成动作计划，再经人工确认。
- 项目账本事实先以带证据的候选事实进入流程；LLM 摘要不能直接覆盖权威状态。
- 节点能力缺失时返回明确的 `blocked` 诊断，不能为了“成功”擅自切换为语义不同的解析策略。

## 文件整理闭环

```text
准备运行包
  -> 提取结构化字段
  -> 更新候选账本事实
  -> 输出复核队列和归档计划
  -> 应用人工复核
  -> 确认后执行归档动作
  -> 生成派生进度视图
```

部分失败也应保留成功的结构化产物、失败证据、复核队列、归档计划、trace 和运行报告。源文件在提取或归档前会进行可读性检查；云盘占位、同步失败或权限问题必须以结构化 `blocked` / failure 结果返回。

## 验证与真实样本

Windows 控制台请使用 UTF-8 模式，避免中文诊断受 GBK 默认编码影响：

```powershell
python -X utf8 -B -m pytest -q -p no:cacheprovider
python -X utf8 -B governance\validate.py tools
python -X utf8 -B governance\validate.py dirs
python -X utf8 -B governance\validate.py repository
```

PageIndex、OCR、真实业务样本和外部服务集成测试不是普通快速测试的隐式依赖。它们必须通过明确的本地配置、环境变量、测试 marker 或受控样本目录启用；仓库不提交客户业务原件、运行日志、真实 DSN 或节点私密配置。

## 环境变量

| 变量 | 必需性 | 用途 |
|---|---:|---|
| `LLM_API_KEY` | 仅 LLM 模式 | LLMPlanner API 密钥。 |
| `PROJECT_MANAGER_LLM_BASE_URL` | LLM 启用时 | 统一的 LLM API endpoint；无默认值。 |
| `LLM_BASE_URL` / `OPENAI_API_BASE` | 兼容入口 | 仅作为旧部署 fallback；新配置应使用 `PROJECT_MANAGER_LLM_BASE_URL`。 |
| `LLM_MODEL` | 否 | LLM 模型名。 |
| `PROJECT_MANAGER_DEPLOYMENT_MODE` | 否 | `local`（SQLite 配置形状）或 `central`（PostgreSQL 配置预留）。 |
| `PROJECT_MANAGER_BUSINESS_ROOT` | 否 | 当前节点可访问的业务根目录；可以是同步业务目录。 |
| `PROJECT_MANAGER_WORKSPACE_DIR` | 否 | 当前节点的本机非同步运行工作区。 |
| `PROJECT_MANAGER_DATABASE_PROVIDER` | 否 | `sqlite` 或 `postgresql`，必须与部署模式匹配。 |
| `PROJECT_MANAGER_SQLITE_PATH` | 否 | 单机 SQLite 文件位置；必须位于节点本机、业务根以外。SQLite 内核已实现，业务 Repository 尚未接入。 |
| `PROJECT_MANAGER_DATABASE_DSN_ENV` | 否 | 保存实际 DSN 的环境变量名称，默认 `PROJECT_MANAGER_DATABASE_DSN`。 |
| `PROJECT_MANAGER_DATABASE_DSN` | Phase 2+ central | 未来 PostgreSQL 连接实现读取的 DSN；不写入配置文件。 |
| `PROJECT_MANAGER_DOCUMENT_STORE` | 否 | `local` 或 `disabled`。 |
| `PROJECT_MANAGER_STRUCTURE_INDEX` | 否 | `pageindex` 或 `disabled`。 |
| `PROJECT_MANAGER_PAGEINDEX_DIR` | PageIndex 启用时 | 当前节点的 PageIndex 安装目录。 |
| `PROJECT_MANAGER_PROJECTION_WRITER` | 否 | 当前为 `filesystem`。 |
| `PROJECT_MANAGER_PROJECTION_ROOT` | 否 | JSON / Markdown 等派生投影输出位置。 |
| `LOOP_PROJECT_BASE_DIR` | 治理兼容入口 | 仅覆盖目录治理的运行工作区，不替代新的配置优先级。 |

没有 `LLM_API_KEY` 时，`planner_mode="auto"` 会回退到 `RuleBasedPlanner`，普通非 LLM 流程不要求 endpoint。显式启用 LLM 时若 endpoint 缺失，会返回稳定配置错误。

## License

MIT
