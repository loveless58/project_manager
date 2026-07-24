# SQLite Database Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不承载业务表的 SQLite 数据库内核，提供显式 migration、事务化 Unit of Work、一致性备份、恢复验证和稳定 CLI 契约。

**Architecture:** `platform_core` 只保存跨后端契约和配置；`infrastructure.database` 保存数据库无关结果/错误与 migration catalog；`infrastructure.database.sqlite` 保存 SQLite 连接、状态、runner、UoW、备份和恢复实现。数据库 CLI 独立运行，本阶段不接入 `main.run()`，生产 migration catalog 保持版本 `0`，Repository 行为只用测试夹具验证。

**Tech Stack:** Python 标准库（`sqlite3`、`argparse`、`dataclasses`、`enum`、`hashlib`、`json`、`pathlib`、`tempfile`、`time`）、pytest；不增加运行依赖。

## Global Constraints

- Python 实现必须兼容仓库当前 Windows Python 3.13 测试环境，并保持 macOS 可移植性。
- 只使用标准库 `sqlite3`；不得引入 SQLAlchemy、Alembic、ORM 或新的运行依赖。
- migration 只能显式执行、forward-only、从 `0001` 严格连续、UTF-8、SHA-256 不可变。
- 正式 `migrations/sqlite/` 本阶段不包含业务 `.sql` 文件，目标 schema 版本为 `0`。
- 不修改 `main.run()` 的启动路径，也不把数据库门禁加到现有 OCR、文件整理、CRM 或项目工具。
- 每个 UoW 使用独立连接；读事务使用 `BEGIN`，写事务使用 `BEGIN IMMEDIATE`，未显式 commit 必须 rollback。
- 待执行 migration 前必须有已验证备份，包括从版本 `0` 执行第一份 migration。
- CLI 和公开异常不得泄漏数据库绝对路径、完整 SQL、配置内容、用户名、秘密或底层异常全文。
- central/PostgreSQL 必须返回 `DB.UNSUPPORTED_PROVIDER`，不得静默回退 SQLite。
- 备份和恢复不得覆盖现有文件；恢复只能落到新路径，不自动替换活跃数据库或修改 Settings。
- 普通 Windows 编码、`PYTHONUTF8=1`、治理扫描、`git diff --check` 都必须通过。

---

## File Structure

| Path | Responsibility |
|---|---|
| `platform_core/settings.py` | 增加显式 `sqlite_path` 覆盖参数，保持统一本地性校验。 |
| `infrastructure/__init__.py` | 基础设施包边界。 |
| `infrastructure/database/__init__.py` | 数据库内核公共导出。 |
| `infrastructure/database/contracts.py` | 不可变 DTO、状态枚举、稳定错误及 error code。 |
| `infrastructure/database/migration_catalog.py` | 发现、校验、排序、解码和校验 migration 文件。 |
| `infrastructure/database/cli.py` | 参数解析、命令编排、公开输出和退出码。 |
| `infrastructure/database/sqlite/connection.py` | SQLite 连接创建、PRAGMA、异常分类。 |
| `infrastructure/database/sqlite/schema.py` | 引导表、applied record 读取、schema 状态判定和完整性检查。 |
| `infrastructure/database/sqlite/migration_runner.py` | 初始化和原子执行 pending migration。 |
| `infrastructure/database/sqlite/unit_of_work.py` | 严格状态机和每事务独立连接。 |
| `infrastructure/database/sqlite/backup.py` | Backup API、manifest、校验、无覆盖发布。 |
| `infrastructure/database/sqlite/restore.py` | 恢复到新路径并完整验证。 |
| `migrations/sqlite/README.md` | 正式 catalog 规则；本阶段没有正式 SQL migration。 |
| `tests/database/fixtures/migrations/*.sql` | 只供测试的多版本 schema。 |
| `tests/database/fixture_repository.py` | 测试专用实体和 Repository。 |
| `tests/database/test_*.py` | 真实 SQLite 集成测试与 CLI 契约测试。 |

---

### Task 1: Settings Override and Stable Database Contracts

**Files:**
- Modify: `platform_core/settings.py`
- Create: `infrastructure/__init__.py`
- Create: `infrastructure/database/__init__.py`
- Create: `infrastructure/database/contracts.py`
- Create: `tests/database/__init__.py`
- Create: `tests/database/test_contracts.py`
- Modify: `tests/platform_core/test_settings.py`

