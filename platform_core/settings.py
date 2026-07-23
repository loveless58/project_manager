from __future__ import annotations

import json
import os
import re
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
        _pick(
            business_root,
            env,
            "PROJECT_MANAGER_BUSINESS_ROOT",
            local.get("business_root"),
            default_root if mode == "local" else None,
        )
    )
    default_runtime = (
        resolved_business_root / ".project_manager"
        if resolved_business_root is not None
        else Path.home() / ".project_manager"
    )
    resolved_runtime = _path(
        _pick(
            runtime_workspace,
            env,
            "PROJECT_MANAGER_WORKSPACE_DIR",
            local.get("runtime_workspace"),
            default_runtime,
        )
    )
    assert resolved_runtime is not None

    default_database = "sqlite" if mode == "local" else "postgresql"
    database_provider = str(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DATABASE_PROVIDER",
            local_database.get("provider"),
            default_database,
        )
    ).lower()
    sqlite_path = _path(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_SQLITE_PATH",
            local_database.get("sqlite_path"),
            resolved_runtime / "state" / "project_manager.sqlite3"
            if database_provider == "sqlite"
            else None,
        )
    )
    dsn_env_var = str(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DATABASE_DSN_ENV",
            local_database.get("dsn_env_var"),
            "PROJECT_MANAGER_DATABASE_DSN",
        )
    )

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dsn_env_var):
        raise SettingsError("database_dsn_env_var must be a valid environment variable name")

    document_store = str(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DOCUMENT_STORE",
            local_providers.get("document_store"),
            "local" if resolved_business_root else "disabled",
        )
    ).lower()
    structure_index = str(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_STRUCTURE_INDEX",
            local_providers.get("structure_index"),
            "disabled",
        )
    ).lower()
    pageindex_dir = _path(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_PAGEINDEX_DIR",
            local_providers.get("pageindex_dir"),
            None,
        )
    )
    projection_writer = str(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_PROJECTION_WRITER",
            local_providers.get("projection_writer"),
            "filesystem",
        )
    ).lower()
    projection_root = _path(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_PROJECTION_ROOT",
            local_providers.get("projection_root"),
            resolved_runtime / "projections",
        )
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
