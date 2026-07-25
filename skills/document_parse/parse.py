"""document_parse skill 主入口。

流程:
1. router 选 executor
2. executor.extract(source) → raw_data
3. 应用 3-layer 业务规则(KB → hard_code → llm)→ business_judgement
4. 组装 document_parse.v1
5. schema 校验
6. 返回 DocumentParseResult
"""

import os
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional

from platform_core.ports import StructureIndex
from platform_core.settings import AppSettings
from skills.document_parse.router import (
    route,
    build_default_executors,
    NoExecutorError,
)
from skills.document_parse.validator import validate

SCHEMA_VERSION = "document_parse.v1"

_REQUIRED_RESULT_FIELDS = (
    "schema_version",
    "run_id",
    "status",
    "source_type",
    "source_path",
    "file_type",
    "executor_used",
    "executor_implementation_status",
    "parse_intent",
    "extracted_data",
    "business_judgement",
    "validation",
    "reason",
    "knowledge_base_used",
    "elapsed_seconds",
)


def parse(
    source: str,
    parse_intent: str = "structured_business_fields",
    run_id: Optional[str] = None,
    knowledge_base=None,  # 后续 Step 5 接 business_rules/document_parse/*.md
    llm_extractor=None,  # 后续接 common.llm_adapter
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    kb_cache_path: Any = None,
    kb_managed_cache_root: Any = None,
    include_diagnostics: bool = False,
) -> Dict[str, Any]:
    """主入口。

    Args:
        source: file_path 或 url
        parse_intent: "structured_business_fields" | "raw_content" | "metadata_only"
        run_id: 可选,自动生成 UUID
        knowledge_base: 知识库(后续接 business_rules/document_parse/*.md)
        llm_extractor: LLM fallback(后续接 common.llm_adapter)
        app_settings: 已解析的统一应用配置；未提供完整显式依赖时使用。
        config_file: settings JSON 路径，仅在需要加载配置时读取。
        environ: settings 环境变量映射，仅在需要加载配置时读取。
        structure_index: 显式注入的 StructureIndex；与 kb_cache_path、
            kb_managed_cache_root 同时提供时完全绕过 settings 加载。
        kb_cache_path: 显式注入的节点本地 KB cache 路径。
        kb_managed_cache_root: 无 settings 注入时必需的受管 cache 根目录。
        include_diagnostics: 是否附加脱敏的降级诊断。默认 False，确保
            document_parse.v1 的既有顶层返回形状不新增字段。

    Returns:
        document_parse.v1
    """
    start = time.time()
    rid = run_id or f"run-{uuid.uuid4()}"
    diagnostics: List[Dict[str, str]] = []
    is_url = bool(source) and source.startswith(("http://", "https://"))

    if not source:
        return _build_blocked_result(
            rid=rid,
            source=source,
            source_type="file",
            file_type="unknown",
            executor_used="unknown",
            elapsed=time.time() - start,
            reason="source_missing",
            error="Source is required.",
            impl_status="implemented",
            parse_intent=parse_intent,
            include_diagnostics=include_diagnostics,
        )

    # Step 1: 路由
    try:
        executor, file_type = route(source)
    except NoExecutorError as e:
        return _build_blocked_result(
            rid=rid,
            source=source,
            source_type="url" if is_url else "file",
            file_type="unknown",
            executor_used="unknown",
            elapsed=time.time() - start,
            reason="parse_error",
            error=str(e),
            impl_status="implemented",
            parse_intent=parse_intent,
            include_diagnostics=include_diagnostics,
        )

    source_type = "url" if file_type == "url" else "file"

    # Step 2: executor 提取
    try:
        executor_result = executor.extract(source)
    except Exception:
        return _build_blocked_result(
            rid=rid,
            source=source,
            source_type=source_type,
            file_type=file_type,
            executor_used=executor.name,
            elapsed=time.time() - start,
            reason="parse_error",
            error="Document executor failed.",
            impl_status="implemented",
            parse_intent=parse_intent,
            status="failed",
            include_diagnostics=include_diagnostics,
        )

    # Step 3: 组装结果
    elapsed = time.time() - start

    if executor_result["status"] != "success":
        impl_status = executor_result.get("implementation_status", "implemented")
        executor_status = executor_result.get("status")
        source_missing = source_type == "file" and not os.path.exists(source)
        if executor_status == "failed":
            status = "failed"
            reason = "parse_error"
        elif source_missing:
            status = "blocked"
            reason = "source_missing"
        else:
            status = "blocked"
            reason = (
                "executor_not_implemented"
                if impl_status == "stub"
                else "parse_error"
            )
        return _build_blocked_result(
            rid=rid,
            source=source,
            source_type=source_type,
            file_type=file_type,
            executor_used=executor.name,
            elapsed=elapsed,
            reason=reason,
            error=executor_result.get("error"),
            impl_status=impl_status,
            parse_intent=parse_intent,
            status=status,
            include_diagnostics=include_diagnostics,
        )

    # Step 4: 应用业务规则(3-layer fallback)
    business_judgement = _apply_business_rules(
        raw_data=executor_result["raw_data"],
        source_path=source,
        knowledge_base=knowledge_base,
        llm_extractor=llm_extractor,
        app_settings=app_settings,
        config_file=config_file,
        environ=environ,
        structure_index=structure_index,
        kb_cache_path=kb_cache_path,
        kb_managed_cache_root=kb_managed_cache_root,
        diagnostics=diagnostics,
    )

    # Step 5: 组装 document_parse.v1
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "status": "success" if business_judgement["confidence"] != "low" else "needs_review",
        "source_type": source_type,
        "source_path": source,
        "file_type": file_type,
        "executor_used": executor.name,
        "executor_implementation_status": executor_result["implementation_status"],
        "parse_intent": parse_intent,
        "extracted_data": executor_result["raw_data"],
        "business_judgement": business_judgement,
        "validation": {
            "schema_valid": False,
            "required_fields_present": False,
            "missing_fields": [],
            "warnings": [],
        },
        "blockers": [],
        "reason": "success",
        "knowledge_base_used": business_judgement["rule_source"] == "knowledge_base",
        "elapsed_seconds": round(elapsed, 3),
    }
    if include_diagnostics:
        result["diagnostics"] = diagnostics

    # Step 6: schema 校验
    return _finalize_result(result)


