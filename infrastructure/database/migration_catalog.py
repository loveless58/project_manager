import hashlib
import re
from collections.abc import Sequence
from pathlib import Path

from .contracts import MigrationCatalogError, MigrationInfo


_TRANSACTION_CONTROL = re.compile(
    r"(?:\A|;)\s*(?:BEGIN|COMMIT|END\s+TRANSACTION|ROLLBACK|SAVEPOINT|RELEASE)\b",
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
        except UnicodeDecodeError:
            raise MigrationCatalogError("migration SQL must be valid UTF-8") from None

        masked_sql = _mask_sql_literals_and_comments(sql)
        if _TRANSACTION_CONTROL.search(masked_sql):
            raise MigrationCatalogError(
                "migration SQL must not contain transaction control"
            )
        _validate_sqlite_schema_boundary(sql)

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


def _validate_sqlite_schema_boundary(sql: str) -> None:
    statement: list[str] = []
    for token in _tokenize_sql(sql):
        if token == ";":
            if _statement_escapes_main_schema(statement):
                raise MigrationCatalogError(
                    "migration SQL must stay within the main schema boundary"
                )
            statement = []
        else:
            statement.append(token)

    if _statement_escapes_main_schema(statement):
        raise MigrationCatalogError(
            "migration SQL must stay within the main schema boundary"
        )


def _statement_escapes_main_schema(tokens: Sequence[str]) -> bool:
    if not tokens:
        return False
    if tokens[0] in {"attach", "detach"}:
        return True

    for offset in range(len(tokens)):
        operation = tokens[offset:]
        if len(operation) >= 2 and operation[0] == "create" and operation[1] in {
            "temp",
            "temporary",
        }:
            return True

        target_index = _schema_operation_target_index(operation)
        if (
            target_index is not None
            and target_index + 1 < len(operation)
            and operation[target_index + 1] == "."
            and operation[target_index] != "main"
        ):
            return True
    return False


def _schema_operation_target_index(tokens: Sequence[str]) -> int | None:
    operation = tokens[0]
    if operation == "create":
        index = 1
        if index < len(tokens) and tokens[index] == "unique":
            index += 1
        if index < len(tokens) and tokens[index] == "virtual":
            index += 1
            if index >= len(tokens) or tokens[index] != "table":
                return None
        elif index >= len(tokens) or tokens[index] not in {
            "index",
            "table",
            "trigger",
            "view",
        }:
            return None
        index += 1
        return _skip_optional_tokens(tokens, index, ("if", "not", "exists"))

    if operation == "drop" and len(tokens) >= 2 and tokens[1] in {
        "index",
        "table",
        "trigger",
        "view",
    }:
        return _skip_optional_tokens(tokens, 2, ("if", "exists"))

    if operation == "alter" and len(tokens) >= 2 and tokens[1] == "table":
        return 2

    if operation in {"insert", "replace"}:
        index = 1
        if index < len(tokens) and tokens[index] == "or":
            index += 2
        if index < len(tokens) and tokens[index] == "into":
            return index + 1
        return None

    if operation == "update":
        index = 1
        if index < len(tokens) and tokens[index] == "or":
            index += 2
        return index

    if operation == "delete" and len(tokens) >= 2 and tokens[1] == "from":
        return 2

    if operation in {"analyze", "reindex"}:
        return 1
    return None


def _skip_optional_tokens(
    tokens: Sequence[str], index: int, optional: tuple[str, ...]
) -> int:
    if tuple(tokens[index : index + len(optional)]) == optional:
        return index + len(optional)
    return index


def _tokenize_sql(sql: str) -> tuple[str, ...]:
    tokens: list[str] = []
    index = 0
    while index < len(sql):
        character = sql[index]
        next_character = sql[index + 1] if index + 1 < len(sql) else ""

        if character.isspace():
            index += 1
        elif character == "-" and next_character == "-":
            index = _skip_line_comment(sql, index)
        elif character == "/" and next_character == "*":
            index = _skip_block_comment(sql, index)
        elif character in {"'", '"', "`"}:
            token, index = _read_quoted_token(sql, index, character)
            tokens.append(token.casefold())
        elif character == "[":
            token, index = _read_bracket_token(sql, index)
            tokens.append(token.casefold())
        elif character.isalpha() or character == "_":
            end = index + 1
            while end < len(sql) and (
                sql[end].isalnum() or sql[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(sql[index:end].casefold())
            index = end
        else:
            tokens.append(character)
            index += 1
    return tuple(tokens)


def _read_quoted_token(sql: str, index: int, delimiter: str) -> tuple[str, int]:
    value: list[str] = []
    escaped_delimiter = delimiter * 2
    index += 1
    while index < len(sql):
        if sql.startswith(escaped_delimiter, index):
            value.append(delimiter)
            index += 2
        elif sql[index] == delimiter:
            return "".join(value), index + 1
        else:
            value.append(sql[index])
            index += 1
    raise MigrationCatalogError("migration SQL is not lexically complete")


def _read_bracket_token(sql: str, index: int) -> tuple[str, int]:
    end = sql.find("]", index + 1)
    if end < 0:
        raise MigrationCatalogError("migration SQL is not lexically complete")
    return sql[index + 1 : end], end + 1


def _skip_line_comment(sql: str, index: int) -> int:
    while index < len(sql) and sql[index] not in "\r\n":
        index += 1
    return index


def _skip_block_comment(sql: str, index: int) -> int:
    end = sql.find("*/", index + 2)
    if end < 0:
        raise MigrationCatalogError("migration SQL is not lexically complete")
    return end + 2