**Interfaces:**
- Consumes: existing `load_app_settings(config_file, environ, business_root, runtime_workspace)` and `DatabaseSettings`.
- Produces: `load_app_settings(config_file, environ, business_root, runtime_workspace, sqlite_path) -> AppSettings`; `SchemaState`; `MigrationInfo`; `AppliedMigration`; `SchemaStatus`; `BackupManifest`; stable `DatabaseError` subclasses.

- [ ] **Step 1: Write failing settings-priority and contract tests**

Add tests that use four distinct paths and assert the exact priority `explicit > environment > JSON > default`:

```python
def test_explicit_sqlite_path_overrides_environment_and_json(tmp_path):
    config = tmp_path / "project-manager.local.json"
    config.write_text(
        json.dumps({"database": {"sqlite_path": str(tmp_path / "json.sqlite3")}}),
        encoding="utf-8",
    )
    settings = load_app_settings(
        config_file=config,
        environ={"PROJECT_MANAGER_SQLITE_PATH": str(tmp_path / "env.sqlite3")},
        runtime_workspace=tmp_path / "runtime",
        business_root=tmp_path / "business",
        sqlite_path=tmp_path / "explicit.sqlite3",
    )
    assert settings.database.sqlite_path == (tmp_path / "explicit.sqlite3").resolve()


def test_explicit_sqlite_path_uses_existing_locality_validation(tmp_path):
    business_root = tmp_path / "business"
    with pytest.raises(SettingsError, match="sqlite_path must not be inside business_root"):
        load_app_settings(
            config_file="",
            environ={},
            business_root=business_root,
            runtime_workspace=tmp_path / "runtime",
            sqlite_path=business_root / "state.sqlite3",
        )
```

In `tests/database/test_contracts.py`, construct each frozen DTO, assert mutation raises `FrozenInstanceError`, and assert exact codes:

```python
@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (DatabaseConfigurationError, "DB.CONFIGURATION"),
        (MigrationCatalogError, "DB.MIGRATION_CATALOG"),
        (MigrationChecksumError, "DB.MIGRATION_CHECKSUM"),
        (SchemaTooNewError, "DB.SCHEMA_TOO_NEW"),
        (MigrationExecutionError, "DB.MIGRATION_EXECUTION"),
        (DatabaseBusyError, "DB.BUSY"),
        (DatabaseIntegrityError, "DB.INTEGRITY"),
        (BackupError, "DB.BACKUP"),
        (RestoreVerificationError, "DB.RESTORE_VERIFICATION"),
        (UnitOfWorkStateError, "DB.UOW_STATE"),
        (UnsupportedDatabaseProviderError, "DB.UNSUPPORTED_PROVIDER"),
    ],
)
def test_database_errors_have_stable_public_codes(error_type, code):
    error = error_type("safe public message")
    assert error.code == code
    assert error.public_message == "safe public message"
```

- [ ] **Step 2: Run focused tests and verify the new API is absent**

Run: `python -m pytest tests/platform_core/test_settings.py tests/database/test_contracts.py -q`

Expected: FAIL because `sqlite_path` and `infrastructure.database.contracts` do not exist.

- [ ] **Step 3: Implement the settings override and exact contract surface**

Change the settings signature and `_pick` call to:

The resulting signature is:

```text
load_app_settings(config_file=None, environ=None, business_root=None,
                  runtime_workspace=None, sqlite_path=None) -> AppSettings
```

Replace the existing `raw_sqlite_path` assignment with this exact code:

```python
raw_sqlite_path = _pick(
    sqlite_path,
    env,
    "PROJECT_MANAGER_SQLITE_PATH",
    local_database.get("sqlite_path"),
    resolved_runtime / "state" / "project_manager.sqlite3"
    if database_provider == "sqlite"
    else None,
)
```

Define the public types in `contracts.py` with frozen, slotted dataclasses and no path in public serialization:

```python
class SchemaState(str, Enum):
    CURRENT = "current"
    PENDING = "pending"
    UNINITIALIZED = "uninitialized"
    TOO_NEW = "too_new"
    TAMPERED = "tampered"
    INVALID_CATALOG = "invalid_catalog"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class MigrationInfo:
    version: int
    name: str
    checksum_sha256: str
    path: Path


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    checksum_sha256: str
    applied_at_utc: str
    execution_ms: int


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    state: SchemaState
    current_version: int
    target_version: int
    pending_versions: tuple[int, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: int
    schema_version: int
    catalog_target_version: int
    sha256: str
    size_bytes: int
    created_at_utc: str
    sqlite_version: str
    integrity_check: str


class DatabaseError(RuntimeError):
    code = "DB.OPERATION_FAILED"

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message
```

