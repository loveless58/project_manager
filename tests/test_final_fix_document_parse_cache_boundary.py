"""Security regression tests for the managed document-parse cache boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from platform_core.settings import load_app_settings
from skills.document_parse import kb
from skills.document_parse.providers import resolve_document_parse_runtime


class StaticStructureIndex:
    name = "static"
    provider_version = "static-v1"


def _settings(tmp_path: Path):
    return load_app_settings(
        config_file="",
        environ={
            "PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business"),
            "PROJECT_MANAGER_WORKSPACE_DIR": str(tmp_path / "runtime"),
            "PROJECT_MANAGER_STRUCTURE_INDEX": "disabled",
        },
    )


def _write_legal_cache(cache_path: Path) -> Path:
    key = "a" * 64
    entries = cache_path.with_name(f"{cache_path.name}.entries")
    entries.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": "document_parse_kb_cache.v3",
                "layout": "per-key-v1",
            }
        ),
        encoding="utf-8",
    )
    entry = entries / f"{key}.json"
    entry.write_text(
        json.dumps(
            {
                "schema_version": "document_parse_kb_cache.v3",
                "cache_key": key,
                "result": {
                    "status": "success",
                    "structure": [
                        {
                            "title": "root",
                            "node_id": "0000",
                            "nodes": [{"title": "child", "node_id": "0001", "nodes": []}],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return entry


def test_settings_default_cache_is_inside_fixed_document_parse_root(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    runtime = resolve_document_parse_runtime(
        app_settings=settings,
        structure_index=StaticStructureIndex(),
    )

    expected_root = settings.runtime_workspace / "cache" / "document_parse"
    assert runtime.managed_cache_root == expected_root
    assert runtime.cache_path == expected_root / "kb_cache.json"


@pytest.mark.parametrize("location", ["business", "outside-runtime"])
def test_settings_reject_cache_outside_fixed_document_parse_root(
    tmp_path: Path,
    location: str,
) -> None:
    settings = _settings(tmp_path)
    cache_path = (
        settings.business_root / "cache.json"
        if location == "business"
        else tmp_path / "arbitrary" / "cache.json"
    )

    with pytest.raises(ValueError):
        resolve_document_parse_runtime(
            app_settings=settings,
            structure_index=StaticStructureIndex(),
            cache_path=cache_path,
        )


def test_injected_runtime_requires_explicit_managed_cache_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="managed_cache_root"):
        resolve_document_parse_runtime(
            structure_index=StaticStructureIndex(),
            cache_path=tmp_path / "cache" / "kb.json",
        )


def test_injected_runtime_accepts_cache_below_explicit_node_local_root(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "runtime" / "cache" / "document_parse"
    cache_path = managed_root / "test-cache.json"

    runtime = resolve_document_parse_runtime(
        structure_index=StaticStructureIndex(),
        cache_path=cache_path,
        managed_cache_root=managed_root,
    )

    assert runtime.settings is None
    assert runtime.managed_cache_root == managed_root.resolve()
    assert runtime.cache_path == cache_path.resolve()


def test_injected_runtime_rejects_obvious_network_managed_root() -> None:
    with pytest.raises(ValueError, match="node-local"):
        resolve_document_parse_runtime(
            structure_index=StaticStructureIndex(),
            cache_path=r"\\server\share\document_parse\kb.json",  # repo-hygiene: allow=synthetic-path
            managed_cache_root=r"\\server\share\document_parse",  # repo-hygiene: allow=synthetic-path
        )


def test_clear_cache_rejects_arbitrary_file_without_unlinking_it(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "runtime" / "cache" / "document_parse"
    managed_root.mkdir(parents=True)
    arbitrary = managed_root / "notes.txt"
    arbitrary.write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError, match="cache marker"):
        kb.clear_cache(cache_path=arbitrary, managed_cache_root=managed_root)

    assert arbitrary.read_text(encoding="utf-8") == "do not delete"


def test_clear_cache_validates_every_entry_before_deleting_anything(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "runtime" / "cache" / "document_parse"
    cache_path = managed_root / "kb.json"
    legal_entry = _write_legal_cache(cache_path)
    rogue = legal_entry.parent / "notes.txt"
    rogue.write_text("unmanaged", encoding="utf-8")

    with pytest.raises(ValueError, match="cache entry"):
        kb.clear_cache(cache_path=cache_path, managed_cache_root=managed_root)

    assert cache_path.is_file()
    assert legal_entry.is_file()
    assert rogue.read_text(encoding="utf-8") == "unmanaged"


def test_clear_cache_removes_only_verified_marker_and_entries(
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "runtime" / "cache" / "document_parse"
    cache_path = managed_root / "kb.json"
    legal_entry = _write_legal_cache(cache_path)

    kb.clear_cache(cache_path=cache_path, managed_cache_root=managed_root)

    assert not cache_path.exists()
    assert not legal_entry.exists()
    assert not legal_entry.parent.exists()
    assert managed_root.is_dir()


def test_clear_cache_rejects_business_file_and_preserves_it(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    business_file = settings.business_root / "customer.json"
    business_file.parent.mkdir(parents=True)
    business_file.write_text("customer data", encoding="utf-8")

    with pytest.raises(ValueError):
        kb.clear_cache(app_settings=settings, cache_path=business_file)

    assert business_file.read_text(encoding="utf-8") == "customer data"
