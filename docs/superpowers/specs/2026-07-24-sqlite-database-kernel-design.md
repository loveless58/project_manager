# SQLite 数据库内核设计

**日期：** 2026-07-24

**阶段：** 多智能体数据平台 Phase 2，第一个实现切片

**状态：** 已批准设计，等待实施计划

**前置阶段：** `2026-07-23-multi-agent-data-platform-design.md` 与 `2026-07-23-platform-foundation.md`

## 1. 目标

本阶段建立 Windows/macOS 单机运行所需的 SQLite 数据库内核，为后续业务 Repository 和 PostgreSQL 17 实现提供已经验证的迁移、事务、备份与运维语义。

本阶段只实现数据库基础设施，不创建项目、文档、任务、审批、审计等业务表，不把现有文件账本迁入数据库，也不让数据库成为尚未定义业务模型的第二权威源。

交付内容包括：

- 基于 Python 标准库 `sqlite3` 的连接工厂；
- 显式、forward-only、带 SHA-256 校验的 SQL migration；
- 每事务独立连接的 SQLite Unit of Work；
- 使用测试专用实体和测试专用 migration 验证 Repository 契约；
- 基于 SQLite Backup API 的一致性备份；
- 恢复到新路径后的完整性、外键与 schema 验证；
- 独立的数据库运维 CLI；
- 稳定的状态、错误码和结构化输出；
- Windows 默认编码与 UTF-8 模式下的完整回归。

## 2. 明确不做

本阶段不实现：

- PostgreSQL 连接、连接池、Repository、迁移锁或备份；
- SQLAlchemy、Alembic 或任何 ORM；
- 项目、文档、任务、节点、审批或审计业务 Schema；
- 生产通用 KV 表；
- 动态表名的通用 Repository；
- SQLite FTS5 或 PostgreSQL 全文检索；
- 控制平面 API、执行节点注册、任务领取、租约或结果提交；
- 现有文件账本到 SQL 的数据迁移；
- 在 `main.run()` 启动时强制检查数据库；
- 自动 migration；
- `down.sql` 或自动数据库降级；
- 自动覆盖活跃数据库的恢复操作。

## 3. 设计选择

### 3.1 标准库优先

使用 Python 标准库 `sqlite3` 和仓库内版本化 SQL migration，不引入第三方 ORM 或迁移框架。

原因：

- SQLite 的事务、锁、外键和备份行为保持透明；
- 没有尚未确定的 ORM 实体反向决定领域模型；
- 不增加当前应用的部署依赖；
- 后续 PostgreSQL 可以复用迁移状态、错误分类和 CLI 语义，但使用独立的方言 SQL、连接池和锁实现。

### 3.2 显式迁移

迁移只能通过独立 CLI/API 显式执行。普通应用启动不自动修改 Schema。

第一个真实业务 Repository 接入时，只对实际依赖数据库的能力增加 schema 启动门禁。现有 OCR、文件整理、CRM 和项目工具在本阶段保持原行为。

### 3.3 每个 Unit of Work 独立连接

不使用进程级共享连接，也不自建 SQLite 连接池。每个 Unit of Work 创建、拥有并关闭一个连接，使事务边界明确，并与未来 PostgreSQL 的每事务连接语义保持接近。

### 3.4 测试夹具 Repository

生产数据库不创建无明确业务归属的通用表。Repository 的 `get/add`、事务、外键和唯一约束通过测试专用实体、测试专用 SQL migration 和测试专用 Repository 验证。

### 3.5 Forward-only 与备份恢复

Migration 只向前执行。降级不依赖可能破坏数据语义的反向 SQL，而是使用迁移前一致性备份恢复到一个新数据库路径，经验证后由运维显式切换。

## 4. 模块与依赖边界

建议目录：