Define one subclass per tested code. Export all DTOs and errors from `infrastructure/database/__init__.py`.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/platform_core/test_settings.py tests/database/test_contracts.py -q`

Expected: all focused tests PASS.

- [ ] **Step 5: Commit**

```bash
git add platform_core/settings.py infrastructure tests/database tests/platform_core/test_settings.py
git commit -m "feat: define database kernel contracts"
```

---

### Task 2: Immutable Migration Catalog

**Files:**
- Create: `infrastructure/database/migration_catalog.py`
- Create: `migrations/sqlite/README.md`
- Create: `tests/database/test_migration_catalog.py`
- Create: `tests/database/fixtures/migrations/0001_create_fixture_parent.sql`
- Create: `tests/database/fixtures/migrations/0002_create_fixture_entity.sql`

**Interfaces:**
- Consumes: `MigrationInfo`, `MigrationCatalogError`.
- Produces: `load_migration_catalog(directory: Path) -> tuple[MigrationInfo, ...]`; `catalog_target_version(catalog) -> int`.

- [ ] **Step 1: Write failing catalog tests**

Cover empty directory, two valid fixture files, numeric sorting, duplicate versions, gaps, version zero, illegal names, invalid UTF-8, stable binary SHA-256, and transaction-control rejection. Create the two shared fixture migrations with these exact contents:

```sql
-- 0001_create_fixture_parent.sql
CREATE TABLE fixture_parent (
    id TEXT PRIMARY KEY
);
```

```sql
-- 0002_create_fixture_entity.sql
CREATE TABLE fixture_entity (
    id TEXT PRIMARY KEY,
    value TEXT NOT NULL UNIQUE,
    parent_id TEXT,
    FOREIGN KEY (parent_id) REFERENCES fixture_parent(id)
);
```

Also include a trigger containing semicolons to prove execution SQL is not naïvely split:


```python
def test_catalog_accepts_trigger_body_without_splitting_sql(tmp_path):
    migration = tmp_path / "0001_create_audit_trigger.sql"
    migration.write_text(
        "CREATE TABLE item(id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE audit(item_id INTEGER);\n"
        "CREATE TRIGGER item_audit AFTER INSERT ON item BEGIN\n"
        "  INSERT INTO audit(item_id) VALUES (NEW.id);\n"
        "END;\n",
        encoding="utf-8",
    )
    catalog = load_migration_catalog(tmp_path)
    assert [item.version for item in catalog] == [1]


@pytest.mark.parametrize(
    "statement",
    ["BEGIN;", "COMMIT;", "ROLLBACK;", "SAVEPOINT x;", "RELEASE x;"],
)
def test_catalog_rejects_runner_transaction_escape(tmp_path, statement):
    (tmp_path / "0001_bad_transaction.sql").write_text(statement, encoding="utf-8")
    with pytest.raises(MigrationCatalogError, match="transaction control"):
        load_migration_catalog(tmp_path)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_migration_catalog.py -q`

Expected: FAIL because `load_migration_catalog` does not exist.

- [ ] **Step 3: Implement strict catalog loading**

Use `re.fullmatch(r"(?P<version>[0-9]{4})_(?P<name>[a-z][a-z0-9_]*)\.sql", filename)`, `path.read_bytes()`, `payload.decode("utf-8")`, and `hashlib.sha256(payload).hexdigest()`. Reject any sequence other than `range(1, len(items) + 1)`.

For forbidden transaction detection, mask single/double/backtick/bracket quoted text plus `--` and `/* block comment */` comments, then search statement starts with:

```python
_TRANSACTION_CONTROL = re.compile(
    r"(?:\A|;)\s*(?:BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b",
    re.IGNORECASE | re.MULTILINE,
)


def catalog_target_version(catalog: Sequence[MigrationInfo]) -> int:
    return catalog[-1].version if catalog else 0
```

The masking scanner must preserve semicolons and newlines outside quoted/comment regions and replace masked characters with spaces. An unterminated quoted string or block comment raises `MigrationCatalogError("migration SQL is not lexically complete")`. Do not split SQL for execution.

Write `migrations/sqlite/README.md` with the filename rule, UTF-8 requirement, forward-only/checksum contract, transaction-control ban, and the statement that this phase intentionally has no production `.sql` files.

- [ ] **Step 4: Run catalog tests**

Run: `python -m pytest tests/database/test_migration_catalog.py -q`

Expected: all catalog tests PASS.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/migration_catalog.py migrations/sqlite tests/database
git commit -m "feat: add immutable sqlite migration catalog"
```

---

### Task 3: Connection Factory, Bootstrap Metadata, and Schema Inspection

