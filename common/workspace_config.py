"""Compatibility facade for project_manager runtime workspace paths."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union


if TYPE_CHECKING:
    from platform_core.settings import AppSettings


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


def resolve_workspace_config(
    business_root: Optional[PathLike] = None,
    runtime_workspace: Optional[PathLike] = None,
    config_file: Optional[PathLike] = None,
    app_settings: Optional["AppSettings"] = None,
) -> WorkspaceConfig:
    """Adapt application settings for legacy workspace callers."""
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


def default_project_files_dir() -> str:
    return str(resolve_workspace_config().project_files_dir)


def default_business_root() -> str:
    return str(resolve_workspace_config().business_root)


def default_data_cleaning_workspace() -> str:
    return str(resolve_workspace_config().data_cleaning_workspace)


def default_opportunity_dir() -> str:
    return str(resolve_workspace_config().opportunity_dir)