```text
infrastructure/
  database/
    __init__.py
    contracts.py
    migration_catalog.py
    cli.py
    sqlite/
      __init__.py
      connection.py
      migration_runner.py
      unit_of_work.py
      backup.py

migrations/
  sqlite/
    README.md

tests/
  database/
    fixtures/
      migrations/
    test_catalog.py
    test_sqlite_migrations.py
    test_sqlite_uow.py
    test_sqlite_backup.py
    test_database_cli.py
```

依赖方向：

```text
platform_core
    ↑
infrastructure.database
    ↑
database CLI

现有 main.run ── 不依赖数据库内核
现有业务工具 ── 不依赖数据库内核
```

规则：

- `platform_core` 继续只保存 Settings、DTO、Repository/UnitOfWork Protocol 和其他核心契约；
- `platform_core` 不导入 `infrastructure.database`；
- `infrastructure.database` 可以实现和依赖核心契约；
- 本阶段不创建通用 PostgreSQL dialect 抽象；
- 正式 `migrations/sqlite/` 初始可以没有 `.sql` 文件，目标 schema 版本为 `0`；
- 第一个业务切片将从 `0001_<name>.sql` 开始；
- 测试 fixture migration 不得进入正式 migration 目录。

## 5. 核心数据结构与错误

`contracts.py` 定义不可变结果 DTO 和稳定错误类型，至少包括：

- `MigrationInfo`
  - `version`
  - `name`
  - `checksum_sha256`
  - `path` 仅供节点内部使用，不进入 CLI 公开输出
- `AppliedMigration`
  - `version`
  - `name`
  - `checksum_sha256`
  - `applied_at_utc`
  - `execution_ms`
- `SchemaStatus`
  - `state`
  - `current_version`
  - `target_version`
  - `pending_versions`
  - `error_code`
- `BackupManifest`
  - `format_version`
  - `schema_version`
  - `catalog_target_version`
  - `sha256`
  - `size_bytes`
  - `created_at_utc`
  - `sqlite_version`
  - `integrity_check`

稳定错误类型：

- `DatabaseConfigurationError`
- `MigrationCatalogError`
- `MigrationChecksumError`
- `SchemaTooNewError`
- `MigrationExecutionError`
- `DatabaseBusyError`
- `DatabaseIntegrityError`
- `BackupError`
- `RestoreVerificationError`
- `UnitOfWorkStateError`

对外错误不得包含完整 SQL、数据库绝对路径、配置文件内容、用户名、底层异常全文或秘密。

## 6. Migration Catalog

Migration 文件名：

```text
0001_<snake_case_name>.sql
0002_<snake_case_name>.sql
```

Catalog 规则：

- 版本从 `0001` 开始严格连续；
- 不允许重复版本、缺号、零版本或非法文件名；
- 按解析后的数值版本排序，不依赖文件系统顺序；
- 每个文件使用二进制内容计算 SHA-256；
- 文件必须可按 UTF-8 解码；
- 已应用 migration 的版本、名称和 SHA-256 必须与仓库一致；
- 已应用 migration 文件被删除、重命名或修改时判定为 `tampered`；
- Catalog 自身非法时，不连接或修改目标数据库。

Migration 文件不得自行包含事务管理语句。事务由 runner 统一控制。实现计划必须定义可审计的检测规则和对应测试，禁止 migration 通过 `BEGIN`、`COMMIT`、`ROLLBACK` 或 `SAVEPOINT` 逃逸 runner 的事务边界。

## 7. Schema 状态

状态枚举：

- `current`：数据库版本等于 catalog 目标版本，所有记录和校验和一致；
- `pending`：数据库版本低于目标版本；
- `uninitialized`：不存在 `_schema_migrations`；
- `too_new`：数据库版本高于当前 catalog；
- `tampered`：已应用记录与 catalog 名称、校验和或文件集合不一致；
- `invalid_catalog`：migration 文件重号、缺号、非法或不可读；
- `corrupt`：数据库、迁移元数据或完整性检查异常。

空正式 catalog 的目标版本为 `0`。未初始化数据库与版本 `0` 数据库是不同状态：前者尚未建立 migration 内核元数据，后者已经初始化且当前无业务 migration。

