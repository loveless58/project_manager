from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

from .path_locality import (
    NodeLocalPathError,
    ensure_node_local_path,
    is_path_within,
)
from .storage_bindings import StorageBinding


PathLike = Union[str, os.PathLike]
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "project-manager.local.json"
LEGACY_CONFIG_FILE = PROJECT_ROOT / "config" / "workspace.local.json"

_CONFIG_TOP_LEVEL_KEYS = frozenset(
    {
        "deployment_mode",
        "business_root",
        "runtime_workspace",
        "storage_bindings",
        "database",
        "providers",
    }
)
_CONFIG_DATABASE_KEYS = frozenset({"provider", "sqlite_path", "dsn_env_var"})
_CONFIG_PROVIDER_KEYS = frozenset(
    {
        "document_store",
        "structure_index",
        "pageindex_dir",
        "projection_writer",
        "projection_root",
    }
)

_CONFIG_TOP_LEVEL_LEAF_KEYS = frozenset(
    {"deployment_mode", "business_root", "runtime_workspace"}
)
_CONFIG_DATABASE_LEAF_KEYS = _CONFIG_DATABASE_KEYS
_CONFIG_PROVIDER_LEAF_KEYS = _CONFIG_PROVIDER_KEYS


class SettingsError(ValueError):
    pass


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, os.PathLike):
        try:
            raw = os.fspath(value)
        except TypeError:
            return True
        return isinstance(raw, str) and bool(raw.strip())
    return True


def _string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise SettingsError(f"{field_name} must be a string")
    return value


def _path(value: Optional[PathLike], *, field_name: str) -> Optional[Path]:
    if value is None:
        return None
    try:
        raw = os.fspath(value)
    except TypeError:
        raise SettingsError(f"{field_name} must be a path string") from None
    if not isinstance(raw, str):
        raise SettingsError(f"{field_name} must be a path string")
    if not raw.strip():
        return None
    try:
        return Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        raise SettingsError(f"{field_name} must be a valid path string") from None


def _unique_json_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SettingsError(f"configuration contains duplicate field: {key}")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise SettingsError("configuration file contains invalid JSON")


