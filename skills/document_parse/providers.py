"""Runtime composition for the document-parse knowledge-base provider.

This module keeps provider selection at the application boundary: settings are
loaded once, the configured ``StructureIndex`` is selected through the shared
registry, and mutable cache state is rooted in the node-local runtime workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Union

from app_bootstrap.composition import build_default_registry
from platform_core.path_locality import NodeLocalPathError, ensure_node_local_path
from platform_core.ports import StructureIndex
from platform_core.registry import AdapterKind, AdapterRegistry
from platform_core.settings import AppSettings, load_app_settings


PathLike = Union[str, Path]


class DocumentParseCachePathError(ValueError):
    """A document-parse cache path is outside its managed node-local root."""


@dataclass(frozen=True)
class DocumentParseRuntime:
    """Selected dependencies for one document-parse KB operation."""

    settings: Optional[AppSettings]
    structure_index: StructureIndex
    managed_cache_root: Path
    cache_path: Path


def _resolve_cache_location(
    *,
    settings: Optional[AppSettings],
    cache_path: Optional[PathLike],
    managed_cache_root: Optional[PathLike],
) -> tuple[Path, Path]:
    if settings is not None:
        fixed_root = (
            settings.runtime_workspace / "cache" / "document_parse"
        ).resolve()
        if managed_cache_root is not None:
            requested_root = Path(managed_cache_root).expanduser().resolve()
            if requested_root != fixed_root:
                raise DocumentParseCachePathError(
                    "managed_cache_root must equal runtime/cache/document_parse"
                )
        raw_root: PathLike = fixed_root
        business_root = settings.business_root
    else:
        if managed_cache_root is None:
            raise DocumentParseCachePathError(
                "managed_cache_root is required without AppSettings"
            )
        raw_root = managed_cache_root
        business_root = None

    try:
        resolved_root = ensure_node_local_path(
            field_name="managed_cache_root",
            raw_value=raw_root,
            resolved_path=raw_root,
            business_root=business_root,
        )
    except NodeLocalPathError as exc:
        raise DocumentParseCachePathError(str(exc)) from exc

    raw_cache: PathLike = (
        cache_path if cache_path is not None else resolved_root / "kb_cache.json"
    )
    try:
        selected_cache = ensure_node_local_path(
            field_name="document_parse_cache_path",
            raw_value=raw_cache,
            resolved_path=raw_cache,
            business_root=business_root,
        )
    except NodeLocalPathError as exc:
        raise DocumentParseCachePathError(str(exc)) from exc

    try:
        selected_cache.relative_to(resolved_root)
    except ValueError as exc:
        raise DocumentParseCachePathError(
            "document_parse_cache_path must be under managed_cache_root"
        ) from exc
    if selected_cache == resolved_root:
        raise DocumentParseCachePathError(
            "document_parse_cache_path must name a file under managed_cache_root"
        )
    return resolved_root, selected_cache


def resolve_document_parse_cache_path(
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    cache_path: Optional[PathLike] = None,
    managed_cache_root: Optional[PathLike] = None,
) -> tuple[Path, Path]:
    """Resolve a cache marker and its authoritative managed root."""

    settings = app_settings
    explicit_managed = managed_cache_root is not None
    if settings is None and not explicit_managed:
        if cache_path is not None and config_file is None and environ is None:
            raise DocumentParseCachePathError(
                "managed_cache_root is required for an explicit cache path"
            )
        settings = load_app_settings(config_file=config_file, environ=environ)
    return _resolve_cache_location(
        settings=settings,
        cache_path=cache_path,
        managed_cache_root=managed_cache_root,
    )


def resolve_document_parse_runtime(
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    cache_path: Optional[PathLike] = None,
    managed_cache_root: Optional[PathLike] = None,
    registry: Optional[AdapterRegistry] = None,
) -> DocumentParseRuntime:
    """Load settings and select only the provider needed by document_parse.

    Fully injected test/embedding runtimes must provide an explicit managed
    cache root. Settings-backed runtimes always use the fixed
    ``runtime/cache/document_parse`` root.
    """

    settings = app_settings
    fully_injected = structure_index is not None and cache_path is not None
    if settings is None and fully_injected and managed_cache_root is None:
        raise DocumentParseCachePathError(
            "managed_cache_root is required without AppSettings"
        )
    if settings is None and not fully_injected:
        settings = load_app_settings(config_file=config_file, environ=environ)

    selected_index = structure_index
    if selected_index is None:
        assert settings is not None
        selected_registry = registry or build_default_registry()
        selected_index = selected_registry.build(
            AdapterKind.STRUCTURE_INDEX,
            settings.providers.structure_index,
            settings,
        )

    resolved_root, selected_cache = _resolve_cache_location(
        settings=settings,
        cache_path=cache_path,
        managed_cache_root=managed_cache_root,
    )
    return DocumentParseRuntime(
        settings=settings,
        structure_index=selected_index,
        managed_cache_root=resolved_root,
        cache_path=selected_cache,
    )


__all__ = [
    "DocumentParseCachePathError",
    "DocumentParseRuntime",
    "resolve_document_parse_cache_path",
    "resolve_document_parse_runtime",
]