**Files:**
- Create: `infrastructure/database/sqlite/__init__.py`
- Create: `infrastructure/database/sqlite/connection.py`
- Create: `infrastructure/database/sqlite/schema.py`
- Create: `tests/database/test_sqlite_connection.py`
- Create: `tests/database/test_sqlite_schema.py`

**Interfaces:**
- Consumes: catalog tuple and Task 1 errors/DTOs.
- Produces: `SqliteConnectionOptions`; `open_sqlite_connection`; `initialize_schema_metadata`; `read_applied_migrations`; `inspect_schema`; `check_database_integrity`.

- [ ] **Step 1: Write failing connection and status tests**

Assert a newly created connection has `foreign_keys=1`, configured `busy_timeout`, `journal_mode="wal"`, `synchronous=1`, `sqlite3.Row`, and `isolation_level is None`. Assert `create=False` rejects a missing source without creating it.

Status tests must cover missing/uninitialized, initialized version 0 current, pending fixture catalog, checksum/name tampering, applied version above target, gapped metadata, and corrupt bytes.

```python
def test_initialized_empty_catalog_is_current(tmp_path):
    database = tmp_path / "state.sqlite3"
    initialize_schema_metadata(database)
    status = inspect_schema(database, ())
    assert status == SchemaStatus(
        state=SchemaState.CURRENT,
        current_version=0,
        target_version=0,
        pending_versions=(),
        error_code=None,
    )
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_sqlite_connection.py tests/database/test_sqlite_schema.py -q`

Expected: FAIL because the SQLite modules do not exist.

- [ ] **Step 3: Implement connection and schema APIs**

Use these exact signatures:

```python
@dataclass(frozen=True, slots=True)
class SqliteConnectionOptions:
    busy_timeout_ms: int = 5_000
```

Expose these exact callable signatures; the ordered rules below define each body completely:

```text
open_sqlite_connection(database_path: Path, *, options: SqliteConnectionOptions = SqliteConnectionOptions(),
                       create: bool = False) -> sqlite3.Connection
initialize_schema_metadata(database_path: Path, *,
                           options: SqliteConnectionOptions = SqliteConnectionOptions()) -> None
inspect_schema(database_path: Path, catalog: Sequence[MigrationInfo], *,
               options: SqliteConnectionOptions = SqliteConnectionOptions()) -> SchemaStatus
check_database_integrity(connection: sqlite3.Connection) -> None
```

`initialize_schema_metadata` creates exactly:

```sql
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    applied_at_utc TEXT NOT NULL,
    execution_ms INTEGER NOT NULL
)
```

Map lock errors containing SQLite error code `SQLITE_BUSY` or `SQLITE_LOCKED` to `DatabaseBusyError("database is busy")`. Map malformed/corrupt errors to `DatabaseIntegrityError("database integrity check failed")`. Preserve neither original exception text nor the path in the public exception.

`inspect_schema` returns `UNINITIALIZED` for a missing database or absent metadata table; validates metadata columns, consecutive applied versions, and catalog identity; uses `TOO_NEW` only when the highest applied version exceeds catalog target, otherwise uses `TAMPERED` for missing/renamed/checksum-mismatched applied files and `CORRUPT` for malformed metadata.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/database/test_sqlite_connection.py tests/database/test_sqlite_schema.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/sqlite tests/database
git commit -m "feat: inspect sqlite schema state"
```

---

### Task 4: Atomic Forward-Only Migration Runner

**Files:**
- Create: `infrastructure/database/sqlite/migration_runner.py`
- Create: `tests/database/test_sqlite_migrations.py`

**Interfaces:**
- Consumes: `BackupManifest`, catalog loader output, connection/schema APIs.
- Produces: `initialize_database`; `apply_pending_migrations`; `MigrationRunResult`.

- [ ] **Step 1: Write failing migration tests**

Test initialization idempotency, ordered upgrade, repeated no-op, missing backup authorization, backup schema mismatch, SQL failure rollback, metadata-insert failure rollback, earlier migration retention, trigger execution, checksum tampering, two competing runners, and busy timeout.

Use a fault hook only for tests, defaulting to `None`:

```python
def test_metadata_failure_rolls_back_schema_and_record(tmp_path, fixture_catalog):
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    authorization = BackupManifest(
        format_version=1,
        schema_version=0,
        catalog_target_version=2,
        sha256="0" * 64,
        size_bytes=1,
        created_at_utc="2026-07-24T00:00:00Z",
        sqlite_version=sqlite3.sqlite_version,
        integrity_check="ok",
    )

    def fail_before_record(connection, migration):
        raise RuntimeError("injected metadata failure")

    with pytest.raises(MigrationExecutionError, match="migration 1 failed"):
        apply_pending_migrations(
            database,
            fixture_catalog,
            backup_manifest=authorization,
            before_record_insert=fail_before_record,
        )

    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        records = connection.execute(
            "SELECT version FROM _schema_migrations ORDER BY version"
        ).fetchall()
    assert ("fixture_parent",) not in tables
    assert records == []
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_sqlite_migrations.py -q`

Expected: FAIL because the runner does not exist.

- [ ] **Step 3: Implement controlled transaction execution**

Use exact public signatures:

```python
@dataclass(frozen=True, slots=True)
class MigrationRunResult:
    previous_version: int
    current_version: int
    applied_versions: tuple[int, ...]
