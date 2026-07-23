# 平台配置与基础接口边界实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立跨 Windows/macOS、单机 SQLite 和群晖 PostgreSQL 17 共用的配置与基础端口层，移除默认盘符和 PageIndex 本机路径硬编码，同时保持现有工具通过兼容门面运行。

**Architecture:** 新增独立的 `platform_core` 包承载不可依赖具体基础设施的 Settings、数据传输类型、端口协议、显式适配器注册表和组合根；现有 `common.workspace_config` 变为兼容门面。首个计划只落地本地 DocumentStore、文件系统 ProjectionWriter、PageIndex StructureIndex 适配器和运行时装配，不创建数据库表、不迁移账本、不实现异地节点协议。

**Tech Stack:** Python 标准库 dataclasses/typing/pathlib/json/hashlib/tempfile，现有 pytest/unittest 测试体系，现有 PageIndexClient，Git 小步提交。

**Reference spec:** `docs/superpowers/specs/2026-07-23-multi-agent-data-platform-design.md`

## Global Constraints

- 核心业务不得依赖 Windows 盘符、macOS `/Volumes` 路径、固定 NAS 根目录或 PageIndex 本机安装目录。
- 单机默认数据根为 `Path.home() / "ProjectManagerData"`，不得默认为仓库目录或同步盘。
- 配置优先级固定为：显式参数、环境变量、本地配置文件、跨平台默认值。
- `PostgreSQL 17.19-4` 只作为群晖安装包标识；实际数据库能力以后以 `SHOW server_version_num` 验证主版本 17。
- 配置文件只保存数据库 DSN 的环境变量名，禁止保存数据库口令、API Key、Cookie、TLS 私钥或节点令牌。
- SQLite 文件不得放入 SynologyDrive 活跃同步目录或网络共享供多机共同打开。
- 适配器必须显式注册；禁止根据字符串导入任意 Python 模块。
- DocumentStore、StructureIndex、ProjectionWriter 和 Repository/UnitOfWork 是端口，不是 Agent。
- 本计划不得改写 `ProjectLedger` 权威源、创建 SQL Schema、实现任务租约或加入浏览器自动化。
- 每个任务遵循红—绿—重构循环，只暂存本任务列出的文件并单独提交。

---

## 文件结构与职责

本计划创建或调整以下结构：

```text
platform_core/
  __init__.py                 # 对外导出稳定类型和构建入口
  settings.py                 # 跨平台配置加载、校验、脱敏摘要
  models.py                   # 端口之间传递的不可变数据类型
  registry.py                 # 显式适配器工厂注册与解析
  composition.py              # 根据 Settings 装配运行时适配器
  ports/
    __init__.py
    repositories.py           # Repository / UnitOfWork 最小事务契约
    document_store.py         # 原始文件只读访问契约
    structure_index.py        # PageIndex 等结构索引契约
    projection_writer.py      # JSON/Markdown/HTML 派生投影写入契约

integrations/
  document_store/
    __init__.py
    local_store.py            # 受根目录约束的本地文件适配器
    disabled_store.py         # 中心控制平面无文件挂载时的显式禁用适配器
  projections/
    __init__.py
    filesystem_writer.py      # 原子写入本地投影
  pageindex/
    structure_index.py        # 现有 PageIndexClient 到 StructureIndex 的映射

config/
  project-manager.example.json # 无密钥的跨平台配置样例
```

`platform_core` 不导入 `main.py`、`tools`、`ledger` 或任何具体 Provider。`integrations` 可以依赖 `platform_core.ports` 和 `platform_core.models`，反向依赖禁止。

---

### Task 1: 跨平台 Settings 与 WorkspaceConfig 兼容门面

**Files:**
- Create: `platform_core/__init__.py`
- Create: `platform_core/settings.py`
- Create: `tests/platform_core/__init__.py`
- Create: `tests/platform_core/test_settings.py`
- Modify: `common/workspace_config.py:1-103`
- Modify: `common/__init__.py:8,27-29`
- Modify: `tests/test_workspace_config.py:1-122`

**Interfaces:**
- Consumes: JSON 本地配置、`PROJECT_MANAGER_*` 环境变量和可选显式路径参数。
- Produces: `load_app_settings(...) -> AppSettings`、`AppSettings.redacted_summary() -> Dict[str, object]`、兼容的 `resolve_workspace_config(...) -> WorkspaceConfig`。

- [ ] **Step 1: 写 Settings 解析和校验失败测试**

创建 `tests/platform_core/__init__.py` 为空文件，并创建 `tests/platform_core/test_settings.py`：

```python
import json
from pathlib import Path

import pytest


def test_portable_local_defaults_do_not_contain_windows_drive(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    from platform_core.settings import load_app_settings

    settings = load_app_settings(config_file="", environ={})

    assert settings.deployment_mode == "local"
    assert settings.database.provider == "sqlite"
    assert settings.business_root == tmp_path / "ProjectManagerData"
    assert settings.runtime_workspace == tmp_path / "ProjectManagerData" / ".project_manager"
    assert "E:\\" not in str(settings.business_root)


def test_environment_overrides_json_and_defaults(tmp_path):
    config_file = tmp_path / "project-manager.local.json"
    config_file.write_text(
        json.dumps(
            {
                "deployment_mode": "local",
                "business_root": str(tmp_path / "json-business"),
                "runtime_workspace": str(tmp_path / "json-runtime"),
            }
        ),
        encoding="utf-8",
    )
    from platform_core.settings import load_app_settings

    settings = load_app_settings(
        config_file=config_file,
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "env-business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "env-runtime"),
        },
    )

    assert settings.business_root == (tmp_path / "env-business").resolve()
    assert settings.runtime_workspace == (tmp_path / "env-runtime").resolve()


def test_central_profile_selects_postgresql_without_reading_secret(tmp_path):
    from platform_core.settings import load_app_settings

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            "PROJECT_MANAGER_DATABASE_DSN_ENV": "PM_DATABASE_DSN",
            "PM_DATABASE_DSN": "postgresql://secret-user:secret-pass@nas/project_manager",
        },
    )

    assert settings.database.provider == "postgresql"
    assert settings.database.dsn_env_var == "PM_DATABASE_DSN"
    assert "secret-pass" not in repr(settings)
    assert "secret-pass" not in json.dumps(settings.redacted_summary())


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        (
            {
                "PROJECT_MANAGER_DEPLOYMENT_MODE": "local",
                "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            },
            "local deployment requires sqlite",
        ),
        (
            {
                "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
                "PROJECT_MANAGER_DATABASE_PROVIDER": "sqlite",
            },
            "central deployment requires postgresql",
        ),
        (
            {"PROJECT_MANAGER_STRUCTURE_INDEX": "pageindex"},
            "pageindex_dir is required",
        ),
    ],
)
def test_invalid_settings_fail_with_stable_message(environ, message):
    from platform_core.settings import SettingsError, load_app_settings

    with pytest.raises(SettingsError, match=message):
        load_app_settings(config_file="", environ=environ)
```

- [ ] **Step 2: 运行测试并确认模块尚不存在**

Run:

```bash
python -m pytest tests/platform_core/test_settings.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'platform_core'`。

- [ ] **Step 3: 实现 Settings 数据类型、加载顺序和校验**

创建 `platform_core/settings.py`：

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union


PathLike = Union[str, os.PathLike]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "project-manager.local.json"
LEGACY_CONFIG_FILE = PROJECT_ROOT / "config" / "workspace.local.json"


