"""Unified workspace configuration for project_manager runtime paths."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BUSINESS_ROOT = Path(r"E:\SynologyDrive")
DEFAULT_WORKSPACE_NAME = "_project_manager_workspace"
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "config" / "workspace.local.json"


PathLike = Union[str, os.PathLike[str]]


@dataclass(frozen=True)
class WorkspaceConfig:
    business_root: Path
    runtime_workspace: Path
    project_files_dir: Path
    data_cleaning_workspace: Path
    opportunity_dir: Path
    logs_dir: Path
    state_dir: Path


def _clean_path(value: Optional[Union[str, os.PathLike[str]]]) -> Optional[Path]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().absolute()
    except RuntimeError:
        if text == "~":
            return PROJECT_ROOT
        if text.startswith("~/") or text.startswith("~\\"):
            return (PROJECT_ROOT / text[2:]).absolute()
        return Path(text).absolute()


def _load_local_config(config_file: Optional[Union[str, os.PathLike[str]]]) -> Dict[str, Any]:
    if config_file == "":
        return {}
    path = Path(config_file).expanduser() if config_file else DEFAULT_CONFIG_FILE
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def resolve_workspace_config(
    business_root: Optional[PathLike] = None,
    runtime_workspace: Optional[PathLike] = None,
    config_file: Optional[PathLike] = None,
) -> WorkspaceConfig:
    """Resolve all runtime paths from explicit args, env, local config, then defaults."""
    local = _load_local_config(config_file)

    resolved_business_root = (
        _clean_path(business_root)
        or _clean_path(os.environ.get("PROJECT_MANAGER_BUSINESS_ROOT"))
        or _clean_path(local.get("business_root"))
        or DEFAULT_BUSINESS_ROOT
    )
    resolved_workspace = (
        _clean_path(runtime_workspace)
        or _clean_path(os.environ.get("PROJECT_MANAGER_WORKSPACE_DIR"))
        or _clean_path(local.get("runtime_workspace"))
        or (resolved_business_root / DEFAULT_WORKSPACE_NAME)
    )

    return WorkspaceConfig(
        business_root=resolved_business_root,
        runtime_workspace=resolved_workspace,
        project_files_dir=resolved_business_root / "项目文件",
        data_cleaning_workspace=resolved_workspace / "数据清洗工作台",
        opportunity_dir=resolved_workspace / "新机会与线索",
        logs_dir=resolved_workspace / "logs",
        state_dir=resolved_workspace / "state",
    )


def default_project_files_dir() -> str:
    return str(resolve_workspace_config().project_files_dir)


def default_business_root() -> str:
    return str(resolve_workspace_config().business_root)


def default_data_cleaning_workspace() -> str:
    return str(resolve_workspace_config().data_cleaning_workspace)


def default_opportunity_dir() -> str:
    return str(resolve_workspace_config().opportunity_dir)
