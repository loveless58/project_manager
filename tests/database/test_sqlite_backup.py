import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import infrastructure.database.sqlite.backup as backup_module
from infrastructure.database.contracts import (
    BackupError,
    MigrationInfo,
    SchemaState,
    SchemaStatus,
)
from infrastructure.database.migration_catalog import load_migration_catalog
from infrastructure.database.sqlite.backup import (
    create_sqlite_backup,
    load_backup_manifest,
    verify_backup_artifacts,
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


def _manifest_path(backup_path: Path) -> Path:
    return Path(f"{backup_path}.manifest.json")


def _insert_applied(
    database: Path,
    version: int,
    name: str,
    checksum: str,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO _schema_migrations "
            "(version, name, checksum_sha256, applied_at_utc, execution_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, name, checksum, "2026-07-24T00:00:00Z", 1),
        )


def _assert_no_backup_artifacts(output_path: Path) -> None:
    assert not output_path.exists()
    assert not _manifest_path(output_path).exists()
    assert list(output_path.parent.iterdir()) == []


def test_backup_uses_wal_consistent_sqlite_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite3"
    initialize_schema_metadata(database)
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE wal_value (value TEXT NOT NULL)")
        writer.execute("INSERT INTO wal_value VALUES ('in-wal')")
        writer.commit()
        assert Path(f"{database}-wal").is_file()

        result = create_sqlite_backup(database, tmp_path / "backup.sqlite3", ())
    finally:
        writer.close()

    with sqlite3.connect(result.backup_path) as connection:
        assert connection.execute("SELECT value FROM wal_value").fetchone()[0] == "in-wal"


def test_missing_source_is_refused_without_creating_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(tmp_path / "missing.sqlite3", output, ())

    _assert_no_backup_artifacts(output)


def test_pending_schema_is_allowed(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    result = create_sqlite_backup(
        database,
        tmp_path / "pending-backup.sqlite3",
        catalog,
    )

    assert result.manifest.schema_version == 0
    assert result.manifest.catalog_target_version == 2


def test_too_new_schema_is_refused(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    _insert_applied(database, 3, "future", "future-checksum")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, catalog)

    _assert_no_backup_artifacts(output)


def test_tampered_schema_is_refused(
    database: Path,
    catalog: tuple[MigrationInfo, ...],
    tmp_path: Path,
) -> None:
    first = catalog[0]
    _insert_applied(database, first.version, first.name, "0" * 64)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, catalog)

    _assert_no_backup_artifacts(output)


def test_invalid_catalog_status_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        backup_module,
        "inspect_schema",
        lambda *args, **kwargs: SchemaStatus(
            state=SchemaState.INVALID_CATALOG,
            current_version=0,
            target_version=0,
            error_code="DB.MIGRATION_CATALOG",
        ),
    )
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_corrupt_database_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a sqlite database")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_foreign_key_failure_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "source.sqlite3"
    initialize_schema_metadata(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL,"
            "FOREIGN KEY (parent_id) REFERENCES parent(id));"
            "INSERT INTO child VALUES (1, 999);"
        )
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_preexisting_backup_is_never_overwritten(
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    output.write_bytes(b"existing backup")

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert output.read_bytes() == b"existing backup"
    assert not _manifest_path(output).exists()
    assert [path.name for path in output_dir.iterdir()] == ["backup.sqlite3"]


def test_preexisting_manifest_is_preserved_and_new_backup_is_compensated(
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    manifest_path = _manifest_path(output)
    manifest_path.write_text("existing manifest", encoding="utf-8")

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert not output.exists()
    assert manifest_path.read_text(encoding="utf-8") == "existing manifest"
    assert [path.name for path in output_dir.iterdir()] == [
        "backup.sqlite3.manifest.json",
    ]


def test_backup_manifest_fields_hash_size_and_verification(
    backup_result,
) -> None:
    backup_payload = backup_result.backup_path.read_bytes()
    manifest = backup_result.manifest

    assert manifest.format_version == 1
    assert manifest.schema_version == 0
    assert manifest.catalog_target_version == 0
    assert manifest.sha256 == hashlib.sha256(backup_payload).hexdigest()
    assert manifest.size_bytes == len(backup_payload)
    assert manifest.created_at_utc.endswith("Z")
    assert manifest.sqlite_version == sqlite3.sqlite_version
    assert manifest.integrity_check == "ok"
    assert load_backup_manifest(backup_result.manifest_path) == manifest
    assert verify_backup_artifacts(
        backup_result.backup_path,
        backup_result.manifest_path,
    ) == manifest
    assert backup_result.manifest_path.read_bytes().endswith(b"\n")


def test_backup_manifest_contains_no_physical_source_identity(
    backup_result,
    database: Path,
) -> None:
    payload = backup_result.manifest_path.read_text(encoding="utf-8")
    assert str(database) not in payload
    assert set(json.loads(payload)) == {
        "format_version",
        "schema_version",
        "catalog_target_version",
        "sha256",
        "size_bytes",
        "created_at_utc",
        "sqlite_version",
        "integrity_check",
    }


def test_verify_backup_artifacts_rejects_changed_backup(backup_result) -> None:
    with backup_result.backup_path.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(BackupError):
        verify_backup_artifacts(
            backup_result.backup_path,
            backup_result.manifest_path,
        )


def test_load_backup_manifest_rejects_invalid_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "invalid.manifest.json"
    manifest_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(BackupError):
        load_backup_manifest(manifest_path)


def test_injected_second_publication_failure_cleans_owned_files(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_link = os.link
    calls = 0

    def fail_manifest_link(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected publication failure")
        real_link(source, destination)

    monkeypatch.setattr(backup_module.os, "link", fail_manifest_link)

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(database) not in str(raised.value)
    assert calls == 2
    _assert_no_backup_artifacts(output)


def test_source_foreign_keys_are_checked_before_backup_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "source.sqlite3"
    initialize_schema_metadata(database)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL,"
            "FOREIGN KEY (parent_id) REFERENCES parent(id));"
            "INSERT INTO child VALUES (1, 999);"
        )
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    original_copy = backup_module._copy_sqlite_snapshot
    copy_called = False

    def repair_source_then_copy(database_path, destination_path, **kwargs):
        nonlocal copy_called
        copy_called = True
        with sqlite3.connect(database_path) as connection:
            connection.execute("DELETE FROM child")
        original_copy(database_path, destination_path, **kwargs)

    monkeypatch.setattr(
        backup_module,
        "_copy_sqlite_snapshot",
        repair_source_then_copy,
    )

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert not copy_called
    _assert_no_backup_artifacts(output)


def test_replaced_backup_is_not_deleted_when_manifest_publication_fails(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    replacement = b"replacement owned elsewhere"
    real_link = os.link
    calls = 0

    def replace_after_first_link(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            real_link(source, destination)
            Path(destination).unlink()
            Path(destination).write_bytes(replacement)
            return
        raise OSError("injected manifest link failure")

    monkeypatch.setattr(backup_module.os, "link", replace_after_first_link)

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert output.read_bytes() == replacement
    assert not _manifest_path(output).exists()
    assert [path.name for path in output_dir.iterdir()] == ["backup.sqlite3"]


def test_link_that_creates_backup_then_raises_is_safely_compensated(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_link = os.link

    def link_then_raise(source, destination):
        real_link(source, destination)
        raise OSError("injected post-link failure")

    monkeypatch.setattr(backup_module.os, "link", link_then_raise)

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    _assert_no_backup_artifacts(output)


def test_first_temp_unlink_failure_is_retried_without_reporting_success(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_unlink = Path.unlink
    temp_unlink_attempts = 0

    def fail_first_backup_temp_unlink(path, *args, **kwargs):
        nonlocal temp_unlink_attempts
        if (
            path.name.startswith(".backup.sqlite3.")
            and ".manifest.json." not in path.name
            and path.suffix == ".tmp"
        ):
            temp_unlink_attempts += 1
            if temp_unlink_attempts == 1:
                raise OSError("injected temp unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_backup_temp_unlink)

    with pytest.raises(BackupError, match="temporary file cleanup"):
        create_sqlite_backup(database, output, ())

    assert temp_unlink_attempts == 2
    _assert_no_backup_artifacts(output)


def test_persistent_temp_unlink_failure_never_reports_success_or_deletes_foreign_file(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    sentinel = output_dir / "foreign.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    output = output_dir / "backup.sqlite3"
    real_unlink = Path.unlink
    temp_unlink_attempts = 0

    def fail_backup_temp_unlink(path, *args, **kwargs):
        nonlocal temp_unlink_attempts
        if (
            path.name.startswith(".backup.sqlite3.")
            and ".manifest.json." not in path.name
            and path.suffix == ".tmp"
        ):
            temp_unlink_attempts += 1
            raise OSError("persistent temp unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_backup_temp_unlink)

    with pytest.raises(BackupError, match="temporary file cleanup"):
        create_sqlite_backup(database, output, ())

    assert temp_unlink_attempts >= 2
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not output.exists()
    assert not _manifest_path(output).exists()
    remaining_temps = [
        path for path in output_dir.rglob("*.tmp")
    ]
    assert len(remaining_temps) == 1


def test_failed_backup_compensation_is_reported_without_deleting_backup(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_link = os.link
    real_unlink = Path.unlink
    link_calls = 0

    def fail_manifest_link(source, destination):
        nonlocal link_calls
        link_calls += 1
        if link_calls == 2:
            raise OSError("injected manifest link failure")
        real_link(source, destination)

    def fail_backup_compensation(path, *args, **kwargs):
        if path == output:
            raise OSError("injected compensation failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(backup_module.os, "link", fail_manifest_link)
    monkeypatch.setattr(Path, "unlink", fail_backup_compensation)

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(raised.value) == "backup publication cleanup failed"
    assert output.is_file()
    assert not _manifest_path(output).exists()
    assert all(path.suffix != ".tmp" for path in output_dir.iterdir())


@pytest.mark.parametrize(
    "case",
    [
        "missing_field",
        "extra_field",
        "format_version",
        "schema_version_bool",
        "target_version_bool",
        "size_bool",
        "negative_schema",
        "negative_target",
        "negative_size",
        "short_sha",
        "uppercase_sha",
        "invalid_utc",
        "integrity_not_ok",
    ],
)
def test_load_backup_manifest_enforces_strict_schema_and_types(
    backup_result,
    tmp_path: Path,
    case: str,
) -> None:
    payload = json.loads(
        backup_result.manifest_path.read_text(encoding="utf-8")
    )
    if case == "missing_field":
        payload.pop("sqlite_version")
    elif case == "extra_field":
        payload["source_path"] = "forbidden"
    elif case == "format_version":
        payload["format_version"] = 2
    elif case == "schema_version_bool":
        payload["schema_version"] = True
    elif case == "target_version_bool":
        payload["catalog_target_version"] = False
    elif case == "size_bool":
        payload["size_bytes"] = True
    elif case == "negative_schema":
        payload["schema_version"] = -1
    elif case == "negative_target":
        payload["catalog_target_version"] = -1
    elif case == "negative_size":
        payload["size_bytes"] = -1
    elif case == "short_sha":
        payload["sha256"] = "0" * 63
    elif case == "uppercase_sha":
        payload["sha256"] = "A" * 64
    elif case == "invalid_utc":
        payload["created_at_utc"] = "2026-07-25T12:00:00+00:00"
    elif case == "integrity_not_ok":
        payload["integrity_check"] = "failed"

    manifest_path = tmp_path / f"{case}.manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BackupError, match="manifest is invalid"):
        load_backup_manifest(manifest_path)


def test_mkstemp_close_error_cleans_created_path_and_is_safely_mapped(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_close = os.close
    close_calls = 0

    def close_then_raise(descriptor):
        nonlocal close_calls
        close_calls += 1
        real_close(descriptor)
        raise OSError("injected close failure")

    monkeypatch.setattr(backup_module.os, "close", close_then_raise)

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(raised.value) == "database backup failed"
    assert close_calls == 1
    _assert_no_backup_artifacts(output)


def test_replaced_temp_is_not_deleted_during_final_cleanup_retry(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    replacement = b"foreign temp replacement"
    real_unlink = Path.unlink
    replaced_path: Path | None = None

    def replace_backup_temp_while_unlink_fails(path, *args, **kwargs):
        nonlocal replaced_path
        if (
            replaced_path is None
            and path.name.startswith(".backup.sqlite3.")
            and ".manifest.json." not in path.name
            and path.suffix == ".tmp"
        ):
            real_unlink(path, *args, **kwargs)
            path.write_bytes(replacement)
            replaced_path = path
            raise OSError("injected unlink race")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", replace_backup_temp_while_unlink_fails)

    with pytest.raises(BackupError, match="temporary file cleanup"):
        create_sqlite_backup(database, output, ())

    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement
    assert not output.exists()
    assert not _manifest_path(output).exists()


def test_temp_cleanup_reports_failed_backup_compensation(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_unlink = Path.unlink
    temp_failed = False

    def fail_temp_once_and_backup_compensation(path, *args, **kwargs):
        nonlocal temp_failed
        if (
            not temp_failed
            and path.name.startswith(".backup.sqlite3.")
            and ".manifest.json." not in path.name
            and path.suffix == ".tmp"
        ):
            temp_failed = True
            raise OSError("injected temp cleanup failure")
        if path == output:
            raise OSError("injected backup compensation failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        Path,
        "unlink",
        fail_temp_once_and_backup_compensation,
    )

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(raised.value) == "backup publication cleanup failed"
    assert output.is_file()
    assert not _manifest_path(output).exists()
    assert all(path.suffix != ".tmp" for path in output_dir.iterdir())


def test_manifest_temp_replacement_compensates_backup_without_deleting_replacement(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    replacement = b"foreign manifest temp"
    replaced_path: Path | None = None
    original_write = backup_module._write_manifest

    def write_then_replace(path, manifest):
        nonlocal replaced_path
        original_write(path, manifest)
        path.unlink()
        path.write_bytes(replacement)
        replaced_path = path

    monkeypatch.setattr(backup_module, "_write_manifest", write_then_replace)

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert not output.exists()
    assert not _manifest_path(output).exists()
    assert replaced_path is not None
    assert replaced_path.read_bytes() == replacement


def test_manifest_temp_stat_failure_compensates_backup_and_cleans_staging(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_stat = Path.stat
    failed = False

    def fail_manifest_temp_stat_once(path, *args, **kwargs):
        nonlocal failed
        if (
            not failed
            and ".manifest.json." in path.name
            and path.suffix == ".tmp"
        ):
            failed = True
            raise OSError("injected manifest temp stat failure")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_manifest_temp_stat_once)

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert failed
    _assert_no_backup_artifacts(output)


def test_backup_final_replacement_before_manifest_link_never_returns_success(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    replacement = b"foreign backup final"
    real_link = os.link
    link_calls = 0

    def replace_backup_after_first_link(source, destination):
        nonlocal link_calls
        link_calls += 1
        real_link(source, destination)
        if link_calls == 1:
            Path(destination).unlink()
            Path(destination).write_bytes(replacement)

    monkeypatch.setattr(
        backup_module.os,
        "link",
        replace_backup_after_first_link,
    )

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert output.read_bytes() == replacement
    assert not _manifest_path(output).exists()
    assert all(not path.is_dir() for path in output_dir.iterdir())


def test_manifest_final_replacement_never_returns_success_or_deletes_replacement(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    manifest_path = _manifest_path(output)
    replacement = "foreign manifest final"
    real_link = os.link
    link_calls = 0

    def replace_manifest_after_second_link(source, destination):
        nonlocal link_calls
        link_calls += 1
        real_link(source, destination)
        if link_calls == 2:
            Path(destination).unlink()
            Path(destination).write_text(replacement, encoding="utf-8")

    monkeypatch.setattr(
        backup_module.os,
        "link",
        replace_manifest_after_second_link,
    )

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert not output.exists()
    assert manifest_path.read_text(encoding="utf-8") == replacement
    assert all(not path.is_dir() for path in output_dir.iterdir())


def test_backup_uses_private_staging_directory_and_removes_it_on_success(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    original_copy = backup_module._copy_sqlite_snapshot
    real_chmod = Path.chmod
    staging_path: Path | None = None
    chmod_calls: list[tuple[Path, int]] = []

    def record_chmod(path, mode, *args, **kwargs):
        chmod_calls.append((path, mode))
        return real_chmod(path, mode, *args, **kwargs)

    def observe_staging(database_path, destination_path, **kwargs):
        nonlocal staging_path
        staging_path = destination_path.parent
        assert staging_path.parent == output_dir
        assert staging_path != output_dir
        original_copy(database_path, destination_path, **kwargs)

    monkeypatch.setattr(Path, "chmod", record_chmod)
    monkeypatch.setattr(
        backup_module,
        "_copy_sqlite_snapshot",
        observe_staging,
    )

    result = create_sqlite_backup(database, output, ())

    assert staging_path is not None
    assert (staging_path, 0o700) in chmod_calls
    assert not staging_path.exists()
    assert set(output_dir.iterdir()) == {
        result.backup_path,
        result.manifest_path,
    }


def test_private_staging_directory_is_removed_after_publication_failure(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    original_copy = backup_module._copy_sqlite_snapshot
    staging_path: Path | None = None

    def observe_staging(database_path, destination_path, **kwargs):
        nonlocal staging_path
        staging_path = destination_path.parent
        original_copy(database_path, destination_path, **kwargs)

    monkeypatch.setattr(
        backup_module,
        "_copy_sqlite_snapshot",
        observe_staging,
    )
    monkeypatch.setattr(
        backup_module.os,
        "link",
        lambda *args: (_ for _ in ()).throw(OSError("injected link failure")),
    )

    with pytest.raises(BackupError):
        create_sqlite_backup(database, output, ())

    assert staging_path is not None
    assert not staging_path.exists()
    _assert_no_backup_artifacts(output)


def test_staging_cleanup_never_deletes_replacement_directory(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_rmdir = Path.rmdir
    replacement_dir: Path | None = None
    marker_name = "foreign-marker.txt"

    def replace_staging_while_rmdir_fails(path):
        nonlocal replacement_dir
        if replacement_dir is None and path != output_dir:
            real_rmdir(path)
            path.mkdir()
            (path / marker_name).write_text("preserve", encoding="utf-8")
            replacement_dir = path
            raise OSError("injected staging rmdir race")
        return real_rmdir(path)

    monkeypatch.setattr(Path, "rmdir", replace_staging_while_rmdir_fails)

    with pytest.raises(BackupError, match="temporary file cleanup"):
        create_sqlite_backup(database, output, ())

    assert replacement_dir is not None
    assert (replacement_dir / marker_name).read_text(encoding="utf-8") == "preserve"
    assert not output.exists()
    assert not _manifest_path(output).exists()


def test_fstat_failure_closes_fd_and_cleans_private_staging(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_close = os.close
    fstat_calls = 0
    close_calls = 0

    def fail_first_fstat(descriptor):
        nonlocal fstat_calls
        fstat_calls += 1
        raise OSError("injected fstat failure")

    def record_close(descriptor):
        nonlocal close_calls
        close_calls += 1
        real_close(descriptor)

    monkeypatch.setattr(backup_module.os, "fstat", fail_first_fstat)
    monkeypatch.setattr(backup_module.os, "close", record_close)

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(raised.value) == "database backup failed"
    assert fstat_calls == 1
    assert close_calls == 1
    _assert_no_backup_artifacts(output)


def test_backup_documents_trusted_output_directory_boundary() -> None:
    module_documentation = backup_module.__doc__ or ""
    api_documentation = create_sqlite_backup.__doc__ or ""

    assert "trusted local operations directory" in module_documentation
    assert "cooperating processes" in module_documentation
    assert "same-privilege malicious path replacement" in module_documentation
    assert "output_path.parent" in api_documentation


def test_staging_identity_failure_removes_only_exact_empty_directory(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_mkdtemp = backup_module.tempfile.mkdtemp
    real_rmdir = Path.rmdir
    created_staging: Path | None = None
    rmdir_calls: list[Path] = []

    def record_created_staging(*args, **kwargs):
        nonlocal created_staging
        created_staging = Path(real_mkdtemp(*args, **kwargs))
        return str(created_staging)

    def fail_first_staging_identity(path):
        if path == created_staging:
            raise OSError("injected staging identity failure")
        return (path.stat().st_dev, path.stat().st_ino)

    def record_exact_rmdir(path):
        rmdir_calls.append(path)
        return real_rmdir(path)

    monkeypatch.setattr(
        backup_module.tempfile,
        "mkdtemp",
        record_created_staging,
    )
    monkeypatch.setattr(
        backup_module,
        "_path_identity",
        fail_first_staging_identity,
    )
    monkeypatch.setattr(Path, "rmdir", record_exact_rmdir)

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(raised.value) == "database backup failed"
    assert created_staging is not None
    assert rmdir_calls == [created_staging]
    assert not created_staging.exists()
    _assert_no_backup_artifacts(output)


def test_staging_identity_failure_preserves_nonempty_replacement_state(
    monkeypatch: pytest.MonkeyPatch,
    database: Path,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    output = output_dir / "backup.sqlite3"
    real_mkdtemp = backup_module.tempfile.mkdtemp
    created_staging: Path | None = None
    marker_name = "non-owned-marker.txt"

    def replace_created_staging_with_nonempty_state(*args, **kwargs):
        nonlocal created_staging
        created_staging = Path(real_mkdtemp(*args, **kwargs))
        (created_staging / marker_name).write_text(
            "preserve",
            encoding="utf-8",
        )
        return str(created_staging)

    def fail_first_staging_identity(path):
        if path == created_staging:
            raise OSError("injected staging identity failure")
        return (path.stat().st_dev, path.stat().st_ino)

    monkeypatch.setattr(
        backup_module.tempfile,
        "mkdtemp",
        replace_created_staging_with_nonempty_state,
    )
    monkeypatch.setattr(
        backup_module,
        "_path_identity",
        fail_first_staging_identity,
    )

    with pytest.raises(BackupError) as raised:
        create_sqlite_backup(database, output, ())

    assert str(raised.value) == "database backup failed"
    assert created_staging is not None
    assert (created_staging / marker_name).read_text(encoding="utf-8") == "preserve"
    assert not output.exists()
    assert not _manifest_path(output).exists()