class SettingsError(ValueError):
    pass


def _path(value: Optional[PathLike]) -> Optional[Path]:
    if value is None or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _read_config(config_file: Optional[PathLike]) -> Dict[str, Any]:
    if config_file == "":
        return {}
    if config_file is not None:
        path = Path(config_file).expanduser()
    elif DEFAULT_CONFIG_FILE.exists():
        path = DEFAULT_CONFIG_FILE
    else:
        path = LEGACY_CONFIG_FILE
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SettingsError("configuration root must be an object")
    return payload


def _pick(explicit: Any, env: Mapping[str, str], env_name: str, local: Any, default: Any) -> Any:
    if explicit is not None and str(explicit).strip():
        return explicit
    env_value = env.get(env_name)
    if env_value is not None and env_value.strip():
        return env_value
    if local is not None and str(local).strip():
        return local
    return default


@dataclass(frozen=True)
class DatabaseSettings:
    provider: str
    sqlite_path: Optional[Path]
    dsn_env_var: str

    def __repr__(self) -> str:
        return (
            "DatabaseSettings("
            f"provider={self.provider!r}, sqlite_path={self.sqlite_path!r}, "
            f"dsn_env_var={self.dsn_env_var!r})"
        )


@dataclass(frozen=True)
class ProviderSettings:
    document_store: str
    structure_index: str
    pageindex_dir: Optional[Path]
    projection_writer: str
    projection_root: Path


@dataclass(frozen=True)
class AppSettings:
    deployment_mode: str
    business_root: Optional[Path]
    runtime_workspace: Path
    database: DatabaseSettings
    providers: ProviderSettings

    def redacted_summary(self) -> Dict[str, object]:
        return {
            "deployment_mode": self.deployment_mode,
            "business_root": str(self.business_root) if self.business_root else None,
            "runtime_workspace": str(self.runtime_workspace),
            "database": {
                "provider": self.database.provider,
                "sqlite_path": str(self.database.sqlite_path) if self.database.sqlite_path else None,
                "dsn_env_var": self.database.dsn_env_var,
            },
            "providers": {
                "document_store": self.providers.document_store,
                "structure_index": self.providers.structure_index,
                "pageindex_dir": str(self.providers.pageindex_dir) if self.providers.pageindex_dir else None,
                "projection_writer": self.providers.projection_writer,
                "projection_root": str(self.providers.projection_root),
            },
        }


def load_app_settings(
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    business_root: Optional[PathLike] = None,
    runtime_workspace: Optional[PathLike] = None,
) -> AppSettings:
    env = dict(os.environ if environ is None else environ)
    local = _read_config(config_file)
    local_database = local.get("database", {}) if isinstance(local.get("database", {}), dict) else {}
    local_providers = local.get("providers", {}) if isinstance(local.get("providers", {}), dict) else {}

    mode = str(
        _pick(None, env, "PROJECT_MANAGER_DEPLOYMENT_MODE", local.get("deployment_mode"), "local")
    ).lower()
    if mode not in {"local", "central"}:
        raise SettingsError("deployment_mode must be local or central")

    default_root = Path.home() / "ProjectManagerData"
    resolved_business_root = _path(
        _pick(business_root, env, "PROJECT_MANAGER_BUSINESS_ROOT", local.get("business_root"), default_root if mode == "local" else None)
    )
    default_runtime = (
        resolved_business_root / ".project_manager"
        if resolved_business_root is not None
        else Path.home() / ".project_manager"
    )
    resolved_runtime = _path(
        _pick(runtime_workspace, env, "PROJECT_MANAGER_WORKSPACE_DIR", local.get("runtime_workspace"), default_runtime)
    )
    assert resolved_runtime is not None

    default_database = "sqlite" if mode == "local" else "postgresql"
    database_provider = str(
        _pick(None, env, "PROJECT_MANAGER_DATABASE_PROVIDER", local_database.get("provider"), default_database)
    ).lower()
    sqlite_path = _path(
        _pick(None, env, "PROJECT_MANAGER_SQLITE_PATH", local_database.get("sqlite_path"), resolved_runtime / "state" / "project_manager.sqlite3" if database_provider == "sqlite" else None)
    )
    dsn_env_var = str(
        _pick(None, env, "PROJECT_MANAGER_DATABASE_DSN_ENV", local_database.get("dsn_env_var"), "PROJECT_MANAGER_DATABASE_DSN")
    )

    document_store = str(
        _pick(None, env, "PROJECT_MANAGER_DOCUMENT_STORE", local_providers.get("document_store"), "local" if resolved_business_root else "disabled")
    ).lower()
    structure_index = str(
        _pick(None, env, "PROJECT_MANAGER_STRUCTURE_INDEX", local_providers.get("structure_index"), "disabled")
    ).lower()
    pageindex_dir = _path(
        _pick(None, env, "PROJECT_MANAGER_PAGEINDEX_DIR", local_providers.get("pageindex_dir"), None)
    )
    projection_writer = str(
        _pick(None, env, "PROJECT_MANAGER_PROJECTION_WRITER", local_providers.get("projection_writer"), "filesystem")
    ).lower()
    projection_root = _path(
        _pick(None, env, "PROJECT_MANAGER_PROJECTION_ROOT", local_providers.get("projection_root"), resolved_runtime / "projections")
    )
    assert projection_root is not None

    if mode == "local" and database_provider != "sqlite":
        raise SettingsError("local deployment requires sqlite")
    if mode == "central" and database_provider != "postgresql":
        raise SettingsError("central deployment requires postgresql")
    if structure_index == "pageindex" and pageindex_dir is None:
        raise SettingsError("pageindex_dir is required when structure_index is pageindex")
    if document_store == "local" and resolved_business_root is None:
        raise SettingsError("business_root is required when document_store is local")

    return AppSettings(
        deployment_mode=mode,
        business_root=resolved_business_root,
        runtime_workspace=resolved_runtime,
        database=DatabaseSettings(
            provider=database_provider,
            sqlite_path=sqlite_path,
            dsn_env_var=dsn_env_var,
        ),
        providers=ProviderSettings(
            document_store=document_store,
            structure_index=structure_index,
            pageindex_dir=pageindex_dir,
            projection_writer=projection_writer,
            projection_root=projection_root,
        ),
    )
```

创建 `platform_core/__init__.py`：

```python
from .settings import (
    AppSettings,
    DatabaseSettings,
    ProviderSettings,
    SettingsError,
    load_app_settings,
)

__all__ = [
    "AppSettings",
    "DatabaseSettings",
    "ProviderSettings",
    "SettingsError",
    "load_app_settings",
]
```

- [ ] **Step 4: 运行 Settings 测试并确认通过**

Run:

```bash
python -m pytest tests/platform_core/test_settings.py -q
```

Expected: `6 passed`。

- [ ] **Step 5: 将 WorkspaceConfig 改为兼容门面并更新旧测试**

在 `common/workspace_config.py` 保留 `WorkspaceConfig` 和四个 `default_*` 函数，但删除 `DEFAULT_BUSINESS_ROOT`、`_load_local_config` 和独立配置优先级；`resolve_workspace_config` 改为：

```python
def resolve_workspace_config(
    business_root: Optional[PathLike] = None,
    runtime_workspace: Optional[PathLike] = None,
    config_file: Optional[PathLike] = None,
    app_settings: Optional["AppSettings"] = None,
) -> WorkspaceConfig:
    from platform_core.settings import load_app_settings

    settings = app_settings or load_app_settings(
        config_file=config_file,
        business_root=business_root,
        runtime_workspace=runtime_workspace,
    )
    if settings.business_root is None:
        raise ValueError("legacy workspace tools require a configured business_root")
    return WorkspaceConfig(
        business_root=settings.business_root,
        runtime_workspace=settings.runtime_workspace,
        project_files_dir=settings.business_root / "项目文件",
        data_cleaning_workspace=settings.runtime_workspace / "数据清洗工作台",
        opportunity_dir=settings.runtime_workspace / "新机会与线索",
        logs_dir=settings.runtime_workspace / "logs",
        state_dir=settings.runtime_workspace / "state",
    )
