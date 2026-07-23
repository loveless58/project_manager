"""document_parse.v1 schema 校验器。

提供两个 API:
- validate(result) -> (is_valid, errors)  # 单条校验,不抛异常
- validate_or_raise(result)               # 失败抛 DocumentParseValidationError

优先用 jsonschema 库(已装 4.26.0),如果未装则 fallback 手写 validator。
"""

import json
import os
from typing import Any, Dict, List, Tuple

try:
    import jsonschema  # type: ignore
    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False


SCHEMA_VERSION = "document_parse.v1"
_SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__), "schemas", "document_parse.v1.json"
)


class DocumentParseValidationError(Exception):
    """document_parse.v1 校验失败。"""

    def __init__(self, errors: List[str]):
        self.errors = errors
        super().__init__(
            "document_parse.v1 validation failed: " + "; ".join(errors)
        )


def _load_schema() -> Dict[str, Any]:
    """延迟加载 schema 文件。"""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def validate(result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """校验 document_parse.v1 结果。

    Returns:
        (is_valid, errors) - is_valid=True 时 errors 为空 list
    """
    if _HAS_JSONSCHEMA:
        return _validate_jsonschema(result)
    return _validate_manual(result)


def _validate_jsonschema(result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """用 jsonschema 库校验(优先路径)。"""
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(result), key=lambda e: list(e.path)):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return (len(errors) == 0, errors)


def _validate_manual(result: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """手写 fallback validator(jsonschema 未装时使用,只校验关键字段)。"""
    errors: List[str] = []

    required = [
        "schema_version", "run_id", "status", "source_type", "source_path",
        "file_type", "executor_used", "executor_implementation_status",
        "parse_intent", "extracted_data", "business_judgement",
        "validation", "reason", "knowledge_base_used", "elapsed_seconds",
    ]
    for field in required:
        if field not in result:
            errors.append(f"{field}: missing required field")

    if result.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version: must be '{SCHEMA_VERSION}', "
            f"got '{result.get('schema_version')}'"
        )

    _check_enum(result, "status", ["success", "needs_review", "blocked"], errors)
    _check_enum(result, "source_type", ["file", "url"], errors)
    _check_enum(
        result,
        "executor_used",
        ["docx", "xlsx", "pdf", "wps", "url", "unknown"],
        errors,
    )
    _check_enum(
        result,
        "executor_implementation_status",
        ["implemented", "stub"],
        errors,
    )
    _check_enum(
        result,
        "parse_intent",
        ["structured_business_fields", "raw_content", "metadata_only"],
        errors,
    )
    _check_enum(
        result,
        "reason",
        [
            "success", "knowledge_base_no_match", "knowledge_base_low_confidence",
            "executor_not_implemented", "human_review_required",
            "parse_error", "source_missing", "schema_invalid",
        ],
        errors,
    )

    elapsed = result.get("elapsed_seconds")
    if elapsed is not None and (not isinstance(elapsed, (int, float)) or elapsed < 0):
        errors.append("elapsed_seconds: must be a non-negative number")

    return (len(errors) == 0, errors)


def _check_enum(
    result: Dict[str, Any],
    field: str,
    allowed: List[str],
    errors: List[str],
) -> None:
    """校验 enum 字段。"""
    value = result.get(field)
    if value is not None and value not in allowed:
        errors.append(f"{field}: must be one of {allowed}, got '{value}'")


def validate_or_raise(result: Dict[str, Any]) -> None:
    """校验失败抛 DocumentParseValidationError。"""
    is_valid, errors = validate(result)
    if not is_valid:
        raise DocumentParseValidationError(errors)