def _read_config(config_file: Optional[PathLike]) -> Dict[str, Any]:
    if config_file == "":
        return {}

    if config_file is not None:
        path = Path(config_file).expanduser()
    else:
        try:
            if DEFAULT_CONFIG_FILE.is_file():
                path = DEFAULT_CONFIG_FILE
            elif LEGACY_CONFIG_FILE.is_file():
                path = LEGACY_CONFIG_FILE
            else:
                return {}
        except OSError:
            raise SettingsError(
                "configuration file is not a readable regular file"
            ) from None

    try:
        if not path.is_file():
            raise SettingsError(
                "configuration file is not a readable regular file"
            )
        serialized = path.read_text(encoding="utf-8-sig")
    except SettingsError:
        raise
    except (OSError, UnicodeError):
        raise SettingsError(
            "configuration file is not a readable regular file"
        ) from None

    try:
        payload = json.loads(
            serialized,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except SettingsError:
        raise
    except (TypeError, ValueError, RecursionError):
        raise SettingsError("configuration file contains invalid JSON") from None
    if not isinstance(payload, dict):
        raise SettingsError("configuration root must be an object")
    _validate_config_schema(payload)
    return payload


def _validate_config_leaves(
    payload: Mapping[str, Any],
    *,
    section_name: str,
    leaf_keys: frozenset[str],
) -> None:
    for key in payload:
        if key not in leaf_keys:
            continue
        value = payload[key]
        if value is not None and not isinstance(value, str):
            qualified = f"{section_name}.{key}" if section_name else key
            raise SettingsError(
                f"configuration field {qualified} must be a string or null"
            )


def _validate_config_schema(payload: Mapping[str, Any]) -> None:
    if not set(payload).issubset(_CONFIG_TOP_LEVEL_KEYS):
        raise SettingsError("configuration contains unknown top-level fields")
    _validate_config_leaves(
        payload,
        section_name="",
        leaf_keys=_CONFIG_TOP_LEVEL_LEAF_KEYS,
    )
    for section_name, allowed_keys, leaf_keys in (
        ("database", _CONFIG_DATABASE_KEYS, _CONFIG_DATABASE_LEAF_KEYS),
        ("providers", _CONFIG_PROVIDER_KEYS, _CONFIG_PROVIDER_LEAF_KEYS),
    ):
        if section_name not in payload:
            continue
        section = payload[section_name]
        if not isinstance(section, dict):
            raise SettingsError(
                f"configuration {section_name} section must be an object"
            )
        if not set(section).issubset(allowed_keys):
            raise SettingsError(
                f"configuration {section_name} section contains unknown fields"
            )
        _validate_config_leaves(
            section,
            section_name=section_name,
            leaf_keys=leaf_keys,
        )



_STORAGE_BINDING_KEYS = frozenset(
    {
        "binding_id",
        "provider",
        "node_id",
        "logical_root",
        "physical_root",
        "roles",
        "readable",
        "writable",
        "enabled",
    }
)
_REQUIRED_STORAGE_BINDING_KEYS = _STORAGE_BINDING_KEYS - {"enabled"}


def _load_storage_bindings(raw_bindings: Any) -> tuple[StorageBinding, ...]:
    if not isinstance(raw_bindings, list):
        raise SettingsError("storage_bindings must be a list")

    bindings: list[StorageBinding] = []
    binding_id_locations: dict[str, int] = {}
    for index, raw_binding in enumerate(raw_bindings):
        field_prefix = f"storage_bindings[{index}]"
        if not isinstance(raw_binding, dict):
            raise SettingsError(f"{field_prefix} must be an object")
        if not set(raw_binding).issubset(_STORAGE_BINDING_KEYS):
            raise SettingsError(f"{field_prefix} contains unknown fields")
        if not _REQUIRED_STORAGE_BINDING_KEYS.issubset(raw_binding):
            raise SettingsError(f"{field_prefix} is missing required fields")

        string_values = {}
        for field_name in (
            "binding_id",
            "provider",
            "node_id",
            "logical_root",
            "physical_root",
        ):
            value = raw_binding[field_name]
            if not isinstance(value, str) or not value.strip():
                raise SettingsError(f"{field_prefix}.{field_name} must be a non-empty string")
            string_values[field_name] = value

        raw_roles = raw_binding["roles"]
        binding_id = string_values["binding_id"]
        first_index = binding_id_locations.get(binding_id)
        if first_index is not None:
            raise SettingsError(
                f"{field_prefix}.binding_id duplicates storage_bindings[{first_index}].binding_id"
            )
        binding_id_locations[binding_id] = index
        if (
            not isinstance(raw_roles, list)
            or not all(isinstance(role, str) and role.strip() for role in raw_roles)
        ):
            raise SettingsError(f"{field_prefix}.roles must be a list of non-empty strings")
        for field_name in ("readable", "writable", "enabled"):
            if field_name in raw_binding and not isinstance(raw_binding[field_name], bool):
                raise SettingsError(f"{field_prefix}.{field_name} must be a boolean")

        physical_root = _path(
            string_values["physical_root"],
            field_name=f"{field_prefix}.physical_root",
        )
        assert physical_root is not None
        try:
            bindings.append(
                StorageBinding(
                    binding_id=string_values["binding_id"],
                    provider=string_values["provider"],
                    node_id=string_values["node_id"],
                    logical_root=string_values["logical_root"],
                    physical_root=physical_root,
                    roles=tuple(raw_roles),
                    readable=raw_binding["readable"],
                    writable=raw_binding["writable"],
                    enabled=raw_binding.get("enabled", True),
                )
            )
        except ValueError as exc:
            raise SettingsError(str(exc)) from None
    return tuple(bindings)

def _ensure_outside_storage_bindings(
    *,
    field_name: str,
    path: Path,
    storage_bindings: tuple[StorageBinding, ...],
) -> Path:
    for binding in storage_bindings:
        if is_path_within(path, binding.physical_root):
            raise SettingsError(f"{field_name} must not be inside storage binding")
        if is_path_within(binding.physical_root, path):
            raise SettingsError(f"storage binding must not be inside {field_name}")
    return path


def validate_runtime_storage_isolation(settings: "AppSettings") -> None:
    """Reject manually assembled settings that overlap runtime and business data."""
    protected_paths = [
        ("runtime_workspace", settings.runtime_workspace),
        ("projection_root", settings.providers.projection_root),
    ]
    if settings.database.provider == "sqlite" and settings.database.sqlite_path is not None:
        protected_paths.append(("sqlite_path", settings.database.sqlite_path))
    for field_name, path in protected_paths:
        _ensure_outside_storage_bindings(
            field_name=field_name,
            path=path,
            storage_bindings=settings.storage_bindings,
        )

def _pick(
    explicit: Any,
    env: Mapping[str, str],
    env_name: str,
    local: Any,
    default: Any,
) -> Any:
    if _has_value(explicit):
        return explicit
    env_value = env.get(env_name)
    if _has_value(env_value):
        return env_value
    if _has_value(local):
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
    storage_bindings: tuple[StorageBinding, ...]
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
    sqlite_path: Optional[PathLike] = None,
) -> AppSettings:
    env = dict(os.environ if environ is None else environ)
    local = _read_config(config_file)
    local_database = local.get("database", {}) if isinstance(local.get("database", {}), dict) else {}
    local_providers = local.get("providers", {}) if isinstance(local.get("providers", {}), dict) else {}

    mode = _string(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DEPLOYMENT_MODE",
            local.get("deployment_mode"),
            "local",
        ),
        field_name="deployment_mode",
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
        ),
        field_name="business_root",
    )
    storage_bindings = (
        _load_storage_bindings(local["storage_bindings"])
        if "storage_bindings" in local
        else (
            (
                StorageBinding(
                    "legacy-business-root",
                    "local",
                    "legacy",
                    "business://legacy-business-root/",
                    resolved_business_root,
                    ("source",),
                    True,
                    False,
                ),
            )
            if resolved_business_root is not None
            else ()
        )
    )

    default_runtime = Path.home() / ".project_manager"
    raw_runtime_workspace = _pick(
        runtime_workspace,
        env,
        "PROJECT_MANAGER_WORKSPACE_DIR",
        local.get("runtime_workspace"),
        default_runtime,
    )
    resolved_runtime = _path(
        raw_runtime_workspace,
        field_name="runtime_workspace",
    )
    assert raw_runtime_workspace is not None
    assert resolved_runtime is not None

    default_database = "sqlite" if mode == "local" else "postgresql"
    database_provider = _string(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DATABASE_PROVIDER",
            local_database.get("provider"),
            default_database,
        ),
        field_name="database_provider",
    ).lower()
    raw_sqlite_path = _pick(
        sqlite_path,
        env,
        "PROJECT_MANAGER_SQLITE_PATH",
        local_database.get("sqlite_path"),
        resolved_runtime / "state" / "project_manager.sqlite3"
        if database_provider == "sqlite"
        else None,
    )
    sqlite_path = _path(
        raw_sqlite_path,
        field_name="sqlite_path",
    )
    dsn_env_var = _string(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DATABASE_DSN_ENV",
            local_database.get("dsn_env_var"),
            "PROJECT_MANAGER_DATABASE_DSN",
        ),
        field_name="database_dsn_env_var",
    )

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dsn_env_var):
        raise SettingsError("database_dsn_env_var must be a valid environment variable name")

    document_store = _string(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_DOCUMENT_STORE",
            local_providers.get("document_store"),
            "local" if resolved_business_root else "disabled",
        ),
        field_name="document_store",
    ).lower()
    structure_index = _string(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_STRUCTURE_INDEX",
            local_providers.get("structure_index"),
            "disabled",
        ),
        field_name="structure_index",
    ).lower()
    pageindex_dir = _path(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_PAGEINDEX_DIR",
            local_providers.get("pageindex_dir"),
            None,
        ),
        field_name="pageindex_dir",
    )
    projection_writer = _string(
        _pick(
            None,
            env,
            "PROJECT_MANAGER_PROJECTION_WRITER",
            local_providers.get("projection_writer"),
            "filesystem",
        ),
        field_name="projection_writer",
    ).lower()
    raw_projection_root = _pick(
        None,
        env,
        "PROJECT_MANAGER_PROJECTION_ROOT",
        local_providers.get("projection_root"),
        resolved_runtime / "projections",
    )
    projection_root = _path(
        raw_projection_root,
        field_name="projection_root",
    )
    assert raw_projection_root is not None
    assert projection_root is not None

    if mode == "local" and database_provider != "sqlite":
        raise SettingsError("local deployment requires sqlite")
    if mode == "central" and database_provider != "postgresql":
        raise SettingsError("central deployment requires postgresql")

    try:
        resolved_runtime = ensure_node_local_path(
            field_name="runtime_workspace",
            raw_value=raw_runtime_workspace,
            resolved_path=resolved_runtime,
            business_root=resolved_business_root,
        )
        projection_root = ensure_node_local_path(
            field_name="projection_root",
            raw_value=raw_projection_root,
            resolved_path=projection_root,
            business_root=resolved_business_root,
        )
    except NodeLocalPathError as exc:
        raise SettingsError(str(exc)) from exc
    resolved_runtime = _ensure_outside_storage_bindings(
        field_name="runtime_workspace",
        path=resolved_runtime,
        storage_bindings=storage_bindings,
    )
    projection_root = _ensure_outside_storage_bindings(
        field_name="projection_root",
        path=projection_root,
        storage_bindings=storage_bindings,
    )

    if database_provider == "sqlite":
        assert raw_sqlite_path is not None
        assert sqlite_path is not None
        try:
            sqlite_path = ensure_node_local_path(
                field_name="sqlite_path",
                raw_value=raw_sqlite_path,
                resolved_path=sqlite_path,
                business_root=resolved_business_root,
            )
        except NodeLocalPathError as exc:
            raise SettingsError(str(exc)) from exc
        sqlite_path = _ensure_outside_storage_bindings(
            field_name="sqlite_path",
            path=sqlite_path,
            storage_bindings=storage_bindings,
        )
    if structure_index == "pageindex" and pageindex_dir is None:
        raise SettingsError("pageindex_dir is required when structure_index is pageindex")
    if document_store == "local" and resolved_business_root is None:
        raise SettingsError("business_root is required when document_store is local")

    return AppSettings(
        deployment_mode=mode,
        business_root=resolved_business_root,
        storage_bindings=storage_bindings,
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
