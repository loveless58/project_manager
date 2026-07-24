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
from platform_core.ports import StructureIndex
from platform_core.registry import AdapterKind, AdapterRegistry
from platform_core.settings import AppSettings, load_app_settings


PathLike = Union[str, Path]


@dataclass(frozen=True)
class DocumentParseRuntime:
    """Selected dependencies for one document-parse KB operation."""

    settings: Optional[AppSettings]
    structure_index: StructureIndex
    cache_path: Path


def resolve_document_parse_runtime(
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    cache_path: Optional[PathLike] = None,
    registry: Optional[AdapterRegistry] = None,
) -> DocumentParseRuntime:
    """Load settings and select only the provider needed by document_parse.

    Environment values retain the priority implemented by ``load_app_settings``
    (explicit arguments, environment, JSON, defaults).  Supplying an adapter or
    cache path is useful for tests and embedding without changing global state.
    """

    settings = app_settings
    if settings is None and (structure_index is None or cache_path is None):
        settings = load_app_settings(
            config_file=config_file,
            environ=environ,
        )
    selected_index = structure_index
    if selected_index is None:
        assert settings is not None
        selected_registry = registry or build_default_registry()
        selected_index = selected_registry.build(
            AdapterKind.STRUCTURE_INDEX,
            settings.providers.structure_index,
            settings,
        )

    if cache_path is not None:
        selected_cache = Path(cache_path).expanduser().resolve()
    else:
        assert settings is not None
        selected_cache = (
            settings.runtime_workspace / "cache" / "document_parse" / "kb_cache.json"
        )
    return DocumentParseRuntime(
        settings=settings,
        structure_index=selected_index,
        cache_path=selected_cache,
    )


__all__ = ["DocumentParseRuntime", "resolve_document_parse_runtime"]
