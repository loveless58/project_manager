import sqlite3
from pathlib import Path

import pytest

from infrastructure.database.contracts import DatabaseBusyError, UnitOfWorkStateError
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.connection import (
    SqliteConnectionOptions,
    open_sqlite_connection,
)
from infrastructure.database.sqlite.migration_runner import (
    apply_pending_migrations,
    initialize_database,
)
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from platform_core.ports import Repository, UnitOfWork
from tests.database.fixture_repository import FixtureEntity, FixtureRepository
from tests.database.migration_authorization import create_migration_authorization


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    database_path = tmp_path / "state.sqlite3"
    catalog = load_migration_catalog(FIXTURE_MIGRATIONS)
    initialize_database(database_path)
    apply_pending_migrations(
        database_path,
        catalog,
        backup_authorization=create_migration_authorization(
            database_path,
            catalog,
            tmp_path / "uow-migration-backup.sqlite3",
        ),
    )
    return database_path


def test_write_uow_requires_explicit_commit(database: Path) -> None:
    with SqliteUnitOfWork(database, mode="write") as uow:
        FixtureRepository(uow.connection).add(FixtureEntity("e-1", "value", None))

    with SqliteUnitOfWork(database, mode="read") as uow:
        assert FixtureRepository(uow.connection).get("e-1") is None


def test_commit_makes_write_visible_to_new_read_uow(database: Path) -> None:
    with SqliteUnitOfWork(database, mode="write") as uow:
        FixtureRepository(uow.connection).add(FixtureEntity("e-1", "value", None))
        uow.commit()

    with SqliteUnitOfWork(database) as uow:
        assert FixtureRepository(uow.connection).get("e-1") == FixtureEntity(
            "e-1", "value", None
        )


def test_exception_exit_rolls_back_and_propagates_original_error(database: Path) -> None:
    with pytest.raises(RuntimeError, match="original failure"):
        with SqliteUnitOfWork(database, mode="write") as uow:
            FixtureRepository(uow.connection).add(FixtureEntity("e-1", "value", None))
            raise RuntimeError("original failure")

    with SqliteUnitOfWork(database) as uow:
        assert FixtureRepository(uow.connection).get("e-1") is None


def test_database_enforces_unique_entity_values(database: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with SqliteUnitOfWork(database, mode="write") as uow:
            repository = FixtureRepository(uow.connection)
            repository.add(FixtureEntity("e-1", "unique", None))
            repository.add(FixtureEntity("e-2", "unique", None))

    with SqliteUnitOfWork(database) as uow:
        assert FixtureRepository(uow.connection).get("e-1") is None


def test_database_enforces_entity_parent_foreign_key(database: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with SqliteUnitOfWork(database, mode="write") as uow:
            FixtureRepository(uow.connection).add(
                FixtureEntity("e-1", "value", "missing-parent")
            )


def test_active_uows_use_isolated_connections_and_hide_uncommitted_write(
    database: Path,
) -> None:
    with SqliteUnitOfWork(database, mode="write") as writer:
        FixtureRepository(writer.connection).add(FixtureEntity("e-1", "value", None))
        with SqliteUnitOfWork(database) as reader:
            assert writer.connection is not reader.connection
            assert FixtureRepository(reader.connection).get("e-1") is None
        writer.commit()

    with SqliteUnitOfWork(database) as reader:
        assert FixtureRepository(reader.connection).get("e-1") == FixtureEntity(
            "e-1", "value", None
        )


def test_write_uow_maps_busy_timeout_to_database_busy(database: Path) -> None:
    locking_connection = open_sqlite_connection(database)
    locking_connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(DatabaseBusyError, match="database is busy"):
            with SqliteUnitOfWork(
                database,
                mode="write",
                options=SqliteConnectionOptions(busy_timeout_ms=1),
            ):
                pass
    finally:
        locking_connection.rollback()
        locking_connection.close()


def test_uow_rejects_operations_before_enter(database: Path) -> None:
    uow = SqliteUnitOfWork(database)

    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        _ = uow.connection
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.commit()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.rollback()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.__exit__(None, None, None)


@pytest.mark.parametrize("finalize", ["commit", "rollback"])
def test_uow_rejects_operations_after_manual_finalization(
    database: Path, finalize: str
) -> None:
    uow = SqliteUnitOfWork(database, mode="write")
    uow.__enter__()
    getattr(uow, finalize)()

    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        _ = uow.connection
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.commit()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.rollback()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.__enter__()

    assert uow.__exit__(None, None, None) is None


def test_uow_rejects_reentry_and_operations_after_exit(database: Path) -> None:
    uow = SqliteUnitOfWork(database)
    uow.__enter__()

    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.__enter__()

    assert uow.__exit__(None, None, None) is None
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        _ = uow.connection
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.commit()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.rollback()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.__enter__()
    with pytest.raises(UnitOfWorkStateError, match="unit of work is not active"):
        uow.__exit__(None, None, None)


def test_runtime_protocols_accept_sqlite_adapters(database: Path) -> None:
    with SqliteUnitOfWork(database, mode="read") as uow:
        repository = FixtureRepository(uow.connection)
        assert isinstance(repository, Repository)
        assert isinstance(uow, UnitOfWork)