```

文件顶部类型导入增加：

```python
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from platform_core.settings import AppSettings
```

在 `common/__init__.py` 增加稳定导出：

```python
from platform_core import AppSettings, SettingsError, load_app_settings
```

并把以下名称加入 `__all__`：

```python
"AppSettings",
"SettingsError",
"load_app_settings",
```

在 `tests/test_workspace_config.py` 将 Windows-only 默认测试替换为跨平台测试：

```python
def test_defaults_to_portable_home_workspace(self):
    with tempfile.TemporaryDirectory() as td:
        with patch.object(Path, "home", classmethod(lambda cls: Path(td))):
            with patch.dict(os.environ, {}, clear=True):
                from common.workspace_config import resolve_workspace_config

                config = resolve_workspace_config(config_file="")

    self.assertEqual(config.business_root, Path(td) / "ProjectManagerData")
    self.assertEqual(config.runtime_workspace, Path(td) / "ProjectManagerData" / ".project_manager")
    self.assertEqual(config.project_files_dir, config.business_root / "项目文件")
```

保留显式参数、环境变量、本地 JSON 和 DataCleaningTools 兼容测试，删除这些测试上的 `@unittest.skipUnless(sys.platform == "win32", ...)`，并使用 `tempfile` 生成平台本地路径，不再断言 `D:\` 或 `E:\`。

- [ ] **Step 6: 运行兼容配置测试**

Run:

```bash
python -m pytest tests/platform_core/test_settings.py tests/test_workspace_config.py -q
```

Expected: 全部 PASS，且没有 Windows-only skip。

- [ ] **Step 7: 提交 Settings 边界**

```bash
git add platform_core/__init__.py platform_core/settings.py tests/platform_core/__init__.py tests/platform_core/test_settings.py common/workspace_config.py common/__init__.py tests/test_workspace_config.py
git commit -m "refactor: add portable application settings"
```

---

### Task 2: 定义基础端口和不可变数据类型

**Files:**
- Create: `platform_core/models.py`
- Create: `platform_core/ports/__init__.py`
- Create: `platform_core/ports/repositories.py`
- Create: `platform_core/ports/document_store.py`
- Create: `platform_core/ports/structure_index.py`
- Create: `platform_core/ports/projection_writer.py`
- Create: `tests/platform_core/test_ports.py`
- Modify: `platform_core/__init__.py`

**Interfaces:**
- Consumes: Python IO 对象、不可变请求 DTO 和领域实体泛型。
- Produces: `Repository[T]`、`UnitOfWork`、`DocumentStore`、`StructureIndex`、`ProjectionWriter` 运行时可检查 Protocol，以及所有方法的精确参数/返回类型。

- [ ] **Step 1: 写端口结构测试**

创建 `tests/platform_core/test_ports.py`：

```python
from io import BytesIO

from platform_core.models import (
    CapabilityReport,
    DocumentRef,
    ObjectStat,
    ProjectionRef,
    ProjectionRequest,
    StructureIndexRequest,
    StructureIndexResult,
)
from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex


class FakeDocumentStore:
    name = "fake"

    def stat(self, ref):
        return ObjectStat(ref=ref, size_bytes=3, modified_at=0.0, etag="abc", available=True)

    def open_read(self, ref):
        return BytesIO(b"abc")


class FakeStructureIndex:
    name = "fake"

    def probe(self):
        return CapabilityReport(status="ready", provider="fake", provider_version="1", reason="ready")

    def index(self, request):
        return StructureIndexResult(status="success", provider="fake", external_ref="memory://1", structure=(), error_code="", error="")


class FakeProjectionWriter:
    name = "fake"

    def write(self, request):
        return ProjectionRef(provider="fake", logical_uri="memory://result", sha256="abc", size_bytes=3)


def test_structural_protocols_accept_matching_adapters():
    assert isinstance(FakeDocumentStore(), DocumentStore)
    assert isinstance(FakeStructureIndex(), StructureIndex)
    assert isinstance(FakeProjectionWriter(), ProjectionWriter)


def test_requests_and_refs_are_immutable():
    ref = DocumentRef(storage_provider="local", object_key="a/b.txt", logical_uri="local://a/b.txt")
    request = ProjectionRequest(projection_type="markdown", relative_path="project/a.md", content="# A", media_type="text/markdown")
    index_request = StructureIndexRequest(document_version_id="dv-1", content_hash="abc", source_path="/tmp/a.pdf", media_type="application/pdf")

    assert ref.object_key == "a/b.txt"
    assert request.projection_type == "markdown"
    assert index_request.document_version_id == "dv-1"
```

- [ ] **Step 2: 运行端口测试并确认导入失败**

Run:

```bash
python -m pytest tests/platform_core/test_ports.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'platform_core.models'`。

- [ ] **Step 3: 实现不可变传输类型**

创建 `platform_core/models.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DocumentRef:
    storage_provider: str
    object_key: str
    logical_uri: str


@dataclass(frozen=True)
class ObjectStat:
    ref: DocumentRef
    size_bytes: int
    modified_at: float
    etag: str
    available: bool


@dataclass(frozen=True)
class CapabilityReport:
    status: str
    provider: str
    provider_version: str
    reason: str


@dataclass(frozen=True)
class StructureIndexRequest:
    document_version_id: str
    content_hash: str
    source_path: str
    media_type: str


@dataclass(frozen=True)
class StructureIndexResult:
    status: str
    provider: str
    external_ref: str
    structure: Sequence[Mapping[str, Any]]
    error_code: str
    error: str


@dataclass(frozen=True)
class ProjectionRequest:
    projection_type: str
    relative_path: str
    content: str
    media_type: str


@dataclass(frozen=True)
class ProjectionRef:
    provider: str
    logical_uri: str
    sha256: str
    size_bytes: int
```

- [ ] **Step 4: 实现五个最小端口协议**

创建 `platform_core/ports/repositories.py`：

```python
from typing import Generic, Optional, Protocol, TypeVar, runtime_checkable


T = TypeVar("T")


@runtime_checkable
class Repository(Protocol, Generic[T]):
    def get(self, entity_id: str) -> Optional[T]:
        raise NotImplementedError

    def add(self, entity: T) -> None:
        raise NotImplementedError


@runtime_checkable
class UnitOfWork(Protocol):
    def __enter__(self) -> "UnitOfWork":
        raise NotImplementedError

    def __exit__(self, exc_type, exc, traceback) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError
```

创建 `platform_core/ports/document_store.py`：

```python
from typing import BinaryIO, Protocol, runtime_checkable

from platform_core.models import DocumentRef, ObjectStat


@runtime_checkable
class DocumentStore(Protocol):
    name: str

    def stat(self, ref: DocumentRef) -> ObjectStat:
        raise NotImplementedError

    def open_read(self, ref: DocumentRef) -> BinaryIO:
        raise NotImplementedError
