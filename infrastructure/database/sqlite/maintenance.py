"""Cooperative cross-thread/process maintenance exclusion for SQLite writes.

Managed writers use one stable lock-file byte per database.  This prevents
``SqliteUnitOfWork(mode="write")`` from entering the intentional commit gaps
between independently committed migrations.  Raw ``sqlite3`` connections do
not participate; the migration generation witness and lock-held recovery-point
refresh cover that explicitly documented boundary.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from typing import Iterator

from ..contracts import DatabaseBusyError


_LOCK_RETRY_SECONDS = 0.01
_MUTEX_REGISTRY_LOCK = threading.Lock()
_MUTEX_REGISTRY: dict[Path, "_DatabaseMaintenanceMutex"] = {}


class _DatabaseMaintenanceMutex:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._thread_lock = threading.RLock()
        self._thread_state = threading.local()

    @contextmanager
    def hold(self, timeout_ms: int) -> Iterator[None]:
        deadline = monotonic() + max(timeout_ms, 0) / 1_000
        remaining = max(0.0, deadline - monotonic())
        if not self._thread_lock.acquire(timeout=remaining):
            raise DatabaseBusyError("database is busy")

        entered = False
        try:
            depth = getattr(self._thread_state, "depth", 0)
            if depth == 0:
                descriptor = _open_lock_file(self._lock_path)
                try:
                    _acquire_file_lock(descriptor, deadline)
                except BaseException:
                    os.close(descriptor)
                    raise
                self._thread_state.descriptor = descriptor
            self._thread_state.depth = depth + 1
            entered = True
            yield
        finally:
            if entered:
                depth = self._thread_state.depth - 1
                self._thread_state.depth = depth
                if depth == 0:
                    descriptor = self._thread_state.descriptor
                    try:
                        _release_file_lock(descriptor)
                    finally:
                        os.close(descriptor)
                        del self._thread_state.descriptor
            self._thread_lock.release()


def _open_lock_file(lock_path: Path) -> int:
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        return descriptor
    except OSError:
        if "descriptor" in locals():
            os.close(descriptor)
        raise DatabaseBusyError("database is busy") from None


def _acquire_file_lock(descriptor: int, deadline: float) -> None:
    while True:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise DatabaseBusyError("database is busy") from None
            sleep(min(_LOCK_RETRY_SECONDS, remaining))


def _release_file_lock(descriptor: int) -> None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        raise DatabaseBusyError("database is busy") from None


def _maintenance_lock_path(database_path: Path) -> Path:
    resolved = database_path.expanduser().resolve(strict=False)
    return resolved.with_name(f".{resolved.name}.maintenance.lock")


def _mutex_for(database_path: Path) -> _DatabaseMaintenanceMutex:
    lock_path = _maintenance_lock_path(database_path)
    with _MUTEX_REGISTRY_LOCK:
        mutex = _MUTEX_REGISTRY.get(lock_path)
        if mutex is None:
            mutex = _DatabaseMaintenanceMutex(lock_path)
            _MUTEX_REGISTRY[lock_path] = mutex
        return mutex


@contextmanager
def database_maintenance_lock(
    database_path: Path,
    *,
    timeout_ms: int,
) -> Iterator[None]:
    """Hold the cooperative maintenance lease for one database.

    The lease is reentrant within a thread and exclusive across managed writer
    threads/processes.  It intentionally does not claim control over raw or
    third-party SQLite connections.
    """

    with _mutex_for(database_path).hold(timeout_ms):
        yield


__all__ = ["database_maintenance_lock"]