```

Expose these exact signatures:

```text
initialize_database(database_path: Path, *,
    options: SqliteConnectionOptions = SqliteConnectionOptions()) -> SchemaStatus
apply_pending_migrations(database_path: Path, catalog: Sequence[MigrationInfo], *,
    backup_manifest: BackupManifest | None,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
    before_record_insert: Callable[[sqlite3.Connection, MigrationInfo], None] | None = None)
    -> MigrationRunResult
```

For each pending migration:

1. Reopen a dedicated write connection.
2. Call `connection.executescript("BEGIN IMMEDIATE;\n" + migration_sql)` while no Python-managed transaction is active.
3. After the call returns, compute `execution_ms = max(0, round((perf_counter() - started) * 1000))`.
4. Invoke the optional fault hook.
5. Insert the applied record with parameterized `connection.execute(insert_sql, insert_parameters)` and UTC `YYYY-MM-DDTHH:MM:SSZ`.
6. Call `connection.commit()`.
7. On any exception while `connection.in_transaction`, call `connection.rollback()` before mapping the error.

Do not append `COMMIT` to the script and do not split migration SQL. Before the first migration and again after acquiring the write lock, re-read applied records and validate status. Require `backup_manifest.schema_version == current_version`, `backup_manifest.catalog_target_version == target_version`, `integrity_check == "ok"`, and a 64-character lowercase hexadecimal SHA-256. A no-op current database does not require a backup manifest.

- [ ] **Step 4: Run migration tests, including concurrency**

Run: `python -m pytest tests/database/test_sqlite_migrations.py -q`

Expected: all tests PASS; the competing runner either observes the committed version and returns no-op or receives `DB.BUSY` according to the configured timeout, without duplicate records.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/sqlite/migration_runner.py tests/database/test_sqlite_migrations.py
git commit -m "feat: apply atomic sqlite migrations"
```

---

### Task 5: Strict SQLite Unit of Work and Test-Only Repository

**Files:**
- Create: `infrastructure/database/sqlite/unit_of_work.py`
- Create: `tests/database/fixture_repository.py`
- Create: `tests/database/test_sqlite_uow.py`

**Interfaces:**
- Consumes: `open_sqlite_connection`, existing `platform_core.ports.Repository` and `UnitOfWork`.
- Produces: `SqliteUnitOfWork`; test-only `FixtureEntity` and `FixtureRepository`.

- [ ] **Step 1: Write failing state-machine and repository tests**

Use the two fixture migrations to create parent/entity tables with foreign key and unique constraints. Test commit visibility, clean-exit rollback, exception rollback, uniqueness, foreign keys, isolated connections, busy timeout, and every invalid state transition.

```python
def test_write_uow_requires_explicit_commit(database):
    with SqliteUnitOfWork(database, mode="write") as uow:
        FixtureRepository(uow.connection).add(FixtureEntity("e-1", "value", None))
    with SqliteUnitOfWork(database, mode="read") as uow:
        assert FixtureRepository(uow.connection).get("e-1") is None


def test_runtime_protocols_accept_sqlite_adapters(database):
    with SqliteUnitOfWork(database, mode="read") as uow:
        repository = FixtureRepository(uow.connection)
        assert isinstance(repository, Repository)
        assert isinstance(uow, UnitOfWork)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_sqlite_uow.py -q`

Expected: FAIL because `SqliteUnitOfWork` does not exist.

- [ ] **Step 3: Implement the exact UoW state machine**

Use:

```python
UowMode = Literal["read", "write"]
```

Implement this exact callable surface:

```text
SqliteUnitOfWork(database_path: Path, *, mode: UowMode = "read",
                 options: SqliteConnectionOptions = SqliteConnectionOptions())
SqliteUnitOfWork.__enter__() -> SqliteUnitOfWork
SqliteUnitOfWork.__exit__(exc_type, exc, traceback) -> None
SqliteUnitOfWork.connection -> sqlite3.Connection
SqliteUnitOfWork.commit() -> None
SqliteUnitOfWork.rollback() -> None
```