```

创建 `platform_core/ports/structure_index.py`：

```python
from typing import Protocol, runtime_checkable

from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult


@runtime_checkable
class StructureIndex(Protocol):
    name: str

    def probe(self) -> CapabilityReport:
        raise NotImplementedError

    def index(self, request: StructureIndexRequest) -> StructureIndexResult:
        raise NotImplementedError
```

创建 `platform_core/ports/projection_writer.py`：

```python
from typing import Protocol, runtime_checkable

from platform_core.models import ProjectionRef, ProjectionRequest


@runtime_checkable
class ProjectionWriter(Protocol):
    name: str

    def write(self, request: ProjectionRequest) -> ProjectionRef:
        raise NotImplementedError
```

创建 `platform_core/ports/__init__.py`：

```python
from .document_store import DocumentStore
from .projection_writer import ProjectionWriter
from .repositories import Repository, UnitOfWork
from .structure_index import StructureIndex

__all__ = ["DocumentStore", "ProjectionWriter", "Repository", "StructureIndex", "UnitOfWork"]
```

在 `platform_core/__init__.py` 导出上述模型和端口。

- [ ] **Step 5: 运行端口契约测试**

Run:

```bash
python -m pytest tests/platform_core/test_ports.py -q
```

Expected: `2 passed`。

- [ ] **Step 6: 提交基础端口**

```bash
git add platform_core/models.py platform_core/ports platform_core/__init__.py tests/platform_core/test_ports.py
git commit -m "feat: define platform service ports"
```

---

### Task 3: 显式适配器注册表

**Files:**
- Create: `platform_core/registry.py`
- Create: `tests/platform_core/test_registry.py`
- Modify: `platform_core/__init__.py`

**Interfaces:**
- Consumes: `AdapterKind`、小写适配器名称、`Callable[[AppSettings], object]` 工厂。
- Produces: `AdapterRegistry.register(...)`、`AdapterRegistry.build(...)`、`AdapterRegistry.names(...)`；重复和未知适配器使用稳定异常类型。

- [ ] **Step 1: 写注册、重复和延迟构造测试**

创建 `tests/platform_core/test_registry.py`：

```python
import pytest

from platform_core.registry import AdapterKind, AdapterRegistry, AdapterRegistryError
from platform_core.settings import load_app_settings


def test_factory_is_explicit_and_lazy():
    registry = AdapterRegistry()
    calls = []
    registry.register(AdapterKind.DOCUMENT_STORE, "fake", lambda settings: calls.append(settings) or "adapter")
    settings = load_app_settings(config_file="", environ={})

    assert calls == []
    assert registry.build(AdapterKind.DOCUMENT_STORE, "fake", settings) == "adapter"
    assert calls == [settings]


def test_duplicate_registration_is_rejected():
    registry = AdapterRegistry()
    registry.register(AdapterKind.STRUCTURE_INDEX, "fake", lambda settings: object())

    with pytest.raises(AdapterRegistryError, match="already registered"):
        registry.register(AdapterKind.STRUCTURE_INDEX, "fake", lambda settings: object())


def test_unknown_adapter_lists_available_names():
    registry = AdapterRegistry()
    registry.register(AdapterKind.PROJECTION_WRITER, "filesystem", lambda settings: object())
    settings = load_app_settings(config_file="", environ={})

    with pytest.raises(AdapterRegistryError, match="available=filesystem"):
        registry.build(AdapterKind.PROJECTION_WRITER, "missing", settings)
```

- [ ] **Step 2: 运行测试并确认注册表不存在**

Run:

```bash
python -m pytest tests/platform_core/test_registry.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'platform_core.registry'`。

- [ ] **Step 3: 实现封闭的工厂注册表**

创建 `platform_core/registry.py`：

```python
from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Tuple

from platform_core.settings import AppSettings


AdapterFactory = Callable[[AppSettings], object]


class AdapterKind(str, Enum):
    DOCUMENT_STORE = "document_store"
    STRUCTURE_INDEX = "structure_index"
    PROJECTION_WRITER = "projection_writer"


class AdapterRegistryError(LookupError):
    pass


class AdapterRegistry:
    def __init__(self) -> None:
        self._factories: Dict[Tuple[AdapterKind, str], AdapterFactory] = {}

    def register(self, kind: AdapterKind, name: str, factory: AdapterFactory) -> None:
        normalized = name.strip().lower()
        if not normalized:
            raise AdapterRegistryError("adapter name must not be empty")
        key = (kind, normalized)
        if key in self._factories:
            raise AdapterRegistryError(f"{kind.value}:{normalized} already registered")
        self._factories[key] = factory

    def build(self, kind: AdapterKind, name: str, settings: AppSettings) -> object:
        normalized = name.strip().lower()
        factory = self._factories.get((kind, normalized))
        if factory is None:
            available = ",".join(self.names(kind)) or "<none>"
            raise AdapterRegistryError(
                f"unknown {kind.value} adapter {normalized!r}; available={available}"
            )
        return factory(settings)

    def names(self, kind: AdapterKind) -> List[str]:
        return sorted(name for registered_kind, name in self._factories if registered_kind == kind)
```

在 `platform_core/__init__.py` 导出 `AdapterKind`、`AdapterRegistry` 和 `AdapterRegistryError`。

- [ ] **Step 4: 运行注册表测试**

Run:

```bash
python -m pytest tests/platform_core/test_registry.py -q
```

Expected: `3 passed`。

- [ ] **Step 5: 提交注册表**

```bash
git add platform_core/registry.py platform_core/__init__.py tests/platform_core/test_registry.py
git commit -m "feat: add explicit adapter registry"
```

---

### Task 4: 本地 DocumentStore 和原子 ProjectionWriter

**Files:**
- Create: `integrations/document_store/__init__.py`
- Create: `integrations/document_store/local_store.py`
- Create: `integrations/document_store/disabled_store.py`
- Create: `integrations/projections/__init__.py`
- Create: `integrations/projections/filesystem_writer.py`
- Create: `tests/integrations/test_local_document_store.py`
- Create: `tests/integrations/test_filesystem_projection_writer.py`

**Interfaces:**
- Consumes: Task 2 的 `DocumentRef`、`ProjectionRequest`。
- Produces: `LocalDocumentStore`、`DisabledDocumentStore`、`FilesystemProjectionWriter`，均满足运行时 Protocol；所有路径必须保持在配置根目录内。

- [ ] **Step 1: 写 DocumentStore 根目录和穿越测试**

创建 `tests/integrations/test_local_document_store.py`：

```python
from pathlib import Path

import pytest

from platform_core.models import DocumentRef
from platform_core.ports import DocumentStore


def test_local_store_reads_object_inside_root(tmp_path):
    from integrations.document_store.local_store import LocalDocumentStore

    source = tmp_path / "project" / "a.txt"
    source.parent.mkdir()
    source.write_bytes(b"abc")
    store = LocalDocumentStore(tmp_path)
    ref = DocumentRef("local", "project/a.txt", "local://project/a.txt")

    stat = store.stat(ref)
    with store.open_read(ref) as stream:
        content = stream.read()

    assert isinstance(store, DocumentStore)
    assert stat.size_bytes == 3
    assert content == b"abc"


def test_local_store_rejects_path_escape(tmp_path):
    from integrations.document_store.local_store import DocumentStorePathError, LocalDocumentStore

    store = LocalDocumentStore(tmp_path)
    ref = DocumentRef("local", "../secret.txt", "local://../secret.txt")

    with pytest.raises(DocumentStorePathError, match="outside configured root"):
        store.stat(ref)
