from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from infrastructure.database.contracts import (
    AppliedMigration,
    BackupError,
    BackupManifest,
    DatabaseBusyError,
    DatabaseConfigurationError,
    DatabaseIntegrityError,
    MigrationCatalogError,
    MigrationChecksumError,
    MigrationExecutionError,
    MigrationInfo,
    RestoreVerificationError,
    SchemaState,
    SchemaStatus,
    SchemaTooNewError,
    UnitOfWorkStateError,
    UnsupportedDatabaseProviderError,
)


@pytest.mark.parametrize(
    "instance, attribute, value",
    [
        (MigrationInfo(1, "initial", "abc", Path("migrations/001.sql")), "version", 2),
        (
            AppliedMigration(1, "initial", "abc", "2026-01-01T00:00:00Z", 12),
            "execution_ms",
            13,
        ),
        (SchemaStatus(SchemaState.CURRENT, 1, 1), "target_version", 2),
        (
            BackupManifest(1, 1, 1, "abc", 1, "2026-01-01T00:00:00Z", "3.45", "ok"),
            "size_bytes",
            2,
        ),
    ],
)
def test_database_dtos_are_frozen(instance, attribute, value):
    with pytest.raises(FrozenInstanceError):
        setattr(instance, attribute, value)


@pytest.mark.parametrize(
    ("error_type", "code"),
    [
        (DatabaseConfigurationError, "DB.CONFIGURATION"),
        (MigrationCatalogError, "DB.MIGRATION_CATALOG"),
        (MigrationChecksumError, "DB.MIGRATION_CHECKSUM"),
        (SchemaTooNewError, "DB.SCHEMA_TOO_NEW"),
        (MigrationExecutionError, "DB.MIGRATION_EXECUTION"),
        (DatabaseBusyError, "DB.BUSY"),
        (DatabaseIntegrityError, "DB.INTEGRITY"),
        (BackupError, "DB.BACKUP"),
        (RestoreVerificationError, "DB.RESTORE_VERIFICATION"),
        (UnitOfWorkStateError, "DB.UOW_STATE"),
        (UnsupportedDatabaseProviderError, "DB.UNSUPPORTED_PROVIDER"),
    ],
)
def test_database_errors_have_stable_public_codes(error_type, code):
    error = error_type("safe public message")

    assert error.code == code
    assert error.public_message == "safe public message"