Represent states as internal enum `NEW`, `ACTIVE`, `COMMITTED`, `ROLLED_BACK`, `CLOSED`. `__enter__` opens a new connection and executes `BEGIN` or `BEGIN IMMEDIATE`. `commit` and `rollback` finalize only from `ACTIVE`; `connection` is accessible only in `ACTIVE`. `__exit__` rolls back any still-active transaction, never suppresses the original exception, always closes the connection, and ends in `CLOSED`. Public state errors use safe fixed messages.

Implement `FixtureRepository` only under `tests/database/`; use parameterized SQL and return `None` for a missing id. Do not add any production generic repository or table.

- [ ] **Step 4: Run UoW and Protocol tests**

Run: `python -m pytest tests/database/test_sqlite_uow.py tests/platform_core/test_ports.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/sqlite/unit_of_work.py tests/database
git commit -m "feat: add strict sqlite unit of work"
```

---

### Task 6: Consistent Backup and Manifest Publication

**Files:**
- Create: `infrastructure/database/sqlite/backup.py`
- Create: `tests/database/test_sqlite_backup.py`

**Interfaces:**
- Consumes: schema inspection/integrity functions and `BackupManifest`.
- Produces: `BackupResult`; `create_sqlite_backup`; `load_backup_manifest`; `verify_backup_artifacts`.

- [ ] **Step 1: Write failing backup tests**

Test WAL-consistent snapshot, source missing, too-new/tampered/invalid-catalog/corrupt refusal while allowing pending schema, foreign-key failure, preexisting backup, preexisting manifest, manifest fields, SHA/size, no source path leakage, and temp cleanup after injected failure.

```python
def test_backup_manifest_contains_no_physical_source_identity(backup_result, database):
    payload = backup_result.manifest_path.read_text(encoding="utf-8")
    assert str(database) not in payload
    assert set(json.loads(payload)) == {
        "format_version", "schema_version", "catalog_target_version",
        "sha256", "size_bytes", "created_at_utc", "sqlite_version",
        "integrity_check",
    }
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_sqlite_backup.py -q`

Expected: FAIL because backup APIs do not exist.

- [ ] **Step 3: Implement backup, manifest, and no-overwrite publication**

Use:

```python
@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_path: Path
    manifest_path: Path
    manifest: BackupManifest
```

Expose these exact signatures:

```text
create_sqlite_backup(database_path: Path, output_path: Path,
                     catalog: Sequence[MigrationInfo], *,
                     options: SqliteConnectionOptions = SqliteConnectionOptions()) -> BackupResult
load_backup_manifest(path: Path) -> BackupManifest
verify_backup_artifacts(backup_path: Path, manifest_path: Path) -> BackupManifest
```

Create both temp files in `output_path.parent`; call `source_connection.backup(destination_connection)`; validate backup integrity, foreign keys and schema; calculate SHA-256 by streaming 1 MiB chunks. Serialize manifest as sorted UTF-8 JSON with a trailing newline.

Publish without overwriting by creating temps on the same filesystem and using `os.link(temp_path, final_path)`, which atomically fails if the final name exists; then unlink the temp. Publish backup first and manifest second. If manifest publication fails, remove only the backup final created by this invocation. In `finally`, remove only operation-owned temp files. Convert all filesystem/SQLite failures to `BackupError` with fixed safe messages.

- [ ] **Step 4: Run backup tests**

Run: `python -m pytest tests/database/test_sqlite_backup.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/sqlite/backup.py tests/database/test_sqlite_backup.py
git commit -m "feat: create verified sqlite backups"
```

---

### Task 7: Restore Materialization and Verification

**Files:**
- Create: `infrastructure/database/sqlite/restore.py`
- Create: `tests/database/test_sqlite_restore.py`

**Interfaces:**
- Consumes: verified backup artifacts, schema/integrity APIs, catalog.
- Produces: `RestoreResult`; `verify_and_restore_sqlite`.

- [ ] **Step 1: Write failing restore tests**

Test valid restore to a new path, target equals active database, existing target, corrupt manifest JSON, wrong format version, size/hash mismatch, schema mismatch, foreign-key failure, corrupt backup, and cleanup after injected verification failure.

```python
def test_restore_never_switches_or_overwrites_active_database(
    database, backup_result, catalog
):
    original_hash = sha256_file(database)
    with pytest.raises(RestoreVerificationError, match="target must be a new path"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            database,
            active_database_path=database,
            catalog=catalog,
        )
    assert sha256_file(database) == original_hash
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_sqlite_restore.py -q`

