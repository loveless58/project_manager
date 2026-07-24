import sqlite3

import pytest

import infrastructure.database.sqlite.connection as sqlite_connection
from infrastructure.database.contracts import DatabaseBusyError, DatabaseIntegrityError
from infrastructure.database.sqlite.connection import (
    SqliteConnectionOptions,
    _raise_mapped_sqlite_error,
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


@pytest.mark.parametrize(
    "filename",
    [
        "state with space.sqlite3",
        "状态.sqlite3",
        "state#hash.sqlite3",
        "state%percent.sqlite3",
    ],
)
def test_open_without_create_handles_existing_special_character_path(
    tmp_path, filename
):
    database = tmp_path / filename
    seed = sqlite3.connect(database)
    seed.execute("CREATE TABLE marker (value INTEGER NOT NULL)")
    seed.execute("INSERT INTO marker VALUES (42)")
    seed.commit()
    seed.close()

    connection = open_sqlite_connection(database, create=False)
    try:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == 42
    finally:
        connection.close()


@pytest.mark.parametrize(
    "filename",
    [
        "missing with space.sqlite3",
        "缺失.sqlite3",
        "missing#hash.sqlite3",
        "missing?question.sqlite3",
        "missing%percent.sqlite3",
    ],
)
def test_open_without_create_does_not_create_missing_special_character_path(
    tmp_path, filename
):
    database = tmp_path / filename

    with pytest.raises(sqlite3.OperationalError):
        open_sqlite_connection(database, create=False)

    assert not database.exists()


def test_open_without_create_encodes_all_special_characters_in_uri(
    monkeypatch, tmp_path
):
    captured = {}

    class _SuccessfulPragmaConnection:
        def __init__(self):
            self.row_factory = None

        def execute(self, sql):
            return self

        def close(self):
            return None

    def connect(target, **kwargs):
        captured["target"] = target
        captured["uri"] = kwargs["uri"]
        return _SuccessfulPragmaConnection()

    monkeypatch.setattr(sqlite_connection.sqlite3, "connect", connect)
    database = tmp_path / "existing 空格状态#?%.sqlite3"

    connection = open_sqlite_connection(database, create=False)
    connection.close()

    uri_path = captured["target"].removesuffix("?mode=rw")
    assert captured["uri"] is True
    assert "%20" in uri_path
    assert "%23" in uri_path
    assert "%3F" in uri_path
    assert "%25" in uri_path
    assert "状态" not in uri_path


@pytest.mark.parametrize(
    "error_code",
    [
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_BUSY | (2 << 8),
        sqlite3.SQLITE_LOCKED | (3 << 8),
    ],
)
def test_busy_and_locked_error_codes_map_to_stable_public_error(error_code):
    sqlite_error = sqlite3.OperationalError("database path is sensitive")
    sqlite_error.sqlite_errorcode = error_code

    with pytest.raises(DatabaseBusyError) as raised:
        _raise_mapped_sqlite_error(sqlite_error)

    assert str(raised.value) == "database is busy"


@pytest.mark.parametrize(
    "error_code",
    [
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_NOTADB,
        sqlite3.SQLITE_CORRUPT | (1 << 8),
        sqlite3.SQLITE_NOTADB | (4 << 8),
    ],
)
def test_corrupt_and_notadb_error_codes_map_to_stable_public_error(error_code):
    sqlite_error = sqlite3.DatabaseError("database path is sensitive")
    sqlite_error.sqlite_errorcode = error_code

    with pytest.raises(DatabaseIntegrityError) as raised:
        _raise_mapped_sqlite_error(sqlite_error)

    assert str(raised.value) == "database integrity check failed"


class _PragmaFailingConnection:
    def __init__(self, sqlite_error):
        self.sqlite_error = sqlite_error
        self.row_factory = None
        self.closed = False

    def execute(self, sql):
        raise self.sqlite_error

    def close(self):
        self.closed = True


def test_pragma_initialization_failure_closes_created_connection(monkeypatch, tmp_path):
    sqlite_error = sqlite3.DatabaseError("database is not valid")
    sqlite_error.sqlite_errorcode = sqlite3.SQLITE_NOTADB
    connection = _PragmaFailingConnection(sqlite_error)
    monkeypatch.setattr(
        sqlite_connection.sqlite3,
        "connect",
        lambda *args, **kwargs: connection,
    )

    with pytest.raises(DatabaseIntegrityError):
        open_sqlite_connection(tmp_path / "state.sqlite3", create=True)

    assert connection.closed is True


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
