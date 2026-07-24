import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from .contracts import MigrationCatalogError, MigrationInfo


_TRANSACTION_CONTROL = re.compile(
    r"(?:\A|;)\s*(?:BEGIN|COMMIT|ROLLBACK|SAVEPOINT|RELEASE)\b",
    re.IGNORECASE | re.MULTILINE,
)


def load_migration_catalog(directory: Path) -> tuple[MigrationInfo, ...]:
    items: list[MigrationInfo] = []

    for path in directory.iterdir():
        if not path.is_file() or path.suffix != ".sql":
            continue

        filename = path.name
        match = re.fullmatch(
            r"(?P<version>[0-9]{4})_(?P<name>[a-z][a-z0-9_]*)\.sql", filename
        )
        if match is None:
            raise MigrationCatalogError(f"migration filename is invalid: {filename}")

        payload = path.read_bytes()
        try:
            sql = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise MigrationCatalogError("migration SQL must be valid UTF-8") from error

        if _TRANSACTION_CONTROL.search(_mask_sql_literals_and_comments(sql)):
            raise MigrationCatalogError(
                "migration SQL must not contain transaction control"
            )

        items.append(
            MigrationInfo(
                version=int(match["version"]),
                name=match["name"],
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                path=path,
            )
        )

    items.sort(key=lambda item: item.version)
    versions = [item.version for item in items]
    if len(set(versions)) != len(versions):
        raise MigrationCatalogError("migration catalog contains duplicate version")
    if versions != list(range(1, len(items) + 1)):
        raise MigrationCatalogError("migration versions must be contiguous starting at 1")

    return tuple(items)


def catalog_target_version(catalog: Sequence[MigrationInfo]) -> int:
    return catalog[-1].version if catalog else 0


def _mask_sql_literals_and_comments(sql: str) -> str:
    masked: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        character = sql[index]
        next_character = sql[index + 1] if index + 1 < length else ""

        if character == "'":
            index = _mask_quoted(sql, masked, index, "'", "''")
        elif character == '"':
            index = _mask_quoted(sql, masked, index, '"', '""')
        elif character == "`":
            index = _mask_quoted(sql, masked, index, "`", "``")
        elif character == "[":
            index = _mask_bracket_identifier(sql, masked, index)
        elif character == "-" and next_character == "-":
            index = _mask_line_comment(sql, masked, index)
        elif character == "/" and next_character == "*":
            index = _mask_block_comment(sql, masked, index)
        else:
            masked.append(character)
            index += 1

    return "".join(masked)


def _mask_quoted(
    sql: str, masked: list[str], index: int, delimiter: str, escaped_delimiter: str
) -> int:
    masked.append(" ")
    index += 1

    while index < len(sql):
        if sql.startswith(escaped_delimiter, index):
            masked.extend("  ")
            index += 2
        elif sql[index] == delimiter:
            masked.append(" ")
            return index + 1
        else:
            masked.append(" ")
            index += 1

    raise MigrationCatalogError("migration SQL is not lexically complete")


def _mask_bracket_identifier(sql: str, masked: list[str], index: int) -> int:
    masked.append(" ")
    index += 1

    while index < len(sql):
        if sql[index] == "]":
            masked.append(" ")
            return index + 1
        masked.append(" ")
        index += 1

    raise MigrationCatalogError("migration SQL is not lexically complete")


def _mask_line_comment(sql: str, masked: list[str], index: int) -> int:
    while index < len(sql) and sql[index] not in "\r\n":
        masked.append(" ")
        index += 1
    return index


def _mask_block_comment(sql: str, masked: list[str], index: int) -> int:
    while index < len(sql):
        if sql.startswith("*/", index):
            masked.extend("  ")
            return index + 2
        masked.append(" ")
        index += 1

    raise MigrationCatalogError("migration SQL is not lexically complete")
