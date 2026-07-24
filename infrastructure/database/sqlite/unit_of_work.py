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
        try:
            if self._state is _UowState.ACTIVE:
                try:
                    self.rollback()
                except BaseException as error:
                    rollback_error = error
        finally:
            try:
                self._connection.close()  # type: ignore[union-attr]
            finally:
                self._connection = None
                self._state = _UowState.CLOSED

        if rollback_error is not None and exc_type is None:
            raise rollback_error
        return None

    @property
    def connection(self) -> sqlite3.Connection:
        self._require_state(_UowState.ACTIVE)
        return self._connection  # type: ignore[return-value]

    def commit(self) -> None:
        self._require_state(_UowState.ACTIVE)
        try:
            self.connection.commit()
        except sqlite3.DatabaseError as error:
            _raise_mapped_sqlite_error(error)
        self._state = _UowState.COMMITTED

    def rollback(self) -> None:
        self._require_state(_UowState.ACTIVE)
        try:
            self.connection.rollback()
        except sqlite3.DatabaseError as error:
            _raise_mapped_sqlite_error(error)
        self._state = _UowState.ROLLED_BACK

    def _require_state(self, *allowed_states: _UowState) -> None:
        if self._state not in allowed_states:
            raise UnitOfWorkStateError("unit of work is not active")


__all__ = ["SqliteUnitOfWork", "UowMode"]