## 8. Migration 元数据与执行

内部引导表：

```sql
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version          INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    checksum_sha256  TEXT NOT NULL,
    applied_at_utc   TEXT NOT NULL,
    execution_ms     INTEGER NOT NULL
);
```

该表属于 migration 机制引导元数据，不是业务 Schema。

执行流程：

1. 完整读取并验证 catalog；
2. 打开 SQLite 连接；
3. 初始化 `_schema_migrations`；
4. 使用 `BEGIN IMMEDIATE` 获取写事务；
5. 获得锁后重新读取已应用记录和状态；
6. 按版本执行待迁移 SQL；
7. 每个 migration 的 SQL 和对应 applied record 在同一事务中提交；
8. 记录 UTC 时间和执行毫秒数；
9. 任一语句失败时回滚当前 migration；
10. 早先已成功提交的 migration 保留；
11. 再次执行时从最后成功版本继续。

实现不得在已经由 Python 开启的事务中直接调用会隐式提交的裸
`sqlite3.Connection.executescript()`。Runner 必须采用经过测试的受控执行方式：
由 runner 生成只包含 `BEGIN IMMEDIATE` 与单份 migration SQL、但不包含 `COMMIT`
的受控脚本；脚本成功返回后计算执行耗时，通过参数化 SQL 写入 applied record，最后调用
连接的 `commit()`。脚本或元数据写入任一步失败都调用连接的 `rollback()`。这样既允许
trigger 等合法多语句 SQL，又保证 migration SQL 与 applied record 在同一个 SQLite
事务中原子提交。实现必须用故障注入测试证明失败时不留下半迁移 schema 或伪造的
applied record，不得使用基于分号的朴素字符串切分 migration SQL。

并发 runner 通过 SQLite 写锁和 `busy_timeout` 串行化。锁等待超时映射为稳定 `DatabaseBusyError`，不向调用方暴露底层 SQL 或数据库路径。

## 9. SQLite 连接工厂

每个连接统一设置：

```text
PRAGMA foreign_keys = ON
PRAGMA busy_timeout = <配置毫秒数>
PRAGMA journal_mode = WAL
PRAGMA synchronous = NORMAL
row_factory = sqlite3.Row
isolation_level = None
```

连接规则：

- 保持 `sqlite3` 默认的同线程连接限制；
- 不跨线程共享连接；
- 不提供进程级全局连接；
- 不自建连接池；
- 数据库本地性由 `AppSettings` 统一验证；
- 连接工厂只接受已解析的本地 `Path`，不接受 DSN 或 provider 字符串；
- active SQLite 不允许位于 `business_root` 内或明显网络路径；
- central/PostgreSQL 配置由 CLI 明确拒绝为本阶段不支持，不静默回退 SQLite。

## 10. SQLite Unit of Work

事务模式：

- `mode="read"`：`BEGIN`；
- `mode="write"`：`BEGIN IMMEDIATE`。

状态机：

```text
new → active → committed → closed
             ↘ rolled_back → closed
```

规则：

- `__enter__()` 创建连接并开始事务；
- `connection` 仅在 active 状态可访问；
- 必须显式调用 `commit()` 才提交；
- 正常退出但未 commit 时回滚；
- 异常退出时回滚并保留原始业务异常传播；
- `commit()` 后禁止再次 commit、rollback 或访问活动连接；
- `rollback()` 结束当前活动事务；
- `__exit__()` 无论成功或失败都关闭连接；
- 进入前、退出后或已完成状态的非法操作抛出 `UnitOfWorkStateError`；
- 每个 UoW 独立连接，不共享未提交状态。

`SqliteUnitOfWork` 必须满足现有 `platform_core.ports.UnitOfWork` 运行时 Protocol。具体 Repository 在未来业务切片中使用其连接；本阶段不修改正式 Protocol。

## 11. Repository 测试夹具

测试专用内容：

