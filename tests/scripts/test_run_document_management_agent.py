# synthetic fixture data only
from __future__ import annotations

SYN_DOC_REF = "1234567890" * 2

import json
from pathlib import Path

from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.migration_runner import apply_pending_migrations, initialize_database
from scripts.run_file_organizer import main
from tests.database.migration_authorization import create_migration_authorization


def _migrate(database_path: Path, tmp_path: Path) -> None:
    catalog = load_migration_catalog(Path("migrations/sqlite"))
    initialize_database(database_path)
    apply_pending_migrations(
        database_path,
        catalog,
        backup_authorization=create_migration_authorization(
            database_path, catalog, tmp_path / "backup.sqlite3"
        ),
    )


def _config(tmp_path: Path, database_path: Path, *, include_results_root: bool) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir(exist_ok=True)
    source = source_root / "input.txt"
    source.write_text("电子发票（普通发票）\n发票号码：{SYN_DOC_REF}", encoding="utf-8")
    config = {
        "database_path": str(database_path),
        "source_binding_id": "incoming",
        "archive_binding_id": "archive",
        "bindings": [
            {
                "binding_id": "incoming", "provider": "local", "node_id": "test",
                "logical_root": "business://incoming/", "physical_root": str(source_root),
                "roles": ["source"], "readable": True, "writable": False,
            },
            {
                "binding_id": "archive", "provider": "local", "node_id": "test",
                "logical_root": "business://archive/", "physical_root": str(tmp_path / "archive"),
                "roles": ["archive"], "readable": True, "writable": True,
            },
        ],
    }
    if include_results_root:
        config["results_root"] = str(tmp_path / "results")
    config_path = tmp_path / ("with-results.json" if include_results_root else "missing-results.json")
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config_path, source


def test_prepare_review_requires_results_root_and_never_changes_source(tmp_path, capsys) -> None:
    database_path = tmp_path / "file-organizer.sqlite3"
    _migrate(database_path, tmp_path)
    missing, source = _config(tmp_path, database_path, include_results_root=False)
    original = source.read_bytes()

    assert main(["--config", str(missing), "prepare-review", "--goal", "解析", str(source)]) == 2
    assert source.read_bytes() == original

    config, source = _config(tmp_path, database_path, include_results_root=True)
    assert main(["--config", str(config), "prepare-review", "--goal", "解析", str(source)]) == 0
    output = json.loads(capsys.readouterr().out)

    assert (tmp_path / "results" / "runs").exists()
    assert output["status"] == "awaiting_review"
    assert source.read_bytes() == original