```

- [ ] **Step 2: 写 ProjectionWriter 原子写入和穿越测试**

创建 `tests/integrations/test_filesystem_projection_writer.py`：

```python
import hashlib

import pytest

from platform_core.models import ProjectionRequest
from platform_core.ports import ProjectionWriter


def test_projection_writer_writes_utf8_and_hash(tmp_path):
    from integrations.projections.filesystem_writer import FilesystemProjectionWriter

    writer = FilesystemProjectionWriter(tmp_path)
    request = ProjectionRequest("markdown", "projects/a.md", "# 项目A\n", "text/markdown")

    result = writer.write(request)

    payload = "# 项目A\n".encode("utf-8")
    assert isinstance(writer, ProjectionWriter)
    assert (tmp_path / "projects" / "a.md").read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.size_bytes == len(payload)


def test_projection_writer_rejects_path_escape(tmp_path):
    from integrations.projections.filesystem_writer import ProjectionPathError, FilesystemProjectionWriter

    writer = FilesystemProjectionWriter(tmp_path)
    request = ProjectionRequest("json", "../outside.json", "{}", "application/json")

    with pytest.raises(ProjectionPathError, match="outside configured root"):
        writer.write(request)
```

- [ ] **Step 3: 运行适配器测试并确认模块不存在**

Run:

```bash
python -m pytest tests/integrations/test_local_document_store.py tests/integrations/test_filesystem_projection_writer.py -q
```

Expected: FAIL during import for the two new integration modules。

- [ ] **Step 4: 实现受根目录约束的本地 DocumentStore**

创建 `integrations/document_store/local_store.py`：

```python
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Union

from platform_core.models import DocumentRef, ObjectStat


class DocumentStorePathError(ValueError):
    pass


class LocalDocumentStore:
    name = "local"

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    def _resolve(self, ref: DocumentRef) -> Path:
        if ref.storage_provider != self.name:
            raise DocumentStorePathError(
                f"provider mismatch: expected {self.name}, got {ref.storage_provider}"
            )
        candidate = (self.root / ref.object_key).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise DocumentStorePathError("object key resolves outside configured root") from exc
        return candidate

    def stat(self, ref: DocumentRef) -> ObjectStat:
        path = self._resolve(ref)
        details = path.stat()
        etag = hashlib.sha256(
            f"{details.st_size}:{details.st_mtime_ns}".encode("ascii")
        ).hexdigest()
        return ObjectStat(
            ref=ref,
            size_bytes=details.st_size,
            modified_at=details.st_mtime,
            etag=etag,
            available=path.is_file(),
        )

    def open_read(self, ref: DocumentRef) -> BinaryIO:
        return self._resolve(ref).open("rb")
```

创建 `integrations/document_store/disabled_store.py`：

```python
from platform_core.models import DocumentRef, ObjectStat


class DocumentStoreUnavailable(RuntimeError):
    pass


class DisabledDocumentStore:
    name = "disabled"

    def stat(self, ref: DocumentRef) -> ObjectStat:
        raise DocumentStoreUnavailable("document store is disabled on this runtime")

    def open_read(self, ref: DocumentRef):
        raise DocumentStoreUnavailable("document store is disabled on this runtime")
```

创建 `integrations/document_store/__init__.py`：

```python
from .disabled_store import DisabledDocumentStore, DocumentStoreUnavailable
from .local_store import DocumentStorePathError, LocalDocumentStore

__all__ = [
    "DisabledDocumentStore",
    "DocumentStorePathError",
    "DocumentStoreUnavailable",
    "LocalDocumentStore",
]
```

- [ ] **Step 5: 实现文件系统 ProjectionWriter**

创建 `integrations/projections/filesystem_writer.py`：

```python
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Union

from platform_core.models import ProjectionRef, ProjectionRequest


class ProjectionPathError(ValueError):
    pass