- `FixtureEntity(id, value, parent_id)`；
- parent/child fixture 表；
- `FixtureRepository.get()`；
- `FixtureRepository.add()`。

验证：

- `add + commit` 后新连接可见；
- 未 commit 的正常退出自动回滚；
- 异常退出自动回滚；
- 唯一约束生效；
- 外键约束生效；
- 两个 UoW 不共享连接或未提交数据；
- 写锁超过 busy timeout 时返回 `DatabaseBusyError`；
- Fixture Repository 满足 `Repository[FixtureEntity]`；
- SQLite UoW 满足 `UnitOfWork`；
- fixture migration 和表不进入生产 migration 目录。

## 12. 一致性备份

备份使用 `sqlite3.Connection.backup()`，不使用普通文件复制。

流程：

1. 源数据库必须存在；
2. 检查 migration 和 schema 状态；
3. `too_new`、`tampered`、`invalid_catalog` 或 `corrupt` 时拒绝；
4. 执行 `PRAGMA integrity_check`；
5. 执行 `PRAGMA foreign_key_check`；
6. 在备份目标目录创建临时数据库；
7. 通过 SQLite Backup API 创建一致快照；
8. 对临时备份再次执行完整性、外键和 schema 检查；
9. 计算 SHA-256 和字节大小；
10. 原子移动到最终备份路径；
11. 原子写入同名 manifest。

Manifest 示例：

```json
{
  "format_version": 1,
  "schema_version": 0,
  "catalog_target_version": 0,
  "sha256": "<sha256>",
  "size_bytes": 123456,
  "created_at_utc": "2026-07-24T00:00:00Z",
  "sqlite_version": "<runtime-version>",
  "integrity_check": "ok"
}
```

Manifest 不包含源数据库路径、用户名、机器标识或业务文件名。备份文件或 manifest 已存在时拒绝覆盖。

## 13. Migration 前置备份

当 catalog 存在待执行 migration 时，CLI 必须提供：

```text
db migrate --backup-dir <本机备份目录>
```

规则：

- 在任何 migration 开始前创建并验证备份；
- 备份失败时不执行 migration；
- 从版本 `0` 执行第一份 migration 也不例外；
- 备份目录必须是节点本机目录；
- runner 的程序化 API 必须显式接收备份策略或备份结果，不能暗中跳过；
- 成功 migration 输出关联的备份标识，但不输出绝对路径。

## 14. 恢复验证

CLI：

```text
db verify-restore \
  --backup <backup.sqlite3> \
  --manifest <backup.manifest.json> \
  --target <新的恢复数据库路径>
```

规则：

- target 必须是新路径；
- target 不能等于当前活跃数据库；
- target 已存在时拒绝；
- 先验证 manifest 格式、文件大小和 SHA-256；
- 使用 SQLite Backup API 恢复到目标临时文件；
- 执行 `integrity_check`、`foreign_key_check` 和 migration 状态检查；
- 验证成功后原子生成最终 target；
- 不自动修改 Settings 或替换活跃数据库；
- 验证失败时清理未完成 target 和临时文件；
- 不删除或修改原备份。

## 15. CLI

入口：

```text
python -m infrastructure.database.cli <command>
```

全局参数：

```text
--config <project-manager.json>
--database <sqlite 文件>
--json
```

`load_app_settings()` 增加可选显式 `sqlite_path` 参数，其优先级为：

```text
CLI 显式路径
> PROJECT_MANAGER_SQLITE_PATH
> 本地 JSON
> 节点本机默认路径
```

所有来源都执行同一 SQLite 本地性检查。

命令：

```text
status
migrate --backup-dir <目录>
check
backup --output <文件>
verify-restore --backup <文件> --manifest <文件> --target <新文件>
```

结构化输出：

```json
{
  "schema_version": "database_cli.v1",
  "command": "status",
  "status": "current",
  "error_code": null,
  "details": {
    "current_version": 0,
    "target_version": 0,
    "pending_versions": []
  }
}
```

