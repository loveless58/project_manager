from __future__ import annotations

import copy
import io
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from infrastructure.database.cli import EXIT_INCOMPATIBLE, main as database_cli
from infrastructure.database.contracts import (
    MigrationCatalogError,
    MigrationExecutionError,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.backup import create_sqlite_backup
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)
from platform_core.settings import SettingsError, load_app_settings
from tests.database.migration_authorization import create_migration_authorization


def _write_catalog(
    tmp_path: Path,
    *statements: str,
    name: str = "migrations",
):
    directory = tmp_path / name
    directory.mkdir()
    for version, sql in enumerate(statements, start=1):
        (directory / f"{version:04d}_change_{version}.sql").write_text(
            sql,
            encoding="utf-8",
        )
    return directory, load_migration_catalog(directory)


def _authorization(
    database: Path,
    catalog,
    tmp_path: Path,
    name: str,
):
    return create_migration_authorization(
        database,
        catalog,
        tmp_path / f"{name}.sqlite3",
    )


def _history(database: Path) -> list[tuple[int, str]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT version, name FROM _schema_migrations ORDER BY version"
        ).fetchall()


def _tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }


def test_migration_sql_cannot_delete_reserved_history_or_commit_schema_side_effect(
    tmp_path,
):
    directory, first_catalog = _write_catalog(
        tmp_path,
        "CREATE TABLE retained_item(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    apply_pending_migrations(
        database,
        first_catalog,
        backup_authorization=_authorization(
            database,
            first_catalog,
            tmp_path,
            "before-first",
        ),
    )
    before_tables = _tables(database)
    before_history = _history(database)

    (directory / "0002_change_2.sql").write_text(
        "DELETE FROM _schema_migrations WHERE version = 1;\n"
        "CREATE TABLE unauthorized_side_effect(id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    catalog = load_migration_catalog(directory)

    with pytest.raises(MigrationExecutionError, match="migration 2 failed"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=_authorization(
                database,
                catalog,
                tmp_path,
                "before-malicious",
            ),
        )

    assert _tables(database) == before_tables
    assert _history(database) == before_history


def test_migration_sql_cannot_change_stateful_pragma_before_commit(tmp_path):
    directory, first_catalog = _write_catalog(
        tmp_path,
        "CREATE TABLE retained_item(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "state.sqlite3"
    initialize_database(database)
    apply_pending_migrations(
        database,
        first_catalog,
        backup_authorization=_authorization(
            database,
            first_catalog,
            tmp_path,
            "pragma-first",
        ),
    )
    (directory / "0002_change_2.sql").write_text(
        "PRAGMA main.user_version = 73;\n"
        "CREATE TABLE pragma_side_effect(id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )
    catalog = load_migration_catalog(directory)

    with pytest.raises(MigrationExecutionError, match="migration 2 failed"):
        apply_pending_migrations(
            database,
            catalog,
            backup_authorization=_authorization(
                database,
                catalog,
                tmp_path,
                "before-pragma",
            ),
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "pragma_side_effect" not in _tables(database)
    assert _history(database) == [(1, "change_1")]


def test_catalog_capability_rejects_copy_and_untrusted_sequences_before_io(tmp_path):
    _, catalog = _write_catalog(
        tmp_path,
        "CREATE TABLE protected_item(id INTEGER PRIMARY KEY);\n",
    )

    with pytest.raises(TypeError):
        copy.copy(catalog)

    forged_item = replace(catalog[0], name="forged")
    invalid_catalogs = [
        tuple(catalog),
        (forged_item,),
        (catalog[0], catalog[0]),
        (replace(catalog[0], version=2),),
    ]
    for index, invalid_catalog in enumerate(invalid_catalogs):
        database = tmp_path / f"untrusted-{index}.sqlite3"
        output = tmp_path / f"untrusted-{index}.backup.sqlite3"
        with pytest.raises(MigrationCatalogError, match="catalog"):
            create_sqlite_backup(database, output, invalid_catalog)
        assert not database.exists()
        assert not output.exists()
        assert not Path(f"{output}.manifest.json").exists()


def test_catalog_capability_rejects_reordered_items_before_database_access(tmp_path):
    _, catalog = _write_catalog(
        tmp_path,
        "CREATE TABLE first_item(id INTEGER PRIMARY KEY);\n",
        "CREATE TABLE second_item(id INTEGER PRIMARY KEY);\n",
    )
    database = tmp_path / "reordered.sqlite3"

    with pytest.raises(MigrationCatalogError, match="catalog"):
        apply_pending_migrations(
            database,
            (catalog[1], catalog[0]),
            backup_authorization=None,
        )

    assert not database.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"deployment_mode": "local", "unexpected": True},
        {"database": {"sqlite_paht": "state.sqlite3"}},
        {"database": {"provider": "sqlite", "unexpected": True}},
        {"providers": {"structure_index": "disabled", "unexpected": True}},
        {"database": []},
        {"providers": "disabled"},
    ],
)
def test_explicit_json_configuration_uses_strict_schema(tmp_path, payload):
    config = tmp_path / "project-manager.local.json"
    config.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SettingsError, match="configuration"):
        load_app_settings(config_file=config, environ={})