def _apply_business_rules(
    raw_data: Dict[str, Any],
    source_path: str = "",
    knowledge_base=None,
    llm_extractor=None,
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    kb_cache_path: Any = None,
    kb_managed_cache_root: Any = None,
    diagnostics: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """3-layer fallback: 知识库(L1) → 硬编码(L2) → LLM(L3)。

    L1: 默认调 skills.document_parse.kb.query_kb(基于 PageIndex + 文件名前缀)
    L2: _hardcoded_classify(MVP 占位,KB 不可用时降级)
    L3: llm_extractor(GPUStack LLM,可选,KB + hard_code 都不高时降级)
    """
    # L1: KB 匹配(使用统一配置选择的 StructureIndex provider)
    try:
        if knowledge_base is None:
            from skills.document_parse.kb import query_kb as default_kb
            kb_result = default_kb(
                raw_data,
                source_path=source_path,
                app_settings=app_settings,
                config_file=config_file,
                environ=environ,
                structure_index=structure_index,
                cache_path=kb_cache_path,
                managed_cache_root=kb_managed_cache_root,
                diagnostics=diagnostics,
            )
        elif callable(knowledge_base):
            # 兼容旧的 callable 接口(单参数 raw_data)
            kb_result = knowledge_base(raw_data)
        else:
            # 旧 .match() 接口(archive_files 风格)
            kb_result = knowledge_base.match(raw_data) if hasattr(knowledge_base, "match") else None

        if kb_result is not None and kb_result.get("confidence") in ("high", "medium"):
            return kb_result
    except Exception:
        if diagnostics is not None:
            diagnostics.append(
                {
                    "component": "document_parse.knowledge_base",
                    "status": "degraded",
                    "error_code": "DOCUMENT_PARSE.KB.UNEXPECTED",
                    "provider": "custom" if knowledge_base is not None else "unknown",
                    "message": "Knowledge-base lookup is unavailable.",
                }
            )

    # L2: 硬编码 fallback
    judgement = _hardcoded_classify(raw_data)
    if judgement["confidence"] == "high":
        return judgement

    # L3: LLM fallback(可选)
    if llm_extractor is not None:
        try:
            return llm_extractor(raw_data)
        except Exception:
            pass

    return judgement


def _hardcoded_classify(raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """硬编码 fallback 分类(MVP 用,后续迁到 business_rules/document_parse/classification.md)。"""
    text = raw_data.get("raw_text", "")

    category = "其他"
    confidence = "medium"
    rule_source = "hard_code"

    if "招标公告" in text or "采购公告" in text or "公开招标" in text:
        category = "招标公告"
        confidence = "high"
    elif "投标文件" in text or "投标书" in text or "投标响应" in text:
        category = "投标文件"
        confidence = "high"
    elif "合同" in text and ("签订" in text or "签署" in text or "签订合同" in text):
        category = "合同文件"
        confidence = "medium"
    elif "报名" in text or "报名表" in text or "登记" in text:
        category = "报名材料"
        confidence = "medium"

    return {
        "category": category,
        "extracted_fields": {},  # MVP 不做字段抽取(Step 5 加)
        "confidence": confidence,
        "rule_source": rule_source,
    }


def _build_blocked_result(
    rid: str,
    source: str,
    source_type: str,
    file_type: str,
    executor_used: str,
    elapsed: float,
    reason: str,
    error: Optional[str],
    impl_status: str,
    parse_intent: str,
    status: str = "blocked",
    include_diagnostics: bool = False,
) -> Dict[str, Any]:
    """构造 blocked 结果(确保 schema 有效)。"""
    warnings = [error] if error else []
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "status": status,
        "source_type": source_type,
        "source_path": source,
        "file_type": file_type,
        "executor_used": executor_used,
        "executor_implementation_status": impl_status,
        "parse_intent": parse_intent,
        "extracted_data": {
            "paragraphs": [],
            "tables": [],
            "metadata": {},
            "raw_text": "",
        },
        "business_judgement": {
            "category": "其他",
            "extracted_fields": {},
            "confidence": "low",
            "rule_source": "none",
        },
        "validation": {
            "schema_valid": False,
            "required_fields_present": False,
            "missing_fields": [],
            "warnings": warnings,
        },
        "blockers": warnings,
        "reason": reason,
        "knowledge_base_used": False,
        "elapsed_seconds": round(elapsed, 3),
    }
    if include_diagnostics:
        result["diagnostics"] = []
    return _finalize_result(result)


def _finalize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Finalize every terminal status with the authoritative validator."""
    validation = result.setdefault(
        "validation",
        {
            "schema_valid": False,
            "required_fields_present": False,
            "missing_fields": [],
            "warnings": [],
        },
    )
    missing_fields = [
        field for field in _REQUIRED_RESULT_FIELDS if field not in result
    ]
    validation["schema_valid"] = False
    validation["required_fields_present"] = not missing_fields
    validation["missing_fields"] = missing_fields
    validation.setdefault("warnings", [])

    is_valid, errors = validate(result)
    validation["schema_valid"] = is_valid
    validation["warnings"].extend(
        error for error in errors if error not in validation["warnings"]
    )
    if not is_valid:
        result["status"] = "blocked"
        result["reason"] = "schema_invalid"
    return result