CLI 不输出数据库绝对路径、SQL 内容、底层异常、用户名或配置文件内容。

退出码：

- `0`：成功且可用；
- `2`：需要运维动作，例如 pending 或 uninitialized；
- `3`：配置、catalog、too_new 或 checksum 不兼容；
- `4`：busy 或锁超时；
- `5`：migration、完整性、备份或恢复失败。

调用方必须读取 `error_code`，不得依赖人类文案。

## 16. 测试与验收

### 16.1 Catalog

- 空 catalog；
- 连续版本；
- 重号；
- 缺号；
- 非法命名；
- 非 UTF-8；
- migration 内含禁止的事务控制；
- SHA-256 稳定。

### 16.2 Migration

- 首次初始化；
- 版本 `0` current；
- 按序升级；
- 重复 migrate 幂等；
- checksum 篡改；
- 已应用文件缺失或重命名；
- 数据库版本过新；
- 当前 migration 失败回滚；
- 更早版本保留；
- 两个 runner 竞争；
- busy timeout；
- 未提供前置备份时拒绝待迁移。

### 16.3 Unit of Work 与 Repository

- 显式 commit；
- 正常退出默认 rollback；
- 异常 rollback；
- 唯一与外键约束；
- 独立连接和未提交隔离；
- 状态机非法调用；
- Protocol 运行时检查。

### 16.4 Backup/restore

- 一致性快照；
- manifest 与 SHA-256；
- 备份目标冲突；
- manifest 篡改；
- 备份字节篡改；
- schema 过新；
- 外键或完整性失败；
- 恢复目标冲突；
- 中断后临时文件清理；
- 恢复到新路径后完整验证。

### 16.5 CLI 与回归

- 人类输出和 JSON 输出；
- 稳定退出码和 error code；
- central/PostgreSQL 明确 unsupported；
- 显式 SQLite 路径优先级；
- 不泄漏数据库路径或底层异常；
- Windows 默认编码全量测试；
- Windows/macOS 兼容的路径测试；
- UTF-8 全量测试；
- 现有 `main.run()` 和工具回归；
- repository governance；
- 秘密和个人路径扫描；
- `git diff --check` 与干净工作树。

## 17. 完成标准

只有同时满足以下条件，本阶段才完成：

- 不增加第三方运行依赖；
- 正式 migration 目录不含业务表；
- Migration Catalog、Runner、Connection Factory、UoW、Backup 和 CLI 职责分离；
- migration 是 forward-only、显式执行且校验和不可变；
- 待迁移前备份强制执行并验证；
- SQLite 外键、WAL、busy timeout 和事务行为经过真实集成测试；
- UoW 默认不自动提交；
- 备份和恢复不覆盖现有文件；
- 恢复结果通过完整性、外键和 schema 校验；
- CLI 结构化输出无秘密和物理路径泄漏；
- central/PostgreSQL 不被 SQLite CLI 静默处理；
- 现有应用不因数据库尚未承载业务而被启动门禁阻断；
- 普通 Windows 编码与 UTF-8 两套全量测试均通过；
- 治理、仓库卫生和 Git 检查通过。

## 18. 后续阶段接口

本阶段完成后，后续工作按以下顺序继续：

1. 选择第一个真实业务纵向切片；
2. 编写正式 `0001_<business_slice>.sql`；
3. 实现该领域的 SQLite Repository；
4. 只对使用该 Repository 的能力增加 schema 启动门禁；
5. 验证文件账本到 SQL 的一次性迁移方案，避免双权威长期并存；
6. 基于已经验证的领域契约设计 PostgreSQL 17 方言迁移、连接池、migration owner 和备份验收；
7. PostgreSQL 控制平面落地后，异地执行节点仍只能通过 API 访问权威状态，不直接连接数据库。

Phase 2 的 SQLite `external_ref`、PageIndex ArtifactStore 逻辑 URI和结构树深度不可变不属于本数据库内核切片；这些继续作为后续业务/ArtifactStore 设计债务管理。
