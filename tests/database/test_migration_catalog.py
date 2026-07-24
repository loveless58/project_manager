import shutil
from pathlib import Path

import pytest

from infrastructure.database.contracts import MigrationCatalogError
from infrastructure.database.migration_catalog import (
    catalog_target_version,
    load_migration_catalog,
)


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


def test_catalog_loads_empty_directory(tmp_path):
    catalog = load_migration_catalog(tmp_path)

    assert catalog == ()
    assert catalog_target_version(catalog) == 0


def test_catalog_loads_fixture_migrations_in_numeric_order(tmp_path):
    shutil.copy2(FIXTURE_MIGRATIONS / "0002_create_fixture_entity.sql", tmp_path)
    shutil.copy2(FIXTURE_MIGRATIONS / "0001_create_fixture_parent.sql", tmp_path)

    catalog = load_migration_catalog(tmp_path)

    assert [(item.version, item.name) for item in catalog] == [
        (1, "create_fixture_parent"),
        (2, "create_fixture_entity"),
    ]
    assert catalog_target_version(catalog) == 2


def test_catalog_uses_stable_binary_sha256(tmp_path):
    migration = tmp_path / "0001_create_item.sql"
    migration.write_bytes(b"CREATE TABLE item (id INTEGER PRIMARY KEY);\r\n")

    catalog = load_migration_catalog(tmp_path)

    assert catalog[0].checksum_sha256 == (
        "7beef9f011f5aa26c4c80c6427c60525c73b73e73ded4ce7533f3040863fb419"
    )
    assert catalog[0].path == migration


def test_catalog_rejects_duplicate_versions(tmp_path):
    (tmp_path / "0001_create_one.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_create_two.sql").write_text("SELECT 2;", encoding="utf-8")

    with pytest.raises(MigrationCatalogError, match="duplicate version"):
        load_migration_catalog(tmp_path)


def test_catalog_rejects_gaps(tmp_path):
    (tmp_path / "0001_create_one.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0003_create_three.sql").write_text("SELECT 3;", encoding="utf-8")

    with pytest.raises(MigrationCatalogError, match="contiguous"):
        load_migration_catalog(tmp_path)


def test_catalog_rejects_version_zero(tmp_path):
    (tmp_path / "0000_create_zero.sql").write_text("SELECT 0;", encoding="utf-8")

    with pytest.raises(MigrationCatalogError, match="contiguous"):
        load_migration_catalog(tmp_path)


@pytest.mark.parametrize(
    "filename",
    [
        "001_create_short.sql",
        "0001_Create_upper.sql",
        "0001_create-dash.sql",
        "0001_.sql",
        "not_a_migration.sql",
    ],
)
def test_catalog_rejects_illegal_filenames(tmp_path, filename):
    (tmp_path / filename).write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(MigrationCatalogError, match="filename"):
        load_migration_catalog(tmp_path)


def test_catalog_rejects_invalid_utf8(tmp_path):
    (tmp_path / "0001_invalid_encoding.sql").write_bytes(b"\xff")

    with pytest.raises(MigrationCatalogError, match="UTF-8") as raised:
        load_migration_catalog(tmp_path)

    assert raised.value.__cause__ is None


def test_catalog_accepts_trigger_body_without_splitting_sql(tmp_path):
    migration = tmp_path / "0001_create_audit_trigger.sql"
    migration.write_text(
        "CREATE TABLE item(id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE audit(item_id INTEGER);\n"
        "CREATE TRIGGER item_audit AFTER INSERT ON item BEGIN\n"
        "  INSERT INTO audit(item_id) VALUES (NEW.id);\n"
        "END;\n",
        encoding="utf-8",
    )
    catalog = load_migration_catalog(tmp_path)
    assert [item.version for item in catalog] == [1]


@pytest.mark.parametrize(
    "statement",
    [
        "BEGIN;",
        "COMMIT;",
        "END TRANSACTION;",
        "ROLLBACK;",
        "SAVEPOINT x;",
        "RELEASE x;",
    ],
)
def test_catalog_rejects_runner_transaction_escape(tmp_path, statement):
    (tmp_path / "0001_bad_transaction.sql").write_text(statement, encoding="utf-8")
    with pytest.raises(MigrationCatalogError, match="transaction control"):
        load_migration_catalog(tmp_path)


