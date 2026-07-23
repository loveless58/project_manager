"""document_parse skill — 文档解析标准化 skill。

提供:
- parse(source, parse_intent, knowledge_base, llm_extractor) → document_parse.v1
- validate(result) → (is_valid, errors)
- validate_or_raise(result)
- make_llm_extractor() — GPUStack LLM fallback 构造器
- query_kb() — PageIndex KB 查询
"""

from skills.document_parse.parse import parse
from skills.document_parse.validator import (
    validate,
    validate_or_raise,
    DocumentParseValidationError,
    SCHEMA_VERSION,
)
from skills.document_parse.router import route, build_default_executors, NoExecutorError
from skills.document_parse.llm_extractor import make_llm_extractor, get_default_llm_extractor
from skills.document_parse.kb import query_kb, get_kb_structure, CATEGORY_TRIGGERS

__all__ = [
    "parse",
    "validate",
    "validate_or_raise",
    "DocumentParseValidationError",
    "SCHEMA_VERSION",
    "route",
    "build_default_executors",
    "NoExecutorError",
    "make_llm_extractor",
    "get_default_llm_extractor",
    "query_kb",
    "get_kb_structure",
    "CATEGORY_TRIGGERS",
]
