import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from ..contracts import DatabaseBusyError, DatabaseIntegrityError


@dataclass(frozen=True, slots=True)
class SqliteConnectionOptions:
    busy_timeout_ms: int = 5_000


def open_sqlite_connection(
    database_path: Path,
    *,
    options: SqliteConnectionOptions = SqliteConnectionOptions(),
    create: bool = False,
    check_same_thread: bool = True,
) -> sqlite3.Connection:
    target = str(database_path)
    use_uri = False
    if not create:
        target = f"{database_path.resolve().as_uri()}?mode=rw"
        use_uri = True

    try:
        connection = sqlite3.connect(
            target,
            timeout=options.busy_timeout_ms / 1_000,
            isolation_level=None,
            uri=use_uri,
            check_same_thread=check_same_thread,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {options.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection
    except sqlite3.DatabaseError as error:
        if "connection" in locals():
            connection.close()
        _raise_mapped_sqlite_error(error)


def _raise_mapped_sqlite_error(error: sqlite3.DatabaseError) -> NoReturn:
    error_code = getattr(error, "sqlite_errorcode", None)
    primary_code = error_code & 0xFF if isinstance(error_code, int) else None
    if primary_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        raise DatabaseBusyError("database is busy") from None
    if primary_code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        raise DatabaseIntegrityError("database integrity check failed") from None
    raise error


__all__ = ["SqliteConnectionOptions", "open_sqlite_connection"]