class FilesystemProjectionWriter:
    name = "filesystem"

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()

    def _resolve(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ProjectionPathError("projection path resolves outside configured root") from exc
        return target

    def write(self, request: ProjectionRequest) -> ProjectionRef:
        target = self._resolve(request.relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = request.content.encode("utf-8")
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temp_name = stream.name
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
        return ProjectionRef(
            provider=self.name,
            logical_uri=f"projection://{request.relative_path.replace(os.sep, '/')}",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
```

创建 `integrations/projections/__init__.py`：

```python
from .filesystem_writer import FilesystemProjectionWriter, ProjectionPathError

__all__ = ["FilesystemProjectionWriter", "ProjectionPathError"]
```

- [ ] **Step 6: 运行适配器契约测试**

Run:

```bash
python -m pytest tests/integrations/test_local_document_store.py tests/integrations/test_filesystem_projection_writer.py -q
```

Expected: `4 passed`。

- [ ] **Step 7: 提交本地存储和投影适配器**

```bash
git add integrations/document_store integrations/projections tests/integrations/test_local_document_store.py tests/integrations/test_filesystem_projection_writer.py
git commit -m "feat: add local document and projection adapters"
```

---

### Task 5: PageIndex StructureIndex 适配器并移除固定 macOS 路径

**Files:**
- Create: `integrations/pageindex/structure_index.py`
- Create: `tests/integrations/test_pageindex_structure_index.py`
- Modify: `integrations/pageindex/pageindex_client.py:30-48`
- Modify: `tests/test_pageindex_client.py:65-86`
- Modify: `integrations/pageindex/__init__.py` if present; otherwise create it

**Interfaces:**
- Consumes: `StructureIndexRequest`、显式 `pageindex_dir` 和现有 `PageIndexClient.index_pdf/index_md`。
- Produces: `PageIndexStructureIndex.probe()` 和 `.index()`；Provider 不可用返回稳定的 blocked 结果，不把本地结果路径当业务 ID。

- [ ] **Step 1: 写显式路径和适配器映射测试**

创建 `tests/integrations/test_pageindex_structure_index.py`：

```python
from platform_core.models import StructureIndexRequest
from platform_core.ports import StructureIndex


class FakePageIndexClient:
    def index_pdf(self, source_path):
        return {
            "status": "success",
            "engine": "pageindex",
            "structure_json_path": "/runtime/results/a_structure.json",
            "structure": [{"title": "第一章", "start_index": 1, "end_index": 3}],
        }

    def index_md(self, source_path):
        return self.index_pdf(source_path)


def test_pageindex_adapter_maps_success_without_exposing_path_as_identity(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    adapter = PageIndexStructureIndex(
        pageindex_dir=tmp_path,
        client_factory=lambda pageindex_dir: FakePageIndexClient(),
    )
    request = StructureIndexRequest("dv-1", "hash-1", "/docs/a.pdf", "application/pdf")

    result = adapter.index(request)

    assert isinstance(adapter, StructureIndex)
    assert result.status == "success"
    assert result.provider == "pageindex"
    assert result.external_ref == "/runtime/results/a_structure.json"
    assert result.structure[0]["title"] == "第一章"


def test_pageindex_adapter_returns_blocked_when_provider_is_unavailable(tmp_path):
    from integrations.pageindex.pageindex_client import PageIndexError
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    def unavailable(pageindex_dir):
        raise PageIndexError("missing runtime")

    adapter = PageIndexStructureIndex(tmp_path, client_factory=unavailable)
    request = StructureIndexRequest("dv-1", "hash-1", "/docs/a.pdf", "application/pdf")

    assert adapter.probe().status == "blocked"
    result = adapter.index(request)
    assert result.status == "blocked"
    assert result.error_code == "INDEX.PROVIDER_UNAVAILABLE"


def test_pageindex_adapter_rejects_unsupported_media_type(tmp_path):
    from integrations.pageindex.structure_index import PageIndexStructureIndex

    adapter = PageIndexStructureIndex(tmp_path, client_factory=lambda pageindex_dir: FakePageIndexClient())
    request = StructureIndexRequest("dv-1", "hash-1", "/docs/a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    result = adapter.index(request)
    assert result.status == "blocked"
    assert result.error_code == "INDEX.UNSUPPORTED_MEDIA_TYPE"
```

在 tests/test_pageindex_client.py 顶部增加 import pytest，并添加：

```python
def test_pageindex_client_requires_explicit_directory():
    with pytest.raises(PageIndexError, match="pageindex_dir must be configured"):
        PageIndexClient()
```

用上述显式目录测试完整替换现有 test_default_init_uses_expected_path 方法，不保留任何默认 PageIndex 路径断言。


同时把测试文件顶部三个固定夹具路径改为显式环境变量，并增加无环境校验的 fast-test 工厂：

```python
PAGEINDEX_TEST_DIR = os.environ.get("PAGEINDEX_TEST_DIR", "")
FEDERAL_RESERVE_PDF = os.environ.get("PAGEINDEX_TEST_PDF", "")
FEDERAL_RESERVE_CACHE = os.environ.get("PAGEINDEX_TEST_CACHE", "")
TS_PDF = os.environ.get("PAGEINDEX_TEST_SCAN_PDF", "")


def _unverified_client():
    return PageIndexClient(
        pageindex_dir=str(Path.cwd()),
        verify_environment=False,
    )
```

把 `TestParsePages`、`TestIndexPdf`、`TestGetPageContent` 和 `TestFindNodesByTitle` 中的无参 `PageIndexClient()` 改成 `_unverified_client()`。把 `test_custom_path` 改为：

```python
def test_custom_path(self):
    configured = str(Path.cwd())
    client = PageIndexClient(
        pageindex_dir=configured,
        verify_environment=False,
    )
    self.assertEqual(client.pageindex_dir, str(Path(configured).resolve()))
```

慢速测试的 `setUp` 必须显式要求真实 PageIndex 目录：

```python
def setUp(self):
    if not PAGEINDEX_TEST_DIR:
        raise unittest.SkipTest("set PAGEINDEX_TEST_DIR for PageIndex slow tests")
    self.client = PageIndexClient(PAGEINDEX_TEST_DIR)
```

这样 fast tests 不依赖某台机器的 PageIndex 安装，slow tests 只有在调用方同时设置 `PAGEINDEX_RUN_SLOW_TESTS=1` 和真实夹具环境变量时才运行。
- [ ] **Step 2: 运行测试并确认失败原因是硬编码默认值和缺少适配器**

Run:

```bash
python -m pytest tests/integrations/test_pageindex_structure_index.py tests/test_pageindex_client.py -q
```

Expected: 新适配器导入失败，且显式目录测试因当前默认 macOS 路径行为失败。

- [ ] **Step 3: 删除 PageIndexClient 固定路径**

在 `integrations/pageindex/pageindex_client.py` 删除 `DEFAULT_PAGEINDEX_DIR`，并把初始化前半段替换为：

```python
def __init__(
    self,
    pageindex_dir: Optional[str] = None,
    timeout_seconds: int = 600,
    verify_environment: bool = True,
):
    if not pageindex_dir:
        raise PageIndexError(
            "pageindex_dir must be configured explicitly through AppSettings"
        )
    self.pageindex_dir = str(Path(pageindex_dir).expanduser().resolve())
    venv_bin = "Scripts" if os.name == "nt" else "bin"
    python_name = "python.exe" if os.name == "nt" else "python"
    self.python_bin = os.path.join(self.pageindex_dir, ".venv", venv_bin, python_name)
    self.cli_script = os.path.join(self.pageindex_dir, "run_pageindex.py")
    self.timeout_seconds = timeout_seconds
    if verify_environment:
        self._verify_environment()
```

- [ ] **Step 4: 实现 PageIndex StructureIndex 映射**

创建 `integrations/pageindex/structure_index.py`：

```python
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult

from .pageindex_client import PageIndexClient, PageIndexError


ClientFactory = Callable[[str], PageIndexClient]


class PageIndexStructureIndex:
    name = "pageindex"

    def __init__(
        self,
        pageindex_dir: Union[str, Path],
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self.pageindex_dir = str(Path(pageindex_dir).expanduser().resolve())
        self.client_factory = client_factory or (lambda directory: PageIndexClient(directory))

    def _client(self) -> PageIndexClient:
        return self.client_factory(self.pageindex_dir)

    def probe(self) -> CapabilityReport:
        try:
            self._client()
        except PageIndexError as exc:
            return CapabilityReport("blocked", self.name, "", str(exc))
        return CapabilityReport("ready", self.name, "configured", "ready")

    def index(self, request: StructureIndexRequest) -> StructureIndexResult:
        if request.media_type not in {"application/pdf", "text/markdown"}:
            return StructureIndexResult(
                "blocked",
                self.name,
                "",
                (),
                "INDEX.UNSUPPORTED_MEDIA_TYPE",
                f"unsupported media type: {request.media_type}",
            )
        try:
            client = self._client()
        except PageIndexError as exc:
            return StructureIndexResult(
                "blocked", self.name, "", (), "INDEX.PROVIDER_UNAVAILABLE", str(exc)
            )
        raw = (
            client.index_pdf(request.source_path)
            if request.media_type == "application/pdf"
            else client.index_md(request.source_path)
        )
        if raw.get("status") != "success":
            return StructureIndexResult(
                raw.get("status", "failed"),
                self.name,
                "",
                (),
                "INDEX.PROVIDER_FAILED",
                raw.get("error", "PageIndex failed without an error message"),
            )
        return StructureIndexResult(
            "success",
            self.name,
            raw.get("structure_json_path", ""),
            tuple(raw.get("structure", [])),
            "",
            "",
        )
```

在 `integrations/pageindex/__init__.py` 导出 `PageIndexClient`、`PageIndexError` 和 `PageIndexStructureIndex`。

- [ ] **Step 5: 运行 PageIndex 单元测试**

Run:

```bash
python -m pytest tests/integrations/test_pageindex_structure_index.py tests/test_pageindex_client.py -q
```

Expected: 全部 PASS；没有测试依赖 `${BUSINESS_ROOT}/PageIndex`。

- [ ] **Step 6: 搜索确认固定 PageIndex 路径已从运行代码移除**

Run:

```bash
git grep -n "${BUSINESS_ROOT}/PageIndex" -- '*.py'
```

Expected: 无输出，退出码 1。

- [ ] **Step 7: 提交 PageIndex 适配器**

```bash
git add integrations/pageindex/pageindex_client.py integrations/pageindex/structure_index.py integrations/pageindex/__init__.py tests/test_pageindex_client.py tests/integrations/test_pageindex_structure_index.py
git commit -m "refactor: adapt pageindex behind structure index port"
```

---

### Task 6: 组合根与主运行时接线

**Files:**
- Create: `platform_core/composition.py`
- Create: `tests/platform_core/test_composition.py`
- Modify: `platform_core/__init__.py`
- Modify: `main.py:18-26,312-375`
- Modify: `tests/test_loop.py` near existing `main.run` trace metadata tests

**Interfaces:**
- Consumes: `AppSettings`、`AdapterRegistry`、Task 4/5 的三个适配器。
- Produces: `build_default_registry() -> AdapterRegistry`、`build_runtime_adapters(settings) -> RuntimeAdapters`；`main.run` 在 trace metadata 记录脱敏部署信息和适配器名称。

- [ ] **Step 1: 写组合根选择和类型测试**

创建 `tests/platform_core/test_composition.py`：

```python
from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex
from platform_core.settings import load_app_settings


def test_local_runtime_builds_declared_adapters(tmp_path):
    from platform_core.composition import build_runtime_adapters

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_DOCUMENT_STORE": "local",
            "PROJECT_MANAGER_STRUCTURE_INDEX": "disabled",
            "PROJECT_MANAGER_PROJECTION_WRITER": "filesystem",
        },
    )

    adapters = build_runtime_adapters(settings)

    assert isinstance(adapters.document_store, DocumentStore)
    assert isinstance(adapters.structure_index, StructureIndex)
    assert isinstance(adapters.projection_writer, ProjectionWriter)
    assert adapters.summary() == {
        "document_store": "local",
        "structure_index": "disabled",
        "projection_writer": "filesystem",
    }


def test_central_control_plane_can_disable_document_store(tmp_path):
    from platform_core.composition import build_runtime_adapters

    settings = load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_DEPLOYMENT_MODE": "central",
            "PROJECT_MANAGER_DATABASE_PROVIDER": "postgresql",
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_DOCUMENT_STORE": "disabled",
        },
    )

    adapters = build_runtime_adapters(settings)
    assert adapters.document_store.name == "disabled"
```

- [ ] **Step 2: 运行组合测试并确认构建入口不存在**

Run:

```bash
python -m pytest tests/platform_core/test_composition.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'platform_core.composition'`。

- [ ] **Step 3: 实现禁用结构索引和显式组合根**

创建 `platform_core/composition.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

from integrations.document_store import DisabledDocumentStore, LocalDocumentStore
from integrations.pageindex import PageIndexStructureIndex
from integrations.projections import FilesystemProjectionWriter
from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult
from platform_core.ports import DocumentStore, ProjectionWriter, StructureIndex
from platform_core.registry import AdapterKind, AdapterRegistry
from platform_core.settings import AppSettings


class DisabledStructureIndex:
    name = "disabled"

    def probe(self) -> CapabilityReport:
        return CapabilityReport("blocked", self.name, "", "structure index is disabled")

    def index(self, request: StructureIndexRequest) -> StructureIndexResult:
        return StructureIndexResult(
            "blocked",
            self.name,
            "",
            (),
            "INDEX.CAPABILITY_DISABLED",
            "structure index is disabled",
        )


@dataclass(frozen=True)
class RuntimeAdapters:
    document_store: DocumentStore
    structure_index: StructureIndex
    projection_writer: ProjectionWriter

    def summary(self):
        return {
            "document_store": self.document_store.name,
            "structure_index": self.structure_index.name,
            "projection_writer": self.projection_writer.name,
        }


def build_default_registry() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register(
        AdapterKind.DOCUMENT_STORE,
        "local",
        lambda settings: LocalDocumentStore(settings.business_root),
    )
    registry.register(
        AdapterKind.DOCUMENT_STORE,
        "disabled",
        lambda settings: DisabledDocumentStore(),
    )
    registry.register(
        AdapterKind.STRUCTURE_INDEX,
        "disabled",
        lambda settings: DisabledStructureIndex(),
    )
    registry.register(
        AdapterKind.STRUCTURE_INDEX,
        "pageindex",
        lambda settings: PageIndexStructureIndex(settings.providers.pageindex_dir),
    )
    registry.register(
        AdapterKind.PROJECTION_WRITER,
        "filesystem",
        lambda settings: FilesystemProjectionWriter(settings.providers.projection_root),
    )
    return registry


def build_runtime_adapters(
    settings: AppSettings,
    registry: AdapterRegistry = None,
) -> RuntimeAdapters:
    selected = registry or build_default_registry()
    return RuntimeAdapters(
        document_store=selected.build(
            AdapterKind.DOCUMENT_STORE,
            settings.providers.document_store,
            settings,
        ),
        structure_index=selected.build(
            AdapterKind.STRUCTURE_INDEX,
            settings.providers.structure_index,
            settings,
        ),
        projection_writer=selected.build(
            AdapterKind.PROJECTION_WRITER,
            settings.providers.projection_writer,
            settings,
        ),
    )
```

在 `platform_core/__init__.py` 导出 `RuntimeAdapters`、`build_default_registry` 和 `build_runtime_adapters`。

- [ ] **Step 4: 运行组合根测试**

Run:

```bash
python -m pytest tests/platform_core/test_composition.py -q
```

Expected: `2 passed`。

- [ ] **Step 5: 将 main.run 接到统一 Settings 和组合根**

在 `main.py` 导入：

```python
from platform_core import load_app_settings
from platform_core.composition import build_runtime_adapters
```

在 `run()` 完成打印参数之后、路由 skill 之前增加：

```python
    app_settings = load_app_settings()
    runtime_adapters = build_runtime_adapters(app_settings)
    workspace_config = resolve_workspace_config(app_settings=app_settings)
```

数据清洗注册使用统一派生路径：

```python
    effective_data_workspace_dir = data_workspace_dir
    if active_skill == "data_cleaning_file_organization" and effective_data_workspace_dir is None:
        effective_data_workspace_dir = str(workspace_config.data_cleaning_workspace)
    reg = _build_registry_for_skill(
        active_skill,
        data_workspace_dir=effective_data_workspace_dir,
    )
```

删除运行循环前重复的 `workspace_config = resolve_workspace_config()`。在 trace metadata 赋值区增加：

```python
    trace.metadata["deployment"] = app_settings.redacted_summary()
    trace.metadata["adapters"] = runtime_adapters.summary()
```

并把 data workspace metadata 改为记录 `effective_data_workspace_dir`。

在 tests/test_loop.py 的 test_main_run_data_cleaning_file_organization_uses_isolated_workspace 方法现有断言末尾追加以下断言：

```python
assert result["metadata"]["deployment"]["deployment_mode"] == "local"
assert result["metadata"]["adapters"] == {
    "document_store": "local",
    "structure_index": "disabled",
    "projection_writer": "filesystem",
}
assert "PROJECT_MANAGER_DATABASE_DSN" not in json.dumps(result)
```

- [ ] **Step 6: 运行组合和主循环定向回归**

Run:

```bash
python -m pytest tests/platform_core/test_composition.py tests/test_loop.py -q
```

Expected: 全部 PASS；trace metadata 包含脱敏部署摘要，现有 tool registry 语义不变。

- [ ] **Step 7: 提交组合根接线**

```bash
git add platform_core/composition.py platform_core/__init__.py main.py tests/platform_core/test_composition.py tests/test_loop.py
git commit -m "refactor: wire runtime adapters through composition root"
```

---

### Task 7: 配置样例、治理契约和阶段 1 全量验收

**Files:**
- Create: `config/project-manager.example.json`
- Create: `tests/test_platform_foundation_docs.py`
- Modify: `.gitignore`
- Modify: `README.md:35-50,175-187`
- Modify: `governance/directory_contract.json:1-58`
- Modify: `docs/prd/2026-07-03-workspace-config-cloud-drive.md:1-60`

**Interfaces:**
- Consumes: Task 1 环境变量名、配置 JSON Schema 形状和 Task 6 适配器名称。
- Produces: 可复制但不含密钥的本地配置样例、与代码一致的 README/治理契约、阶段 1 可复现验收命令。

- [ ] **Step 1: 写文档和配置契约测试**

创建 `tests/test_platform_foundation_docs.py`：

```python
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_example_config_matches_portable_foundation_contract():
    payload = json.loads(
        (PROJECT_DIR / "config" / "project-manager.example.json").read_text(encoding="utf-8")
    )
    assert payload["deployment_mode"] == "local"
    assert payload["database"]["provider"] == "sqlite"
    assert payload["providers"]["structure_index"] == "disabled"
    assert "password" not in json.dumps(payload).lower()
    assert "E:\\" not in json.dumps(payload)


def test_directory_contract_points_to_new_settings_source():
    payload = json.loads(
        (PROJECT_DIR / "governance" / "directory_contract.json").read_text(encoding="utf-8")
    )
    assert payload["version"] == "3.0.0"
    assert payload["config_source"] == "platform_core.settings.load_app_settings"
    assert "E:\\SynologyDrive" not in json.dumps(payload)


def test_local_secret_config_is_ignored():
    ignore_text = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "config/project-manager.local.json" in ignore_text
    assert "config/workspace.local.json" in ignore_text
```

- [ ] **Step 2: 运行文档契约测试并确认配置样例尚不存在**

Run:

```bash
python -m pytest tests/test_platform_foundation_docs.py -q
```

Expected: FAIL with `FileNotFoundError` for `config/project-manager.example.json`。

- [ ] **Step 3: 添加无密钥配置样例和忽略规则**

创建 `config/project-manager.example.json`：

```json
{
  "deployment_mode": "local",
  "business_root": "~/ProjectManagerData",
  "runtime_workspace": "~/ProjectManagerData/.project_manager",
  "database": {
    "provider": "sqlite",
    "sqlite_path": "~/ProjectManagerData/.project_manager/state/project_manager.sqlite3",
    "dsn_env_var": "PROJECT_MANAGER_DATABASE_DSN"
  },
  "providers": {
    "document_store": "local",
    "structure_index": "disabled",
    "pageindex_dir": null,
    "projection_writer": "filesystem",
    "projection_root": "~/ProjectManagerData/.project_manager/projections"
  }
}
```

在 `.gitignore` 的 Secrets 段增加：

```gitignore
config/project-manager.local.json
config/workspace.local.json
```

- [ ] **Step 4: 更新 README 和旧 PRD 的兼容说明**

README 的配置段必须明确：

```markdown
配置优先级为：显式参数 → `PROJECT_MANAGER_*` 环境变量 → `config/project-manager.local.json` → 跨平台默认值。

Windows、macOS 和群晖挂载路径都通过本地配置提供。仓库不再默认 `E:\SynologyDrive`。如需继续使用该目录，请在本机设置：

```powershell
$env:PROJECT_MANAGER_BUSINESS_ROOT = "E:\SynologyDrive"
$env:PROJECT_MANAGER_WORKSPACE_DIR = "E:\SynologyDrive\_project_manager_workspace"
```

群晖中心化配置使用 PostgreSQL 17；本地配置文件只保存 `PROJECT_MANAGER_DATABASE_DSN` 这个环境变量名，实际 DSN 通过运行环境注入。
```

在旧 PRD 标题后增加：

```markdown
> 状态：已被 `docs/superpowers/specs/2026-07-23-multi-agent-data-platform-design.md` 的跨平台部署设计取代。本文仅保留历史兼容背景，`E:\SynologyDrive` 不再是代码默认值。
```

- [ ] **Step 5: 更新治理目录契约**

把 `governance/directory_contract.json` 的头部字段改为：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Project Manager Agent - Directory Contract",
  "description": "Runtime paths are resolved by platform_core.settings and mapped per deployment node.",
  "version": "3.0.0",
  "config_source": "platform_core.settings.load_app_settings",
  "business_root_env": "PROJECT_MANAGER_BUSINESS_ROOT",
  "workspace_dir_env": "PROJECT_MANAGER_WORKSPACE_DIR",
  "compat_base_dir_env": "LOOP_PROJECT_BASE_DIR",
  "base_dir_default": "${HOME}/ProjectManagerData/.project_manager"
}
```

保留原 `required_dirs` 和 `validation_rules`，把规则描述中的 `common.workspace_config` 改为 `platform_core.settings`，修复建议改为显式配置业务根和运行工作区，不再引用某个盘符。

- [ ] **Step 6: 运行阶段 1 定向测试**

Run:

```bash
python -m pytest \
  tests/platform_core \
  tests/integrations/test_local_document_store.py \
  tests/integrations/test_filesystem_projection_writer.py \
  tests/integrations/test_pageindex_structure_index.py \
  tests/test_workspace_config.py \
  tests/test_pageindex_client.py \
  tests/test_platform_foundation_docs.py \
  -q
```

Expected: 全部 PASS，0 failed。

- [ ] **Step 7: 运行现有完整测试集**

Run:

```bash
python -m pytest -q
```

Expected: 0 failed。环境依赖型测试只能按现有 marker/skip 规则跳过，不得通过新增 skip 掩盖回归。

- [ ] **Step 8: 执行硬编码和密钥扫描**

Run:

```bash
git grep -n -E 'DEFAULT_BUSINESS_ROOT|DEFAULT_PAGEINDEX_DIR|${BUSINESS_ROOT}/PageIndex' -- '*.py'
git grep -n -E 'postgresql://[^ ]+:[^ ]+@' -- ':!docs/superpowers/plans/*' ':!tests/*'
git diff --check
```

Expected: 前两个 grep 无输出并返回 1；`git diff --check` 返回 0。测试夹具中的 Windows 示例路径可以保留，但运行代码不得把它们作为默认值。

- [ ] **Step 9: 提交文档与阶段 1 验收材料**

```bash
git add .gitignore README.md config/project-manager.example.json governance/directory_contract.json docs/prd/2026-07-03-workspace-config-cloud-drive.md tests/test_platform_foundation_docs.py
git commit -m "docs: document portable platform configuration"
```

- [ ] **Step 10: 记录阶段 1 最终证据**

Run:

```bash
git status --short
git log --oneline -7
```

Expected: 除执行前已存在且未纳入计划的用户文件外，工作树无本计划产生的未提交变更；最近提交依次覆盖 Settings、端口、注册表、本地适配器、PageIndex、组合根和文档。

---

## 阶段 1 完成门

只有同时满足以下条件，才能为阶段 2“领域 Schema、迁移框架、Unit of Work 和 SQLite 实现”另写实施计划：

- `main.run` 使用 `load_app_settings()` 和组合根，现有工具暴露列表无变化；
- `common.workspace_config` 只是兼容门面，不再拥有独立默认值和配置优先级；
- 运行代码中不存在 `E:\SynologyDrive` 或固定 PageIndex 安装路径默认值；
- 本地 DocumentStore 和 ProjectionWriter 的路径穿越测试通过；
- PageIndex 缺失时返回可识别的 blocked 能力状态；
- AdapterRegistry 拒绝重复和未知适配器，且不支持动态任意模块加载；
- trace metadata 只包含脱敏配置摘要；
- 阶段 1 定向测试和完整回归测试均为 0 failed；
- 配置样例、README、治理契约与代码环境变量名一致；
- 本计划的七个逻辑提交都可单独审查和回滚。
