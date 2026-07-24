from pathlib import Path

import infrastructure.database as database
import infrastructure.database.sqlite as sqlite_database


ROOT_PUBLIC_NAMES = {
    "AppliedMigration",
    "BackupError",
    "BackupManifest",
    "DatabaseBusyError",
    "DatabaseConfigurationError",
    "DatabaseError",
    "DatabaseIntegrityError",
    "MigrationCatalogError",
    "MigrationChecksumError",
    "MigrationExecutionError",
    "MigrationInfo",
    "RestoreVerificationError",
    "SchemaState",
    "SchemaStatus",
    "SchemaTooNewError",
    "UnitOfWorkStateError",
    "UnsupportedDatabaseProviderError",
    "catalog_target_version",
    "load_migration_catalog",
}

SQLITE_PUBLIC_NAMES = {
    "BackupResult",
    "MigrationRunResult",
    "RestoreResult",
    "SqliteConnectionOptions",
    "SqliteUnitOfWork",
    "UowMode",
    "apply_pending_migrations",
    "check_database_integrity",
    "create_sqlite_backup",
    "initialize_database",
    "initialize_schema_metadata",
    "inspect_schema",
    "load_backup_manifest",
    "open_sqlite_connection",
    "read_applied_migrations",
    "verify_and_restore_sqlite",
    "verify_backup_artifacts",
}


def test_database_package_exports_only_stable_contracts_and_catalog_functions():
    assert set(database.__all__) == ROOT_PUBLIC_NAMES
    assert all(hasattr(database, name) for name in ROOT_PUBLIC_NAMES)


def test_sqlite_package_exports_only_supported_operations():
    assert set(sqlite_database.__all__) == SQLITE_PUBLIC_NAMES
    assert all(hasattr(sqlite_database, name) for name in SQLITE_PUBLIC_NAMES)


def test_sqlite_package_does_not_export_fault_or_publication_internals():
    forbidden = {
        "before_record_insert",
        "_MigrationTransactionAuthorizer",
        "_PublicationFailure",
        "_RemovalResult",
        "_remove_owned_path",
    }

    assert forbidden.isdisjoint(sqlite_database.__all__)
    assert all(not hasattr(sqlite_database, name) for name in forbidden)


def test_production_catalog_stays_at_version_zero():
    root = Path(__file__).resolve().parents[2]
    assert list((root / "migrations" / "sqlite").glob("*.sql")) == []
    assert database.catalog_target_version(()) == 0
