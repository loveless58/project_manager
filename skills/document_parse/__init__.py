"""document_parse skill — 文档解析标准化 skill。

提供:
- parse(source, parse_intent) → document_parse.v1
- validate(result) → (is_valid, errors)
- validate_or_raise(result)
"""

from skills.document_parse.parse import parse
from skills.document_parse.validator import (
    validate,
    validate_or_raise,
    DocumentParseValidationError,
    SCHEMA_VERSION,
)
from skills.document_parse.router import route, build_default_executors, NoExecutorError

__all__ = [
    "parse",
    "validate",
    "validate_or_raise",
    "DocumentParseValidationError",
    "SCHEMA_VERSION",
    "route",
    "build_default_executors",
    "NoExecutorError",
]
