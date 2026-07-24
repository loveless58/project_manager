import sqlite3
from pathlib import Path

import pytest

import infrastructure.database.sqlite.unit_of_work as unit_of_work_module
from infrastructure.database.contracts import BackupManifest, UnitOfWorkStateError
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from tests.database.fixture_repository import FixtureEntity, FixtureRepository


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    database_path = tmp_path / "state.sqlite3"
    initialize_database(database_path)
    apply_pending_migrations(
        database_path,
        load_migration_catalog(FIXTURE_MIGRATIONS),
        backup_manifest=BackupManifest(
            format_version=1,
            schema_version=0,
            catalog_target_version=2,
            sha256="0" * 64,
            size_bytes=1,
            created_at_utc="2026-07-24T00:00:00Z",
            sqlite_version=sqlite3.sqlite_version,
            integrity_check="ok",
        ),
    )
    return database_path


def test_commit_closes_saved_connection_and_persists_writes_once(database: Path) -> None:
    with SqliteUnitOfWork(database, mode="write") as uow:
        connection = uow.connection
        FixtureRepository(connection).add(FixtureEntity("e-1", "value", None))
        uow.commit()

        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute(
                "INSERT INTO fixture_entity (id, value, parent_id) VALUES (?, ?, ?)",
                ("e-2", "second", None),
            )

    with SqliteUnitOfWork(database) as uow:
        rows = uow.connection.execute(
            "SELECT id FROM fixture_entity ORDER BY id"
        ).fetchall()
    assert [row["id"] for row in rows] == ["e-1"]


def test_rollback_closes_saved_connection_and_discards_writes(database: Path) -> None:
    uow = SqliteUnitOfWork(database, mode="write")
    uow.__enter__()
    connection = uow.connection
    FixtureRepository(connection).add(FixtureEntity("e-1", "value", None))
    uow.rollback()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute(
            "INSERT INTO fixture_entity (id, value, parent_id) VALUES (?, ?, ?)",
            ("e-2", "second", None),
        )
    assert uow.__exit__(None, None, None) is None

    with SqliteUnitOfWork(database) as read_uow:
        assert FixtureRepository(read_uow.connection).get("e-1") is None
        assert FixtureRepository(read_uow.connection).get("e-2") is None


class _CleanupFailingConnection:
    def __init__(
        self,
        *,
        rollback_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def execute(self, sql: str) -> None:
        assert sql == "BEGIN"

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1
        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _uow_with_connection(
    monkeypatch, database: Path, connection: _CleanupFailingConnection
) -> SqliteUnitOfWork:
    monkeypatch.setattr(
        unit_of_work_module,
        "open_sqlite_connection",
        lambda *_args, **_kwargs: connection,
    )
    return SqliteUnitOfWork(database)


@pytest.mark.parametrize("failure", ["rollback", "close"])
def test_exit_preserves_business_error_when_cleanup_fails(
    monkeypatch, database: Path, failure: str
) -> None:
    cleanup_error = RuntimeError(f"{failure} cleanup failed")
    connection = _CleanupFailingConnection(
        rollback_error=cleanup_error if failure == "rollback" else None,
        close_error=cleanup_error if failure == "close" else None,
    )
    uow = _uow_with_connection(monkeypatch, database, connection)

    with pytest.raises(ValueError, match="business failure"):
        with uow:
            raise ValueError("business failure")

    assert connection.close_calls == 1
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.commit()


def test_exit_without_business_error_prioritizes_rollback_failure(
    monkeypatch, database: Path
) -> None:
    connection = _CleanupFailingConnection(
        rollback_error=RuntimeError("rollback cleanup failed"),
        close_error=RuntimeError("close cleanup failed"),
    )
    uow = _uow_with_connection(monkeypatch, database, connection)

    with pytest.raises(RuntimeError, match="rollback cleanup failed"):
        with uow:
            pass

    assert connection.close_calls == 1
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.rollback()


def test_exit_without_business_error_propagates_close_failure(
    monkeypatch, database: Path
) -> None:
    connection = _CleanupFailingConnection(
        close_error=RuntimeError("close cleanup failed")
    )
    uow = _uow_with_connection(monkeypatch, database, connection)

    with pytest.raises(RuntimeError, match="close cleanup failed"):
        with uow:
            pass

    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        _ = uow.connection


@pytest.mark.parametrize("method", ["commit", "rollback"])
def test_finalization_close_failure_clears_uow_state_and_reference(
    monkeypatch, database: Path, method: str
) -> None:
    connection = _CleanupFailingConnection(close_error=RuntimeError("close failed"))
    uow = _uow_with_connection(monkeypatch, database, connection)
    uow.__enter__()

    with pytest.raises(RuntimeError, match="close failed"):
        getattr(uow, method)()

    assert connection.close_calls == 1
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        getattr(uow, method)()
    assert uow.__exit__(None, None, None) is None
    assert connection.close_calls == 1
