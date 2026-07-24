import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pytest

import infrastructure.database.sqlite.restore as restore_module
from infrastructure.database.contracts import (
    BackupManifest,
    MigrationInfo,
    RestoreVerificationError,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.backup import create_sqlite_backup
from infrastructure.database.sqlite.restore import (
    RestoreResult,
    verify_and_restore_sqlite,
)
from infrastructure.database.sqlite.schema import initialize_schema_metadata


FIXTURE_MIGRATIONS = Path(__file__).parent / "fixtures" / "migrations"


@pytest.fixture
def catalog() -> tuple[MigrationInfo, ...]:
    return load_migration_catalog(FIXTURE_MIGRATIONS)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "source.sqlite3"
    initialize_schema_metadata(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE snapshot_value (value TEXT NOT NULL)")
        connection.execute("INSERT INTO snapshot_value VALUES ('committed')")
    return path


@pytest.fixture
def backup_result(database: Path, tmp_path: Path):
    return create_sqlite_backup(database, tmp_path / "backup.sqlite3", ())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    backup_path: Path,
    manifest_path: Path,
    *,
    schema_version: int = 0,
    catalog_target_version: int = 0,
) -> None:
    payload = BackupManifest(
        format_version=1,
        schema_version=schema_version,
        catalog_target_version=catalog_target_version,
        sha256=_sha256(backup_path),
        size_bytes=backup_path.stat().st_size,
        created_at_utc="2026-07-24T00:00:00Z",
        sqlite_version=sqlite3.sqlite_version,
        integrity_check="ok",
    )
    manifest_path.write_text(
        json.dumps(asdict(payload), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _raw_backup_with_manifest(
    tmp_path: Path,
    setup_sql: str,
    *,
    schema_version: int = 0,
    catalog_target_version: int = 0,
) -> tuple[Path, Path]:
    backup_path = tmp_path / "raw-backup.sqlite3"
    with sqlite3.connect(backup_path) as connection:
        connection.executescript(setup_sql)
    manifest_path = tmp_path / "raw-backup.manifest.json"
    _write_manifest(
        backup_path,
        manifest_path,
        schema_version=schema_version,
        catalog_target_version=catalog_target_version,
    )
    return backup_path, manifest_path


def _assert_no_restore_artifacts(target_path: Path) -> None:
    assert not target_path.exists()
    assert all("restore-staging" not in path.name for path in target_path.parent.iterdir())


def test_restores_verified_backup_to_new_path(
    backup_result,
    tmp_path: Path,
) -> None:
    active = tmp_path / "active.sqlite3"
    initialize_schema_metadata(active)
    target = tmp_path / "restored.sqlite3"

    result = verify_and_restore_sqlite(
        backup_result.backup_path,
        backup_result.manifest_path,
        target,
        active_database_path=active,
        catalog=(),
    )

    assert result == RestoreResult(
        schema_version=0,
        sha256=_sha256(target),
        size_bytes=target.stat().st_size,
    )
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT value FROM snapshot_value").fetchone()[0] == "committed"
    assert _sha256(backup_result.backup_path) == backup_result.manifest.sha256


@pytest.mark.parametrize("alias", [False, True])
def test_restore_never_switches_or_overwrites_active_database(
    backup_result,
    tmp_path: Path,
    alias: bool,
) -> None:
    active = tmp_path / "active.sqlite3"
    initialize_schema_metadata(active)
    original_hash = _sha256(active)
    target = tmp_path / "alias" / ".." / active.name if alias else active

    with pytest.raises(
        RestoreVerificationError,
        match="target must be a new path",
    ) as raised:
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=active,
            catalog=(),
        )

    assert raised.value.__cause__ is None
    assert _sha256(active) == original_hash


def test_existing_target_is_preserved(
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    target.write_bytes(b"existing target")

    with pytest.raises(RestoreVerificationError, match="target must not exist"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert target.read_bytes() == b"existing target"


@pytest.mark.parametrize(
    "case",
    [
        "invalid_json",
        "missing_field",
        "extra_field",
        "wrong_format",
        "format_bool",
        "schema_bool",
        "target_bool",
        "size_bool",
    ],
)
def test_manifest_is_strictly_validated_before_opening_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
    case: str,
) -> None:
    payload = asdict(backup_result.manifest)
    if case == "invalid_json":
        serialized = "{not-json"
    else:
        if case == "missing_field":
            payload.pop("sqlite_version")
        elif case == "extra_field":
            payload["source_path"] = "forbidden"
        elif case == "wrong_format":
            payload["format_version"] = 2
        elif case == "format_bool":
            payload["format_version"] = True
        elif case == "schema_bool":
            payload["schema_version"] = True
        elif case == "target_bool":
            payload["catalog_target_version"] = False
        elif case == "size_bool":
            payload["size_bytes"] = True
        serialized = json.dumps(payload)
    manifest = tmp_path / f"{case}.manifest.json"
    manifest.write_text(serialized, encoding="utf-8")
    opened = False

    def fail_if_opened(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("SQLite must not be opened")

    monkeypatch.setattr(restore_module, "_materialize_sqlite_backup", fail_if_opened)

    with pytest.raises(RestoreVerificationError, match="manifest is invalid"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            manifest,
            tmp_path / "restored.sqlite3",
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert not opened


@pytest.mark.parametrize("field", ["size_bytes", "sha256"])
def test_backup_size_and_hash_are_verified_before_opening_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
    field: str,
) -> None:
    payload = asdict(backup_result.manifest)
    payload[field] = payload[field] + 1 if field == "size_bytes" else "0" * 64
    manifest = tmp_path / f"wrong-{field}.manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    opened = False

    def fail_if_opened(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("SQLite must not be opened")

    monkeypatch.setattr(restore_module, "_materialize_sqlite_backup", fail_if_opened)

    with pytest.raises(RestoreVerificationError, match="backup verification failed"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            manifest,
            tmp_path / "restored.sqlite3",
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert not opened


def test_pending_backup_is_restorable_when_manifest_and_catalog_match(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    backup = create_sqlite_backup(database, tmp_path / "pending.sqlite3", catalog)
    target = tmp_path / "restored.sqlite3"

    result = verify_and_restore_sqlite(
        backup.backup_path,
        backup.manifest_path,
        target,
        active_database_path=database,
        catalog=catalog,
    )

    assert result.schema_version == 0
    assert target.is_file()


@pytest.mark.parametrize("mismatch", ["schema", "catalog"])
def test_manifest_schema_metadata_must_match_restored_database(
    backup_result,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
    mismatch: str,
) -> None:
    payload = asdict(backup_result.manifest)
    if mismatch == "schema":
        payload["schema_version"] = 1
        restore_catalog: tuple[MigrationInfo, ...] = ()
    else:
        payload["catalog_target_version"] = 2
        restore_catalog = ()
    manifest = tmp_path / f"{mismatch}.manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    target = tmp_path / "restored.sqlite3"

    with pytest.raises(RestoreVerificationError, match="schema validation failed"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            manifest,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=restore_catalog,
        )

    _assert_no_restore_artifacts(target)


def test_uninitialized_backup_is_rejected(tmp_path: Path) -> None:
    backup, manifest = _raw_backup_with_manifest(
        tmp_path,
        "CREATE TABLE value (id INTEGER PRIMARY KEY);",
    )
    target = tmp_path / "restored.sqlite3"

    with pytest.raises(RestoreVerificationError, match="schema validation failed"):
        verify_and_restore_sqlite(
            backup,
            manifest,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    _assert_no_restore_artifacts(target)


@pytest.mark.parametrize("state", ["too_new", "tampered"])
def test_unknown_or_drifted_schema_is_rejected(
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
    state: str,
) -> None:
    first = catalog[0]
    version = 3 if state == "too_new" else 1
    name = "future" if state == "too_new" else first.name
    checksum = "future" if state == "too_new" else "0" * 64
    backup, manifest = _raw_backup_with_manifest(
        tmp_path,
        "CREATE TABLE _schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL,"
        "applied_at_utc TEXT NOT NULL, execution_ms INTEGER NOT NULL);"
        "INSERT INTO _schema_migrations VALUES "
        f"({version}, '{name}', '{checksum}', '2026-07-24T00:00:00Z', 1);",
        schema_version=version,
        catalog_target_version=2,
    )
    target = tmp_path / "restored.sqlite3"

    with pytest.raises(RestoreVerificationError, match="schema validation failed"):
        verify_and_restore_sqlite(
            backup,
            manifest,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=catalog,
        )

    _assert_no_restore_artifacts(target)


def test_foreign_key_orphan_is_rejected(tmp_path: Path) -> None:
    backup, manifest = _raw_backup_with_manifest(
        tmp_path,
        "CREATE TABLE _schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL,"
        "applied_at_utc TEXT NOT NULL, execution_ms INTEGER NOT NULL);"
        "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
        "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));"
        "INSERT INTO child VALUES (999);",
    )
    target = tmp_path / "restored.sqlite3"

    with pytest.raises(RestoreVerificationError, match="database validation failed"):
        verify_and_restore_sqlite(
            backup,
            manifest,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    _assert_no_restore_artifacts(target)


def test_corrupt_backup_is_rejected_after_manifest_verification(tmp_path: Path) -> None:
    backup = tmp_path / "corrupt.sqlite3"
    backup.write_bytes(b"not a sqlite database")
    manifest = tmp_path / "corrupt.manifest.json"
    _write_manifest(backup, manifest)
    target = tmp_path / "restored.sqlite3"

    with pytest.raises(RestoreVerificationError, match="restore verification failed"):
        verify_and_restore_sqlite(
            backup,
            manifest,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    _assert_no_restore_artifacts(target)


def test_injected_target_verification_failure_cleans_only_owned_temporary_state(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    sentinel = tmp_path / "foreign.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    backup_hash = _sha256(backup_result.backup_path)
    manifest_hash = _sha256(backup_result.manifest_path)

    def fail_validation(*args, **kwargs):
        raise RestoreVerificationError("injected validation failure")

    monkeypatch.setattr(restore_module, "_validate_restored_database", fail_validation)

    with pytest.raises(RestoreVerificationError, match="injected validation failure"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    _assert_no_restore_artifacts(target)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert _sha256(backup_result.backup_path) == backup_hash
    assert _sha256(backup_result.manifest_path) == manifest_hash


def test_backup_api_failure_closes_source_and_destination_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    real_open = restore_module._open_backup_read_only
    real_connect = restore_module.sqlite3.connect
    source_closed = False
    destination_closed = False

    class FailingSource:
        def __init__(self, connection):
            self.connection = connection

        def backup(self, destination):
            raise sqlite3.OperationalError("sensitive backup API failure")

        def close(self):
            nonlocal source_closed
            source_closed = True
            self.connection.close()

    class ObservedDestination:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            nonlocal destination_closed
            destination_closed = True
            self.connection.close()

    def observed_open(path, **kwargs):
        return FailingSource(real_open(path, **kwargs))

    def observed_connect(*args, **kwargs):
        return ObservedDestination(real_connect(*args, **kwargs))

    monkeypatch.setattr(restore_module, "_open_backup_read_only", observed_open)
    monkeypatch.setattr(restore_module, "_open_restore_destination", observed_connect)

    with pytest.raises(RestoreVerificationError) as raised:
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert "sensitive" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert source_closed
    assert destination_closed
    _assert_no_restore_artifacts(target)


def test_publication_failure_cleans_owned_temp_and_preserves_foreign_files(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    sentinel = tmp_path / "foreign.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    real_link = os.link

    def fail_target_link(source, destination):
        if Path(destination) == target:
            raise OSError("sensitive link failure")
        real_link(source, destination)

    monkeypatch.setattr(restore_module.os, "link", fail_target_link)

    with pytest.raises(RestoreVerificationError, match="publication failed") as raised:
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert "sensitive" not in str(raised.value)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    _assert_no_restore_artifacts(target)


def test_link_that_creates_target_then_raises_is_compensated(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    real_link = os.link

    def link_then_raise(source, destination):
        real_link(source, destination)
        if Path(destination) == target:
            raise OSError("injected post-link ambiguity")

    monkeypatch.setattr(restore_module.os, "link", link_then_raise)

    with pytest.raises(RestoreVerificationError, match="publication failed"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    _assert_no_restore_artifacts(target)


def test_replaced_target_is_never_deleted_or_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    replacement = b"foreign replacement"
    real_link = os.link

    def link_then_replace(source, destination):
        real_link(source, destination)
        if Path(destination) == target:
            Path(destination).unlink()
            Path(destination).write_bytes(replacement)

    monkeypatch.setattr(restore_module.os, "link", link_then_replace)

    with pytest.raises(RestoreVerificationError, match="publication failed"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert target.read_bytes() == replacement
    assert all("restore-staging" not in path.name for path in tmp_path.iterdir())


def test_final_identity_stat_failure_compensates_published_target(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    real_identity = restore_module._path_identity
    target_stat_calls = 0

    def fail_final_target_identity(path):
        nonlocal target_stat_calls
        if path == target:
            target_stat_calls += 1
            if target_stat_calls == 1:
                raise OSError("injected final identity failure")
        return real_identity(path)

    monkeypatch.setattr(
        restore_module,
        "_path_identity",
        fail_final_target_identity,
    )

    with pytest.raises(RestoreVerificationError):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert target_stat_calls >= 2
    _assert_no_restore_artifacts(target)


def test_backup_connection_uses_immutable_read_only_uri(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
) -> None:
    real_connect = restore_module.sqlite3.connect
    calls: list[tuple[object, dict[str, object]]] = []

    def record_connect(database, **kwargs):
        calls.append((database, kwargs))
        return real_connect(database, **kwargs)

    monkeypatch.setattr(restore_module.sqlite3, "connect", record_connect)

    connection = restore_module._open_backup_read_only(
        backup_result.backup_path,
        options=restore_module.SqliteConnectionOptions(),
    )
    connection.close()

    assert len(calls) == 1
    database_uri, options = calls[0]
    assert "mode=ro" in str(database_uri)
    assert "immutable=1" in str(database_uri)
    assert options["uri"] is True


def test_backup_replacement_between_precheck_and_open_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    real_open = restore_module._open_backup_read_only
    replaced = False

    def replace_then_open(path, **kwargs):
        nonlocal replaced
        with path.open("ab") as stream:
            stream.write(b"replacement bytes")
        replaced = True
        return real_open(path, **kwargs)

    monkeypatch.setattr(
        restore_module,
        "_open_backup_read_only",
        replace_then_open,
    )

    with pytest.raises(
        RestoreVerificationError,
        match="backup verification failed",
    ):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert replaced
    _assert_no_restore_artifacts(target)


def test_replaced_temporary_target_is_not_deleted_during_failure_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    replacement = b"foreign temporary replacement"
    replaced_path: Path | None = None

    def replace_then_fail(path, *args, **kwargs):
        nonlocal replaced_path
        path.unlink()
        path.write_bytes(replacement)
        replaced_path = path
        raise RestoreVerificationError("injected validation failure")

    monkeypatch.setattr(
        restore_module,
        "_validate_restored_database",
        replace_then_fail,
    )

    with pytest.raises(
        RestoreVerificationError,
        match="temporary file cleanup failed",
    ):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement
    assert not target.exists()


def test_source_path_ab_swap_cannot_publish_replacement_business_data(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip(
            "Windows denies renaming an opened SQLite source; POSIX CI exercises the swap"
        )
    target = tmp_path / "restored.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    initialize_schema_metadata(replacement)
    with sqlite3.connect(replacement) as connection:
        connection.execute("CREATE TABLE snapshot_value (value TEXT NOT NULL)")
        connection.execute("INSERT INTO snapshot_value VALUES ('replacement')")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")

    original_hash = _sha256(backup_result.backup_path)
    held_original = tmp_path / "held-original.sqlite3"
    real_open = restore_module._open_backup_read_only
    swapped = False

    def swap_original_around_open(path, **kwargs):
        nonlocal swapped
        backup_result.backup_path.replace(held_original)
        replacement.replace(backup_result.backup_path)
        try:
            connection = real_open(path, **kwargs)
            connection.execute("SELECT value FROM snapshot_value").fetchone()
        finally:
            backup_result.backup_path.replace(replacement)
            held_original.replace(backup_result.backup_path)
        swapped = True
        return connection

    monkeypatch.setattr(
        restore_module,
        "_open_backup_read_only",
        swap_original_around_open,
    )

    verify_and_restore_sqlite(
        backup_result.backup_path,
        backup_result.manifest_path,
        target,
        active_database_path=tmp_path / "active.sqlite3",
        catalog=(),
    )

    assert swapped
    assert _sha256(backup_result.backup_path) == original_hash
    with sqlite3.connect(target) as connection:
        assert connection.execute(
            "SELECT value FROM snapshot_value"
        ).fetchone()[0] == "committed"


def _create_symlink_or_skip(link: Path, destination: Path) -> None:
    try:
        link.symlink_to(destination)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links unavailable: {type(error).__name__}")


def test_broken_target_symlink_is_refused_before_resolution(
    backup_result,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.sqlite3"
    target = tmp_path / "broken-target.sqlite3"
    _create_symlink_or_skip(target, missing)

    with pytest.raises(RestoreVerificationError, match="target must not exist"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert target.is_symlink()
    assert not missing.exists()


def test_target_symlink_to_existing_file_is_preserved(
    backup_result,
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.sqlite3"
    existing.write_bytes(b"preserve existing")
    target = tmp_path / "existing-target.sqlite3"
    _create_symlink_or_skip(target, existing)

    with pytest.raises(RestoreVerificationError, match="target must not exist"):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert target.is_symlink()
    assert existing.read_bytes() == b"preserve existing"


def test_target_symlink_to_active_database_keeps_active_alias_check(
    backup_result,
    tmp_path: Path,
) -> None:
    active = tmp_path / "active.sqlite3"
    initialize_schema_metadata(active)
    active_hash = _sha256(active)
    target = tmp_path / "active-target.sqlite3"
    _create_symlink_or_skip(target, active)

    with pytest.raises(
        RestoreVerificationError,
        match="target must be a new path",
    ):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=active,
            catalog=(),
        )

    assert target.is_symlink()
    assert _sha256(active) == active_hash


def test_plain_new_target_still_restores_after_raw_entry_check(
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "plain-new.sqlite3"

    verify_and_restore_sqlite(
        backup_result.backup_path,
        backup_result.manifest_path,
        target,
        active_database_path=tmp_path / "active.sqlite3",
        catalog=(),
    )

    assert target.is_file()
    assert not target.is_symlink()


def test_source_link_that_creates_then_raises_is_cleaned(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    real_link = os.link

    def source_link_then_raise(source, destination):
        real_link(source, destination)
        if Path(destination).name == "verified-source.sqlite3":
            raise OSError("injected source link ambiguity")

    monkeypatch.setattr(restore_module.os, "link", source_link_then_raise)

    with pytest.raises(
        RestoreVerificationError,
        match="backup source binding failed",
    ) as raised:
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert raised.value.__cause__ is None
    _assert_no_restore_artifacts(target)


def test_source_binding_stat_failure_cleans_private_link(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    real_identity = restore_module._path_identity
    failed = False

    def fail_bound_identity_once(path):
        nonlocal failed
        if not failed and path.name == "verified-source.sqlite3":
            failed = True
            raise OSError("injected bound identity failure")
        return real_identity(path)

    monkeypatch.setattr(restore_module, "_path_identity", fail_bound_identity_once)

    with pytest.raises(
        RestoreVerificationError,
        match="backup source binding failed",
    ):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert failed
    _assert_no_restore_artifacts(target)


def test_replaced_bound_source_is_not_deleted_during_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    replacement = b"foreign bound replacement"
    replaced_path: Path | None = None
    real_bind = restore_module._bind_backup_source

    def bind_then_replace(*args, **kwargs):
        nonlocal replaced_path
        path, identity = real_bind(*args, **kwargs)
        path.unlink()
        path.write_bytes(replacement)
        replaced_path = path
        return path, identity

    monkeypatch.setattr(restore_module, "_bind_backup_source", bind_then_replace)

    with pytest.raises(
        RestoreVerificationError,
        match="temporary file cleanup failed",
    ):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement
    assert not target.exists()


def test_source_and_target_use_separate_local_staging_directories(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    backup_dir = tmp_path / "backup-operations"
    target_dir = tmp_path / "target-operations"
    backup_dir.mkdir()
    target_dir.mkdir()
    backup = create_sqlite_backup(database, backup_dir / "backup.sqlite3", ())
    target = target_dir / "restored.sqlite3"
    real_create = restore_module._create_staging_directory
    observed: dict[str, tuple[Path, Path]] = {}

    def observe_staging(parent, final_name, *, purpose):
        path, identity = real_create(parent, final_name, purpose=purpose)
        observed[purpose] = (path, parent)
        return path, identity

    monkeypatch.setattr(
        restore_module,
        "_create_staging_directory",
        observe_staging,
    )

    verify_and_restore_sqlite(
        backup.backup_path,
        backup.manifest_path,
        target,
        active_database_path=database,
        catalog=(),
    )

    source_staging, source_parent = observed["source"]
    target_staging, target_parent = observed["target"]
    assert source_parent == backup_dir.resolve()
    assert target_parent == target_dir.resolve()
    assert source_staging != target_staging
    assert not source_staging.exists()
    assert not target_staging.exists()

def test_source_link_error_never_deletes_non_owned_replacement(
    monkeypatch: pytest.MonkeyPatch,
    backup_result,
    tmp_path: Path,
) -> None:
    target = tmp_path / "restored.sqlite3"
    replacement = b"foreign link replacement"
    replaced_path: Path | None = None
    real_link = os.link

    def replace_destination_then_raise(source, destination):
        nonlocal replaced_path
        if Path(destination).name == "verified-source.sqlite3":
            Path(destination).write_bytes(replacement)
            replaced_path = Path(destination)
            raise OSError("injected source replacement")
        real_link(source, destination)

    monkeypatch.setattr(
        restore_module.os,
        "link",
        replace_destination_then_raise,
    )

    with pytest.raises(
        RestoreVerificationError,
        match="temporary file cleanup failed",
    ):
        verify_and_restore_sqlite(
            backup_result.backup_path,
            backup_result.manifest_path,
            target,
            active_database_path=tmp_path / "active.sqlite3",
            catalog=(),
        )

    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement
    assert not target.exists()



def test_restore_documents_trusted_target_directory_boundary() -> None:
    module_documentation = restore_module.__doc__ or ""
    api_documentation = verify_and_restore_sqlite.__doc__ or ""

    assert "trusted local operations directory" in module_documentation
    assert "same-privilege malicious path replacement" in module_documentation
    assert "backup_path.parent" in api_documentation
    assert "target_path.parent" in api_documentation