def test_catalog_allows_transaction_control_words_in_literals_and_comments(tmp_path):
    (tmp_path / "0001_safe_text.sql").write_text(
        "CREATE TABLE note(text TEXT DEFAULT 'BEGIN;');\n"
        '-- COMMIT;\n'
        "/* ROLLBACK; */\n"
        'INSERT INTO note(text) VALUES (\"SAVEPOINT x;\");\n'
        "INSERT INTO note(text) VALUES (`RELEASE x;`);\n"
        "INSERT INTO note(text) VALUES ([BEGIN;]);\n",
        encoding="utf-8",
    )

    assert [item.version for item in load_migration_catalog(tmp_path)] == [1]


@pytest.mark.parametrize(
    "statement",
    [
        "ATTACH DATABASE 'side.sqlite3' AS side;",
        "DETACH DATABASE side;",
        "CREATE TEMP TABLE transient_item(id INTEGER);",
        "CREATE TEMPORARY VIEW transient_view AS SELECT 1;",
        "CREATE TABLE temp.transient_item(id INTEGER);",
        "CREATE TABLE auxiliary.item(id INTEGER);",
        'CREATE TABLE "auxiliary"."item"(id INTEGER);',
        "DROP VIEW auxiliary.item_view;",
        "ALTER TABLE auxiliary.item ADD COLUMN value TEXT;",
        "INSERT INTO auxiliary.item(id) VALUES (1);",
        "UPDATE auxiliary.item SET id = 2;",
        "DELETE FROM auxiliary.item;",
        "PRAGMA auxiliary.user_version = 123;",
        "PRAGMA temp.user_version;",
        'PRAGMA "auxiliary"."user_version" = 123;',
        "WITH payload(id) AS (VALUES (1)) "
        "INSERT INTO auxiliary.item(id) SELECT id FROM payload;",
        "CREATE TRIGGER main.escape AFTER INSERT ON main.item BEGIN "
        "INSERT INTO auxiliary.audit(item_id) VALUES (NEW.id); "
        "END;",
    ],
)
def test_catalog_rejects_sqlite_schema_boundary_escape(tmp_path, statement):
    (tmp_path / "0001_schema_escape.sql").write_text(
        statement,
        encoding="utf-8",
    )

    with pytest.raises(MigrationCatalogError, match="schema boundary"):
        load_migration_catalog(tmp_path)


def test_catalog_allows_schema_boundary_words_in_literals_and_comments(tmp_path):
    (tmp_path / "0001_safe_schema_text.sql").write_text(
        "CREATE TABLE main.note(text TEXT DEFAULT 'ATTACH DATABASE side;');\n"
        "-- DETACH DATABASE side;\n"
        "/* CREATE TEMP TABLE escaped(id INTEGER); */\n"
        "INSERT INTO main.note(text) VALUES ('UPDATE auxiliary.note SET x = 1;');\n",
        encoding="utf-8",
    )

    assert [item.version for item in load_migration_catalog(tmp_path)] == [1]


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 'create' 'temp';",
        'SELECT "create" "temp";',
        "CREATE TABLE main.note('create' temp);",
        'CREATE TABLE main.note("create" temp);',
        "CREATE TABLE main.note(value TEXT); "
        "CREATE TRIGGER main.note_audit AFTER INSERT ON main.note BEGIN "
        "SELECT 'create' 'temp'; "
        "END;",
    ],
)
def test_catalog_does_not_treat_expression_tokens_as_operations(tmp_path, statement):
    (tmp_path / "0001_legal_expression.sql").write_text(
        statement,
        encoding="utf-8",
    )

    assert [item.version for item in load_migration_catalog(tmp_path)] == [1]


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE main.item(id INTEGER);",
        "INSERT INTO main.item(id) VALUES (1);",
        "UPDATE main.item SET id = 2;",
        "DELETE FROM main.item;",
        "PRAGMA main.user_version = 123;",
        "PRAGMA main.user_version;",
        "PRAGMA foreign_keys = ON;",
    ],
)
def test_catalog_allows_explicit_main_schema_operations(tmp_path, statement):
    (tmp_path / "0001_main_schema.sql").write_text(statement, encoding="utf-8")

    assert [item.version for item in load_migration_catalog(tmp_path)] == [1]


@pytest.mark.parametrize("sql", ["SELECT 'unterminated", "/* unterminated"])
def test_catalog_rejects_lexically_incomplete_sql(tmp_path, sql):
    (tmp_path / "0001_incomplete.sql").write_text(sql, encoding="utf-8")

    with pytest.raises(
        MigrationCatalogError, match="migration SQL is not lexically complete"
    ):
        load_migration_catalog(tmp_path)
