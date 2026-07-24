import sqlite3
from enum import Enum, auto
from pathlib import Path
from types import TracebackType
from typing import Literal

from ..contracts import UnitOfWorkStateError
from .connection import (
    SqliteConnectionOptions,
    _raise_mapped_sqlite_error,
    open_sqlite_connection,
)


UowMode = Literal["read", "write"]


class _UowState(Enum):
    NEW = auto()
    ACTIVE = auto()
    COMMITTED = auto()
    ROLLED_BACK = auto()
    CLOSED = auto()


class SqliteUnitOfWork:
    def __init__(
        self,
        database_path: Path,
        *,
        mode: UowMode = "read",
        options: SqliteConnectionOptions = SqliteConnectionOptions(),
    ) -> None:
        self._database_path = database_path
        self._mode = mode
        self._options = options
        self._connection: sqlite3.Connection | None = None
        self._state = _UowState.NEW

    def __enter__(self) -> "SqliteUnitOfWork":
        self._require_state(_UowState.NEW)
        connection = open_sqlite_connection(self._database_path, options=self._options)
        try:
            connection.execute("BEGIN IMMEDIATE" if self._mode == "write" else "BEGIN")
        except sqlite3.DatabaseError as error:
            connection.close()
            _raise_mapped_sqlite_error(error)
        self._connection = connection
        self._state = _UowState.ACTIVE
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._require_state(
            _UowState.ACTIVE,
            _UowState.COMMITTED,
            _UowState.ROLLED_BACK,
        )
        rollback_error: BaseException | None = None
        if self._state is _UowState.ACTIVE:
            try:
                self._active_connection().rollback()
            except sqlite3.DatabaseError as error:
                try:
                    _raise_mapped_sqlite_error(error)
                except BaseException as mapped_error:
                    rollback_error = mapped_error
            except BaseException as error:
                rollback_error = error
            else:
                self._state = _UowState.ROLLED_BACK

        close_error = self._close_connection()
        self._state = _UowState.CLOSED

        if exc_type is None:
            if rollback_error is not None:
                raise rollback_error
            if close_error is not None:
                raise close_error
        return None

    @property
    def connection(self) -> sqlite3.Connection:
        return self._active_connection()

    def commit(self) -> None:
        connection = self._active_connection()
        try:
            connection.commit()
        except sqlite3.DatabaseError as error:
            _raise_mapped_sqlite_error(error)
        self._state = _UowState.COMMITTED
        close_error = self._close_connection()
        if close_error is not None:
            raise close_error

    def rollback(self) -> None:
        connection = self._active_connection()
        try:
            connection.rollback()
        except sqlite3.DatabaseError as error:
            _raise_mapped_sqlite_error(error)
        self._state = _UowState.ROLLED_BACK
        close_error = self._close_connection()
        if close_error is not None:
            raise close_error

    def _active_connection(self) -> sqlite3.Connection:
        self._require_state(_UowState.ACTIVE)
        connection = self._connection
        if connection is None:
            raise UnitOfWorkStateError("unit of work is not active")
        return connection

    def _close_connection(self) -> BaseException | None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return None
        try:
            connection.close()
        except BaseException as error:
            return error
        return None

    def _require_state(self, *allowed_states: _UowState) -> None:
        if self._state not in allowed_states:
            raise UnitOfWorkStateError("unit of work is not active")


__all__ = ["SqliteUnitOfWork", "UowMode"]
