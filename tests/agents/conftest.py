from contextlib import contextmanager
from pathlib import Path

import pytest

from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.migration_runner import apply_pending_migrations, initialize_database
from infrastructure.database.sqlite.unit_of_work import SqliteUnitOfWork
from tests.database.migration_authorization import create_migration_authorization


class _DatabasePath:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def write_uow(self):
        with SqliteUnitOfWork(self.path, mode="write") as uow:
            yield uow.connection
            uow.commit()


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "file-organizer.sqlite3"
    catalog = load_migration_catalog(Path("migrations/sqlite"))
    initialize_database(path)
    authorization = create_migration_authorization(path, catalog, tmp_path / "migration-backup.sqlite3")
    apply_pending_migrations(path, catalog, backup_authorization=authorization)
    return _DatabasePath(path)