def test_cli_migrate_rejects_misspelled_sqlite_path_without_touching_default(
    tmp_path,
):
    runtime = tmp_path / "runtime"
    config = tmp_path / "project-manager.local.json"
    config.write_text(
        json.dumps(
            {
                "deployment_mode": "local",
                "business_root": str(tmp_path / "business"),
                "runtime_workspace": str(runtime),
                "database": {
                    "provider": "sqlite",
                    "sqlite_paht": str(tmp_path / "intended.sqlite3"),
                },
                "providers": {
                    "document_store": "local",
                    "structure_index": "disabled",
                    "projection_writer": "filesystem",
                    "projection_root": str(runtime / "projections"),
                },
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    code = database_cli(
        [
            "--config",
            str(config),
            "--json",
            "migrate",
            "--backup-dir",
            str(runtime / "backups"),
        ],
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert code == EXIT_INCOMPATIBLE
    assert json.loads(stdout.getvalue())["error_code"] == "DB.CONFIGURATION"
    assert not (runtime / "state" / "project_manager.sqlite3").exists()
    assert not (tmp_path / "intended.sqlite3").exists()


def test_cli_backup_rejects_business_root_output_without_creating_file(tmp_path):
    business_root = tmp_path / "business"
    business_root.mkdir()
    runtime = tmp_path / "runtime"
    database = runtime / "state" / "state.sqlite3"
    database.parent.mkdir(parents=True)
    initialize_database(database)
    config = tmp_path / "project-manager.local.json"
    config.write_text(
        json.dumps(
            {
                "deployment_mode": "local",
                "business_root": str(business_root),
                "runtime_workspace": str(runtime),
                "database": {
                    "provider": "sqlite",
                    "sqlite_path": str(database),
                },
                "providers": {
                    "document_store": "local",
                    "structure_index": "disabled",
                    "projection_writer": "filesystem",
                    "projection_root": str(runtime / "projections"),
                },
            }
        ),
        encoding="utf-8",
    )
    output = business_root / "forbidden-backup.sqlite3"

    code = database_cli(
        ["--config", str(config), "backup", "--output", str(output)],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert code == EXIT_INCOMPATIBLE
    assert not output.exists()
    assert not Path(f"{output}.manifest.json").exists()


def test_single_quoted_schema_identifier_is_rejected_by_catalog(tmp_path):
    migration = tmp_path / "0001_escaped_schema.sql"
    migration.write_text(
        "CREATE TABLE 'auxiliary'.escaped_item(id INTEGER PRIMARY KEY);\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationCatalogError, match="schema boundary"):
        load_migration_catalog(tmp_path)


def test_sqlite_treats_single_quoted_schema_token_as_identifier(tmp_path):
    database = tmp_path / "main.sqlite3"
    auxiliary = tmp_path / "auxiliary.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("ATTACH DATABASE ? AS auxiliary", (str(auxiliary),))
        connection.execute(
            "CREATE TABLE 'auxiliary'.escaped_item(id INTEGER PRIMARY KEY)"
        )
        escaped = connection.execute(
            "SELECT name FROM auxiliary.sqlite_schema WHERE name = 'escaped_item'"
        ).fetchone()
    assert escaped == ("escaped_item",)


def test_single_quoted_string_literal_remains_allowed_in_catalog(tmp_path):
    migration = tmp_path / "0001_string_literal.sql"
    migration.write_text(
        "CREATE TABLE allowed_item("
        "value TEXT CHECK(value <> 'auxiliary'));\n",
        encoding="utf-8",
    )

    catalog = load_migration_catalog(tmp_path)

    assert [item.name for item in catalog] == ["string_literal"]
