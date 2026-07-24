from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class SchemaState(str, Enum):
    CURRENT = "current"
    PENDING = "pending"
    UNINITIALIZED = "uninitialized"
    TOO_NEW = "too_new"
    TAMPERED = "tampered"
    INVALID_CATALOG = "invalid_catalog"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class MigrationInfo:
    version: int
    name: str
    checksum_sha256: str
    path: Path


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    checksum_sha256: str
    applied_at_utc: str
    execution_ms: int


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    state: SchemaState
    current_version: int
    target_version: int
    pending_versions: tuple[int, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class BackupManifest:
    format_version: int
    schema_version: int
    catalog_target_version: int
    sha256: str
    size_bytes: int
    created_at_utc: str
    sqlite_version: str
    integrity_check: str


class DatabaseError(RuntimeError):
    code = "DB.OPERATION_FAILED"

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class DatabaseConfigurationError(DatabaseError):
    code = "DB.CONFIGURATION"


class MigrationCatalogError(DatabaseError):
    code = "DB.MIGRATION_CATALOG"


class MigrationChecksumError(DatabaseError):
    code = "DB.MIGRATION_CHECKSUM"


class SchemaTooNewError(DatabaseError):
    code = "DB.SCHEMA_TOO_NEW"


class MigrationExecutionError(DatabaseError):
    code = "DB.MIGRATION_EXECUTION"


class DatabaseBusyError(DatabaseError):
    code = "DB.BUSY"


class DatabaseIntegrityError(DatabaseError):
    code = "DB.INTEGRITY"


class BackupError(DatabaseError):
    code = "DB.BACKUP"


class RestoreVerificationError(DatabaseError):
    code = "DB.RESTORE_VERIFICATION"


class UnitOfWorkStateError(DatabaseError):
    code = "DB.UOW_STATE"


class UnsupportedDatabaseProviderError(DatabaseError):
    code = "DB.UNSUPPORTED_PROVIDER"
