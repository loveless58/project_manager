import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from weakref import WeakKeyDictionary

from .contracts import MigrationCatalogError, MigrationInfo


_TRANSACTION_CONTROL = re.compile(
    r"(?:\A|;)\s*(?:BEGIN|COMMIT|END\s+TRANSACTION|ROLLBACK|SAVEPOINT|RELEASE)\b",
    re.IGNORECASE | re.MULTILINE,
)


class _SqlTokenKind(Enum):
    WORD = "word"
    STRING = "string"
    QUOTED_IDENTIFIER = "quoted_identifier"
    PUNCTUATION = "punctuation"


@dataclass(frozen=True, slots=True)
class _SqlToken:
    kind: _SqlTokenKind
    value: str


@dataclass(frozen=True, slots=True)
class _MigrationCatalogRecord:
    directory: Path
    directory_identity: tuple[int, int]
    items: tuple[MigrationInfo, ...]


class MigrationCatalog(Sequence[MigrationInfo]):
    """Opaque, loader-issued migration catalog capability.

    The SQL files remain the source of truth.  Every database-mutating public
    boundary revalidates the registered directory and bytes before opening a
    database, so a copied/replaced DTO sequence is never migration authority.
    """

    __slots__ = ("__weakref__",)

    def __init__(self) -> None:
        raise TypeError("MigrationCatalog values are created by load_migration_catalog")

    def __len__(self) -> int:
        return len(_catalog_record_for(self).items)

    def __getitem__(self, index):
        return _catalog_record_for(self).items[index]

    def __iter__(self):
        return iter(_catalog_record_for(self).items)

    def __copy__(self):
        raise TypeError("MigrationCatalog cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("MigrationCatalog cannot be copied")

    def __repr__(self) -> str:
        return f"MigrationCatalog(target_version={catalog_target_version(self)})"


_CATALOG_RECORD_LOCK = RLock()
_CATALOG_RECORDS = WeakKeyDictionary()


def _issue_catalog(record: _MigrationCatalogRecord) -> MigrationCatalog:
    catalog = object.__new__(MigrationCatalog)
    with _CATALOG_RECORD_LOCK:
        _CATALOG_RECORDS[catalog] = record
    return catalog


def _catalog_record_for(catalog: object) -> _MigrationCatalogRecord:
    if type(catalog) is not MigrationCatalog:
        raise MigrationCatalogError("migration catalog provenance is invalid")
    with _CATALOG_RECORD_LOCK:
        try:
            return _CATALOG_RECORDS[catalog]
        except KeyError:
            raise MigrationCatalogError(
                "migration catalog provenance is invalid"
            ) from None


def load_migration_catalog(directory: Path) -> MigrationCatalog:
    try:
        resolved_directory = directory.resolve(strict=True)
        if not resolved_directory.is_dir():
            raise OSError
        items = _load_migration_items(resolved_directory)
        stat_result = resolved_directory.stat()
    except MigrationCatalogError:
        raise
    except OSError:
        raise MigrationCatalogError("migration catalog directory is invalid") from None
    return _issue_catalog(
        _MigrationCatalogRecord(
            directory=resolved_directory,
            directory_identity=(stat_result.st_dev, stat_result.st_ino),
            items=items,
        )
    )


def validate_migration_catalog(catalog: object) -> MigrationCatalog:
    record = _catalog_record_for(catalog)
    try:
        stat_result = record.directory.stat()
        if (
            not record.directory.is_dir()
            or (stat_result.st_dev, stat_result.st_ino) != record.directory_identity
            or _load_migration_items(record.directory) != record.items
        ):
            raise MigrationCatalogError("migration catalog provenance is invalid")
    except MigrationCatalogError:
        raise
    except OSError:
        raise MigrationCatalogError("migration catalog provenance is invalid") from None
    return catalog


def _load_migration_items(directory: Path) -> tuple[MigrationInfo, ...]:
    items: list[MigrationInfo] = []

    for path in directory.iterdir():
        if path.suffix != ".sql":
            continue
        if path.is_symlink() or not path.is_file():
            raise MigrationCatalogError("migration path is invalid")

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
                path=path.resolve(strict=True),
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
    statement: list[_SqlToken] = []
    for token in _tokenize_sql(sql):
        if _is_punctuation(token, ";"):
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


def _statement_escapes_main_schema(tokens: Sequence[_SqlToken]) -> bool:
    operation = _semantic_operation(tokens)
    if not operation:
        return False
    return _operation_escapes_main_schema(operation)


def _semantic_operation(tokens: Sequence[_SqlToken]) -> Sequence[_SqlToken]:
    if not tokens:
        return ()
    if not _is_word(tokens[0], "with"):
        return tokens

    depth = 0
    for index, token in enumerate(tokens[1:], start=1):
        if _is_punctuation(token, "("):
            depth += 1
        elif _is_punctuation(token, ")"):
            depth = max(0, depth - 1)
        elif depth == 0 and _is_word(
            token,
            "delete",
            "insert",
            "replace",
            "update",
        ):
            return tokens[index:]
    return ()


def _operation_escapes_main_schema(tokens: Sequence[_SqlToken]) -> bool:
    if _is_word(tokens[0], "attach", "detach"):
        return True

    if (
        len(tokens) >= 2
        and _is_word(tokens[0], "create")
        and _is_word(tokens[1], "temp", "temporary")
    ):
        return True

    if _is_word(tokens[0], "pragma") and _target_uses_non_main_schema(
        tokens, 1
    ):
        return True

    target_index = _schema_operation_target_index(tokens)
    if _target_uses_non_main_schema(tokens, target_index):
        return True

    if _starts_create_trigger(tokens):
        body_index = _find_word(tokens, "begin", start=target_index or 0)
        if body_index is not None:
            return _statement_escapes_main_schema(tokens[body_index + 1 :])
    return False


def _schema_operation_target_index(tokens: Sequence[_SqlToken]) -> int | None:
    if _is_word(tokens[0], "create"):
        index = 1
        if index < len(tokens) and _is_word(tokens[index], "unique"):
            index += 1
        if index < len(tokens) and _is_word(tokens[index], "virtual"):
            index += 1
            if index >= len(tokens) or not _is_word(tokens[index], "table"):
                return None
        elif index >= len(tokens) or not _is_word(
            tokens[index],
            "index",
            "table",
            "trigger",
            "view",
        ):
            return None
        index += 1
        return _skip_optional_words(tokens, index, "if", "not", "exists")

    if (
        _is_word(tokens[0], "drop")
        and len(tokens) >= 2
        and _is_word(tokens[1], "index", "table", "trigger", "view")
    ):
        return _skip_optional_words(tokens, 2, "if", "exists")

    if (
        _is_word(tokens[0], "alter")
        and len(tokens) >= 2
        and _is_word(tokens[1], "table")
    ):
        return 2

    if _is_word(tokens[0], "insert", "replace"):
        index = 1
        if index < len(tokens) and _is_word(tokens[index], "or"):
            index += 2
        if index < len(tokens) and _is_word(tokens[index], "into"):
            return index + 1
        return None

    if _is_word(tokens[0], "update"):
        index = 1
        if index < len(tokens) and _is_word(tokens[index], "or"):
            index += 2
        return index

    if (
        _is_word(tokens[0], "delete")
        and len(tokens) >= 2
        and _is_word(tokens[1], "from")
    ):
        return 2

    if _is_word(tokens[0], "analyze", "reindex"):
        return 1
    return None


def _target_uses_non_main_schema(
    tokens: Sequence[_SqlToken], target_index: int | None
) -> bool:
    if target_index is None or target_index + 1 >= len(tokens):
        return False
    schema_token = tokens[target_index]
    schema = _identifier_value(schema_token)
    if (
        schema is None
        and schema_token.kind is _SqlTokenKind.STRING
        and _is_punctuation(tokens[target_index + 1], ".")
    ):
        schema = schema_token.value.casefold()
    return (
        schema is not None
        and _is_punctuation(tokens[target_index + 1], ".")
        and schema != "main"
    )


def _starts_create_trigger(tokens: Sequence[_SqlToken]) -> bool:
    index = 1
    if not _is_word(tokens[0], "create"):
        return False
    if index < len(tokens) and _is_word(tokens[index], "temp", "temporary"):
        index += 1
    return index < len(tokens) and _is_word(tokens[index], "trigger")


def _skip_optional_words(
    tokens: Sequence[_SqlToken], index: int, *optional: str
) -> int:
    if index + len(optional) > len(tokens):
        return index
    if all(_is_word(tokens[index + offset], word) for offset, word in enumerate(optional)):
        return index + len(optional)
    return index


def _find_word(
    tokens: Sequence[_SqlToken], word: str, *, start: int = 0
) -> int | None:
    for index in range(start, len(tokens)):
        if _is_word(tokens[index], word):
            return index
    return None


def _is_word(token: _SqlToken, *values: str) -> bool:
    return token.kind is _SqlTokenKind.WORD and token.value in values


def _is_punctuation(token: _SqlToken, value: str) -> bool:
    return token.kind is _SqlTokenKind.PUNCTUATION and token.value == value


def _identifier_value(token: _SqlToken) -> str | None:
    if token.kind in {_SqlTokenKind.WORD, _SqlTokenKind.QUOTED_IDENTIFIER}:
        return token.value
    return None


def _tokenize_sql(sql: str) -> tuple[_SqlToken, ...]:
    tokens: list[_SqlToken] = []
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
        elif character == "'":
            token, index = _read_quoted_token(sql, index, character)
            tokens.append(_SqlToken(_SqlTokenKind.STRING, token))
        elif character in {'"', "`"}:
            token, index = _read_quoted_token(sql, index, character)
            tokens.append(
                _SqlToken(_SqlTokenKind.QUOTED_IDENTIFIER, token.casefold())
            )
        elif character == "[":
            token, index = _read_bracket_token(sql, index)
            tokens.append(
                _SqlToken(_SqlTokenKind.QUOTED_IDENTIFIER, token.casefold())
            )
        elif character.isalpha() or character == "_":
            end = index + 1
            while end < len(sql) and (
                sql[end].isalnum() or sql[end] in {"_", "$"}
            ):
                end += 1
            tokens.append(
                _SqlToken(_SqlTokenKind.WORD, sql[index:end].casefold())
            )
            index = end
        else:
            tokens.append(_SqlToken(_SqlTokenKind.PUNCTUATION, character))
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


__all__ = [
    "MigrationCatalog",
    "catalog_target_version",
    "load_migration_catalog",
    "validate_migration_catalog",
]
