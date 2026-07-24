import sqlite3

import pytest

from infrastructure.database.contracts import DatabaseBusyError
from infrastructure.database.sqlite.connection import (
    SqliteConnectionOptions,
    open_sqlite_connection,
)
from infrastructure.database.sqlite.schema import initialize_schema_metadata


def test_new_connection_applies_required_sqlite_options(tmp_path):
    database = tmp_path / "state.sqlite3"

    connection = open_sqlite_connection(
        database,
        options=SqliteConnectionOptions(busy_timeout_ms=1_234),
        create=True,
    )
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 1_234
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert connection.row_factory is sqlite3.Row
        assert connection.isolation_level is None
        assert connection.execute("SELECT 7 AS value").fetchone()["value"] == 7
    finally:
        connection.close()


def test_open_without_create_rejects_missing_database_without_creating_it(tmp_path):
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(sqlite3.OperationalError):
        open_sqlite_connection(database, create=False)

    assert not database.exists()


def test_initialize_maps_locked_database_to_stable_busy_error(tmp_path):
    database = tmp_path / "state.sqlite3"
    locking_connection = open_sqlite_connection(database, create=True)
    locking_connection.execute("CREATE TABLE lock_holder (id INTEGER PRIMARY KEY)")
    locking_connection.execute("BEGIN IMMEDIATE")

    try:
        with pytest.raises(DatabaseBusyError) as raised:
            initialize_schema_metadata(
                database,
                options=SqliteConnectionOptions(busy_timeout_ms=1),
            )
    finally:
        locking_connection.execute("ROLLBACK")
        locking_connection.close()

    assert str(raised.value) == "database is busy"

