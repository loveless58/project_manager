from collections.abc import Sequence
from pathlib import Path

from infrastructure.database.contracts import MigrationInfo
from infrastructure.database.sqlite import backup as backup_module


def create_migration_authorization(
    database_path: Path,
    catalog: Sequence[MigrationInfo],
    output_path: Path,
):
    """Create, reload/verify, and authorize a real migration backup."""

    result = backup_module.create_sqlite_backup(database_path, output_path, catalog)
    verified = backup_module.verify_migration_backup(result)
    return backup_module.authorize_migration_backup(
        database_path,
        verified,
        catalog,
    )