Expected: FAIL because restore APIs do not exist.

- [ ] **Step 3: Implement verified restore to a new path**

Use:

```python
@dataclass(frozen=True, slots=True)
class RestoreResult:
    schema_version: int
    sha256: str
    size_bytes: int
```

Expose this exact signature:

```text
verify_and_restore_sqlite(backup_path: Path, manifest_path: Path, target_path: Path, *,
    active_database_path: Path, catalog: Sequence[MigrationInfo],
    options: SqliteConnectionOptions = SqliteConnectionOptions()) -> RestoreResult
```

Resolve paths before equality checks. Require absent target and target different from active. Verify manifest format/version/hash/size before opening SQLite. Restore through `backup_connection.backup(temp_target_connection)`, not file copy. Validate temp target integrity, foreign keys, and exact schema version/checksums; atomically publish with the same no-overwrite hard-link helper. Never modify settings, active database, backup, or manifest. Remove operation-owned temp target on every failure.

- [ ] **Step 4: Run restore tests**

Run: `python -m pytest tests/database/test_sqlite_restore.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/sqlite/restore.py tests/database/test_sqlite_restore.py
git commit -m "feat: verify sqlite restore candidates"
```

---

### Task 8: Standalone Database Operations CLI

**Files:**
- Create: `infrastructure/database/cli.py`
- Create: `tests/database/test_database_cli.py`

**Interfaces:**
- Consumes: settings, catalog, schema, runner, backup, restore.
- Produces: `python -m infrastructure.database.cli`; commands `status`, `migrate`, `check`, `backup`, `verify-restore`.

- [ ] **Step 1: Write failing CLI contract tests**

Call `main(argv, stdout, stderr)` directly for deterministic tests and use one subprocess smoke test for `python -m`. Assert JSON envelope fields, human output, every exit-code category, unsupported PostgreSQL, mandatory migration backup, explicit path priority, and redaction of paths/SQL/raw exceptions.

```python
def test_json_status_envelope_has_stable_schema(tmp_path):
    stdout = io.StringIO()
    code = main(
        ["--database", str(tmp_path / "state.sqlite3"), "--json", "status"],
        stdout=stdout,
        stderr=io.StringIO(),
    )
    payload = json.loads(stdout.getvalue())
    assert code == 2
    assert payload == {
        "schema_version": "database_cli.v1",
        "command": "status",
        "status": "uninitialized",
        "error_code": None,
        "details": {
            "current_version": 0,
            "target_version": 0,
            "pending_versions": [],
        },
    }
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/database/test_database_cli.py -q`

Expected: FAIL because the CLI module does not exist.

- [ ] **Step 3: Implement parsing, orchestration, and redaction**

Define the exit constants exactly:

```python
EXIT_OK = 0
EXIT_ACTION_REQUIRED = 2
EXIT_INCOMPATIBLE = 3
EXIT_BUSY = 4
EXIT_OPERATION_FAILED = 5
```

Build the parser with one required subparser and the exact global/command options listed below.
The public callable signature is:

```text
main(argv: Sequence[str] | None = None, *, stdout: TextIO = sys.stdout,
     stderr: TextIO = sys.stderr) -> int
```

The module terminates through:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

Global options are `--config`, `--database`, and `--json`. Commands and required options are:

```text
status
migrate --backup-dir DIRECTORY
check
backup --output FILE
verify-restore --backup FILE --manifest FILE --target FILE
```

Load the fixed production catalog from repository `migrations/sqlite`. For `migrate`: initialize metadata; inspect; if pending, generate a timestamp-and-random-token backup filename under `--backup-dir`, create and verify backup, then pass its manifest to `apply_pending_migrations`. Never use a path supplied only by the migration runner as proof of backup.

Map exit codes exactly: success/current `0`; pending/uninitialized `2`; configuration/catalog/too-new/checksum/unsupported `3`; busy `4`; migration/integrity/backup/restore failure `5`. Emit `database_cli.v1` with only logical status, versions, pending versions, backup SHA prefix identifier, counts, and stable `error_code`; do not include paths. Catch only known `DatabaseError` and `SettingsError`; convert settings to `DB.CONFIGURATION`; convert any unexpected exception at the top boundary to `DB.OPERATION_FAILED` with `"database operation failed"` and no raw text.

- [ ] **Step 4: Run CLI tests and command smoke tests**

Run: `python -m pytest tests/database/test_database_cli.py -q`

Run: `python -m infrastructure.database.cli --database .tmp-cli-state.sqlite3 --json status`

Expected: tests PASS; smoke command exits `2`, emits valid `database_cli.v1`, and does not create `.tmp-cli-state.sqlite3` for a read-only status.

Remove the smoke-test file only if it was created by a failed implementation attempt; verify its resolved path is exactly the repository-root `.tmp-cli-state.sqlite3` before removal.

- [ ] **Step 5: Commit**

```bash
git add infrastructure/database/cli.py tests/database/test_database_cli.py
git commit -m "feat: add sqlite database operations cli"
```

---

### Task 9: Public Exports, Documentation, and Full Verification

**Files:**
- Modify: `infrastructure/database/__init__.py`
- Modify: `infrastructure/database/sqlite/__init__.py`
- Modify: `README.md`
- Create: `tests/database/test_public_api.py`
- Create: `tests/database/test_database_docs.py`

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: reviewed public import surface and operator-facing commands without wiring database into `main.run()`.

- [ ] **Step 1: Write failing public API and documentation tests**

Assert intended imports work, private fault hooks are not exported, production catalog contains no `.sql` files, README documents explicit migration and restore safety, and `main.py` does not import `infrastructure.database`.

```python
def test_production_catalog_stays_at_version_zero():
    root = Path(__file__).resolve().parents[2]
    assert list((root / "migrations" / "sqlite").glob("*.sql")) == []


def test_main_remains_independent_of_database_kernel():
    root = Path(__file__).resolve().parents[2]
    source = (root / "main.py").read_text(encoding="utf-8")
    assert "infrastructure.database" not in source
```

- [ ] **Step 2: Run documentation/API tests and verify failure**

Run: `python -m pytest tests/database/test_public_api.py tests/database/test_database_docs.py -q`

Expected: FAIL until exports and README are complete.

- [ ] **Step 3: Finalize exports and operator documentation**

Export only stable DTOs/errors/catalog functions and supported SQLite operations. Keep `before_record_insert` documented as test fault injection and do not export it as a separate helper. Add README commands using environment-neutral examples such as `runtime/state.sqlite3` and `runtime/backups`; state that PostgreSQL is a later provider and that restore creates a candidate database requiring explicit operator switch.

- [ ] **Step 4: Run all focused database tests**

Run: `python -m pytest tests/database tests/platform_core/test_settings.py tests/platform_core/test_ports.py -q`

Expected: all database and affected platform tests PASS.

- [ ] **Step 5: Run the complete suite in normal Windows encoding**

Run: `python -m pytest -q`

Expected: all tests PASS with only previously accepted warnings/skips; no new warning category.

- [ ] **Step 6: Run the complete suite in UTF-8 mode**

PowerShell:

```powershell
$env:PYTHONUTF8 = "1"
python -m pytest -q
Remove-Item Env:PYTHONUTF8
```

Expected: same pass/skip result as the normal run.

- [ ] **Step 7: Run governance and repository hygiene**

Run: `python governance/validate.py all`

Expected: `0 errors, 0 warnings`, with no decode errors and no skipped non-target text.

- [ ] **Step 8: Run final Git checks**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; only the intended Task 9 files are uncommitted.

- [ ] **Step 9: Commit final documentation and exports**

```bash
git add README.md infrastructure/database tests/database
git commit -m "docs: document sqlite database operations"
```

- [ ] **Step 10: Verify clean branch and commit sequence**

Run: `git status --short`

Run: `git log --oneline -12`

Expected: clean worktree and nine implementation commits following the two approved design commits; no generated database, WAL, SHM, backup, manifest, log, cache, or personal-path artifact is tracked.

---

## Spec Coverage Matrix

| Approved requirement | Implemented by |
|---|---|
| stdlib `sqlite3`, no ORM/dependency | Tasks 1–9 global constraint and full dependency diff review |
| explicit, immutable migration catalog | Tasks 2 and 4 |
| controlled `executescript()` transaction boundary | Task 4 failure-injection and trigger tests |
| schema states and stable errors | Tasks 1 and 3 |
| per-UoW connection and explicit commit | Task 5 |
| test-only Repository/entity | Task 5 |
| Backup API, manifest, SHA/size, no overwrite | Task 6 |
| mandatory pre-migration backup | Tasks 4 and 8 |
| restore to new target with full validation | Task 7 |
| standalone CLI, output schema, exit codes, redaction | Task 8 |
| explicit SQLite path priority and locality | Task 1 |
| PostgreSQL unsupported without fallback | Task 8 |
| production schema version 0 and no business table | Tasks 2 and 9 |
| no `main.run()` startup gate | Task 9 |
| Windows/macOS portability, normal/UTF-8, governance | Task 9 |
