"""document_parse 知识库(KB),基于可配置 StructureIndex + 文件名前缀双信号。

设计:
1. KB 文档(business_rules/document_parse/kb.md)按章节分类
2. StructureIndex KB 索引:把 kb.md 转换为可查询的层级结构
3. Query 时三信号评分:
   a. 文件名前缀(强信号): "合同"/"承诺函"/"授权委托"/"报名" 等 → +30
   b. KB 章节匹配(node 数): 多 node → +20, 单 node → +10
   c. 正文关键词(弱信号): +5
4. 取最高分类别,分数 ≥30 → high, ≥15 → medium, <15 → low
5. Cache:运行时工作区(key=内容哈希 + provider + provider version)

详见 skills/document_parse/SKILL.md §Three-Layer Fallback。
"""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from platform_core.models import StructureIndexRequest
from platform_core.ports import StructureIndex
from platform_core.settings import AppSettings
from skills.document_parse.providers import (
    resolve_document_parse_cache_path,
    resolve_document_parse_runtime,
)


PathLike = Union[str, os.PathLike]
_KB_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DOC_PATH = os.path.abspath(
    os.path.join(_KB_DIR, "..", "..", "business_rules", "document_parse", "kb.md")
)
_CACHE_SCHEMA_VERSION = "document_parse_kb_cache.v3"
_DIAGNOSTIC_COMPONENT = "document_parse.knowledge_base"


# 类别 → 触发关键词(用于 KB 节点查找 + 正文二次确认 + 文件名前缀)
CATEGORY_TRIGGERS: Dict[str, Dict[str, List[str]]] = {
"招标公告": {
    "node_keywords": [
        "招标公告", "采购公告", "公开招标", "邀请招标", "招标说明书", "澄清",
    ],
    "content_keywords": [
        "招标公告", "采购公告", "公开招标", "邀请招标", "招标说明书",
        "澄清", "澄清1号", "澄清2号", "澄清3号", "澄清答复",
    ],
    "filename_keywords": [
        "招标", "竞价", "采购公告", "澄清", "澄清1号", "澄清2号", "澄清3号",
    ],
},
    "投标文件": {
        "node_keywords": ["投标文件", "投标"],
        "content_keywords": [
            "投标文件", "投标书", "投标响应", "投标函", "投标报价",
        ],
        "filename_keywords": ["投标", "响应文件", "外发", "外发文件"],
    },
    "合同文件": {
        "node_keywords": ["合同文件", "合同"],
        "content_keywords": ["合同", "协议", "维保"],
        "filename_keywords": [
            "合同", "协议", "维保", "法审", "合作协议", "技术开发", "服务协议",
        ],
    },
    "报名材料": {
        "node_keywords": ["报名材料", "报名"],
        "content_keywords": [
            "报名", "报名表", "登记", "备案", "承诺函", "授权委托",
            "购标说明", "购标", "承诺书递交", "购标所需资料", "购买招标文件",
        ],
        "filename_keywords": [
            "投标人信息登记备案表", "报名表", "承诺函", "授权委托",
            "报名材料", "报名所需资料", "登记备案",
            "购标说明", "购标", "承诺书递交", "购标所需资料", "购买招标文件",
        ],
    },
    "其他": {
        "node_keywords": ["其他"],
        "content_keywords": [],
        "filename_keywords": [
            "统计", "明细", "模板", "列表", "凭证", "报备",
            "预测", "汇总", "全订单", "资金滚动", "开票申请",
            "采购款", "采购明细",
        ],
    },
}


# Negative filename signals: 命中这些前缀的文档,即使含其他类别关键词也降权
# 解决 "投标项目分状态统计.xlsx" 被误判为 "投标文件" 的问题
# 注意: 移除了 "模板" — "供应商业务授权委托书模板.docx" 应判 报名材料
NEGATIVE_FILENAME_SIGNALS: List[str] = [
    "统计", "明细", "列表", "汇总", "凭证", "报备",
]


class KnowledgeBaseUnavailable(RuntimeError):
    """Stable, redacted failure used by the KB fallback boundary."""

    def __init__(self, error_code: str, provider: str, message: str) -> None:
        self.error_code = error_code
        self.provider = _safe_provider_name(provider)
        self.public_message = message
        super().__init__(message)

    def diagnostic(self) -> Dict[str, str]:
        return {
            "component": _DIAGNOSTIC_COMPONENT,
            "status": "degraded",
            "error_code": self.error_code,
            "provider": self.provider,
            "message": self.public_message,
        }


class DocumentParseCacheLayoutError(ValueError):
    """A cache marker or entry is not an owned document-parse artifact."""


def _safe_provider_name(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    if re.fullmatch(r"[a-z0-9_.-]{1,64}", normalized):
        return normalized
    return "unknown"


def _normalize_provider_version(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _provider_identity(structure_index: StructureIndex) -> Tuple[str, str]:
    provider = _safe_provider_name(getattr(structure_index, "name", "unknown"))
    if provider == "disabled":
        return provider, "disabled"

    try:
        provider_version = _normalize_provider_version(
            getattr(structure_index, "provider_version", "")
        )
    except Exception:
        provider_version = ""
    if not provider_version:
        try:
            provider_version = _normalize_provider_version(
                structure_index.probe().provider_version
            )
        except Exception:
            provider_version = ""
    if not provider_version:
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.PROVIDER_IDENTITY_UNAVAILABLE",
            provider,
            "Knowledge-base provider identity is unavailable.",
        )
    return provider, provider_version


def _cache_key(
    *,
    document_version_id: str,
    content_hash: str,
    provider: str,
    provider_version: str,
) -> str:
    serialized = json.dumps(
        {
            "cache_schema": _CACHE_SCHEMA_VERSION,
            "content_hash": content_hash,
            "document_version_id": document_version_id,
            "provider": provider,
            "provider_version": provider_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _is_valid_structure(structure: Any) -> bool:
    """Return whether a cached result contains the hierarchy consumed below."""
    if not isinstance(structure, dict):
        return False
    if structure.get("status") != "success":
        return False
    nodes = structure.get("structure", [])
    if not nodes:
        return False
    top = nodes[0]
    return isinstance(top, dict) and bool(top.get("nodes"))


def _cache_entries_dir(cache_path: Path) -> Path:
    return cache_path.with_name(f"{cache_path.name}.entries")


def _cache_entry_path(cache_path: Path, key: str) -> Path:
    return _cache_entries_dir(cache_path) / f"{key}.json"


def _load_cache_entry(cache_path: Path, key: str) -> Optional[Dict[str, Any]]:
    if not cache_path.is_file():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
        return None
    if payload.get("layout") != "per-key-v1":
        return None

    entry_path = _cache_entry_path(cache_path, key)
    try:
        with entry_path.open("r", encoding="utf-8") as source:
            entry = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(entry, dict) or entry.get("cache_key") != key:
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else None


def _json_file_matches(target: Path, payload: Dict[str, Any]) -> bool:
    """Confirm that a concurrent publisher already achieved our exact state."""

    try:
        with target.open("r", encoding="utf-8") as source:
            existing = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return existing == payload


def _write_json_atomic(
    target: Path,
    payload: Dict[str, Any],
    *,
    provider: str,
) -> None:
    temporary = target.with_name(f".{uuid.uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8") as destination:
            json.dump(
                payload,
                destination,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
    except OSError:
        # Windows can reject one of two simultaneous replaces of the same
        # marker.  Treat the loser as successful only when the winner wrote
        # byte-semantically equivalent JSON; divergent state still fails
        # closed.
        if _json_file_matches(target, payload):
            return
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.CACHE_WRITE_FAILED",
            provider,
            "Knowledge-base cache could not be updated.",
        ) from None
    except (TypeError, ValueError):
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.CACHE_WRITE_FAILED",
            provider,
            "Knowledge-base cache could not be updated.",
        ) from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _save_cache_entry(
    cache_path: Path,
    key: str,
    result: Dict[str, Any],
    *,
    provider: str,
) -> None:
    _write_json_atomic(
        _cache_entry_path(cache_path, key),
        {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "cache_key": key,
            "result": result,
        },
        provider=provider,
    )
    _write_json_atomic(
        cache_path,
        {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "layout": "per-key-v1",
        },
        provider=provider,
    )


def get_kb_structure(
    force_reindex: bool = False,
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    cache_path: Optional[PathLike] = None,
    managed_cache_root: Optional[PathLike] = None,
) -> Dict[str, Any]:
    """Get the KB hierarchy through the configured StructureIndex provider."""
    runtime = resolve_document_parse_runtime(
        app_settings=app_settings,
        config_file=config_file,
        environ=environ,
        structure_index=structure_index,
        cache_path=cache_path,
        managed_cache_root=managed_cache_root,
    )
    provider, provider_version = _provider_identity(runtime.structure_index)
    if provider == "disabled":
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.STRUCTURE_INDEX_DISABLED",
            provider,
            "Knowledge-base structure indexing is disabled.",
        )

    kb_path = Path(KB_DOC_PATH)
    try:
        content_hash = _sha256_file(kb_path)
    except OSError:
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.DOCUMENT_UNAVAILABLE",
            provider,
            "Knowledge-base document is unavailable.",
        ) from None

    document_version_id = f"document-parse-kb:{content_hash}"
    key = _cache_key(
        document_version_id=document_version_id,
        content_hash=content_hash,
        provider=provider,
        provider_version=provider_version,
    )
    if not force_reindex:
        cached = _load_cache_entry(runtime.cache_path, key)
        if _is_valid_structure(cached):
            return cached

    try:
        indexed = runtime.structure_index.index(
            StructureIndexRequest(
                document_version_id=document_version_id,
                content_hash=content_hash,
                source_path=str(kb_path),
                media_type="text/markdown",
            )
        )
    except Exception:
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.PROVIDER_UNAVAILABLE",
            provider,
            "Knowledge-base structure index is unavailable.",
        ) from None

    if indexed.status == "blocked":
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.PROVIDER_UNAVAILABLE",
            indexed.provider or provider,
            "Knowledge-base structure index is unavailable.",
        )
    if indexed.status != "success":
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.INDEX_FAILED",
            indexed.provider or provider,
            "Knowledge-base structure indexing failed.",
        )

    try:
        current_provider, current_provider_version = _provider_identity(
            runtime.structure_index
        )
    except KnowledgeBaseUnavailable:
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.PROVIDER_IDENTITY_CHANGED",
            provider,
            "Knowledge-base provider identity changed during indexing.",
        ) from None
    if (current_provider, current_provider_version) != (
        provider,
        provider_version,
    ):
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.PROVIDER_IDENTITY_CHANGED",
            provider,
            "Knowledge-base provider identity changed during indexing.",
        )

    result = {
        "status": "success",
        "engine": _safe_provider_name(indexed.provider or provider),
        "doc_name": kb_path.name,
        "doc_id": indexed.external_ref,
        "structure": list(indexed.structure),
        "structure_json_path": indexed.external_ref,
        "elapsed_seconds": 0.0,
    }
    if not _is_valid_structure(result):
        raise KnowledgeBaseUnavailable(
            "DOCUMENT_PARSE.KB.INVALID_STRUCTURE",
            indexed.provider or provider,
            "Knowledge-base structure index returned an invalid hierarchy.",
        )

    _save_cache_entry(runtime.cache_path, key, result, provider=provider)
    return result


def _get_filename(path: str) -> str:
    """basename 不含扩展名。"""
    return os.path.splitext(os.path.basename(path))[0]


def _find_nodes_by_title(
    index_result: Dict[str, Any],
    keyword: str,
) -> List[Dict[str, Any]]:
    """Traverse a StructureIndex result without depending on a concrete adapter."""
    pattern = re.compile(keyword, re.IGNORECASE)
    matches: List[Dict[str, Any]] = []

    def traverse(nodes: Any) -> None:
        if not isinstance(nodes, (list, tuple)):
            return
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            title = str(node.get("title", "") or "")
            if pattern.search(title):
                matches.append(
                    {
                        "title": node.get("title"),
                        "node_id": node.get("node_id"),
                        "start_index": node.get("start_index"),
                        "end_index": node.get("end_index"),
                        "summary": node.get("summary"),
                    }
                )
            traverse(node.get("nodes"))

    traverse(index_result.get("structure", []))
    return matches


def _score_category(
    category: str,
    triggers: Dict[str, List[str]],
    text: str,
    filename: str,
    matched_nodes: List[Dict[str, Any]],
) -> int:
    """单类别打分(越高越匹配)。

    Negative signal: 文件名命中黑名单前缀 → 业务类别降权,"其他"除外
    (因为"其他"本身就是 fallback,该类别本来就该匹配这些前缀)。
    """
    score = 0

    # 文件名前缀(强信号,+30,只算一次)
    for kw in triggers["filename_keywords"]:
        if kw in filename:
            score += 30
            break

    # KB 节点匹配数(+20 / +10)
    if len(matched_nodes) >= 2:
        score += 20
    elif len(matched_nodes) == 1:
        score += 10

    # 正文关键词(弱信号,+5,只算一次)
    for kw in triggers["content_keywords"]:
        if kw in text:
            score += 5
            break

    # Negative signal: 命中黑名单前缀 → 业务类别直接 -100,"其他"除外
    if category != "其他":
        for neg in NEGATIVE_FILENAME_SIGNALS:
            if neg in filename:
                score -= 100
                break

    return score


def query_kb(
    raw_data: Dict[str, Any],
    source_path: str = "",
    force_reindex: bool = False,
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    cache_path: Optional[PathLike] = None,
    managed_cache_root: Optional[PathLike] = None,
    diagnostics: Optional[List[Dict[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Query the KB, returning None when the fallback chain should continue."""
    try:
        kb_structure = get_kb_structure(
            force_reindex=force_reindex,
            app_settings=app_settings,
            config_file=config_file,
            environ=environ,
            structure_index=structure_index,
            cache_path=cache_path,
            managed_cache_root=managed_cache_root,
        )
    except KnowledgeBaseUnavailable as exc:
        if diagnostics is not None:
            diagnostics.append(exc.diagnostic())
        return None
    except Exception:
        if diagnostics is not None:
            diagnostics.append(
                KnowledgeBaseUnavailable(
                    "DOCUMENT_PARSE.KB.UNEXPECTED",
                    getattr(structure_index, "name", "unknown"),
                    "Knowledge-base lookup is unavailable.",
                ).diagnostic()
            )
        return None

    text = raw_data.get("raw_text", "") or ""
    filename = _get_filename(source_path) if source_path else ""


    # 对每个类别评分
    scored: List[Tuple[int, str, List[Dict[str, Any]]]] = []
    for category, triggers in CATEGORY_TRIGGERS.items():
        matched = []
        seen = set()
        for nk in triggers["node_keywords"]:
            for n in _find_nodes_by_title(kb_structure, nk):
                nid = n.get("node_id")
                if nid not in seen:
                    seen.add(nid)
                    matched.append(n)
        score = _score_category(category, triggers, text, filename, matched)
        scored.append((score, category, matched))

    # 排序取最高
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_category, best_nodes = scored[0]

    if best_score <= 0:
        return None  # 没正分,不返回(避免"其他"乱入)

    if best_score >= 30:
        confidence = "high"
    elif best_score >= 15:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "category": best_category,
        "extracted_fields": {},
        "confidence": confidence,
        "rule_source": "knowledge_base",
        "matched_nodes": [n.get("title") for n in best_nodes],
        "score": best_score,
    }


_CACHE_ENTRY_FILENAME = re.compile(r"^(?P<key>[0-9a-f]{64})\.json$")


def _read_owned_cache_json(path: Path, artifact_name: str) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DocumentParseCacheLayoutError(
            f"{artifact_name} must be a regular managed file"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise DocumentParseCacheLayoutError(
            f"{artifact_name} is not valid managed JSON"
        ) from None
    if not isinstance(payload, dict):
        raise DocumentParseCacheLayoutError(
            f"{artifact_name} must contain a JSON object"
        )
    return payload


def _verify_cache_layout(
    cache_path: Path,
) -> tuple[bool, bool, List[Path]]:
    entries_dir = _cache_entries_dir(cache_path)
    marker_present = cache_path.exists() or cache_path.is_symlink()
    entries_present = entries_dir.exists() or entries_dir.is_symlink()
    if not marker_present:
        if entries_present:
            raise DocumentParseCacheLayoutError(
                "cache marker is required before cache entries can be removed"
            )
        return False, False, []

    marker = _read_owned_cache_json(cache_path, "cache marker")
    if marker != {
        "schema_version": _CACHE_SCHEMA_VERSION,
        "layout": "per-key-v1",
    }:
        raise DocumentParseCacheLayoutError(
            "cache marker has an unrecognized schema or layout"
        )

    if not entries_present:
        return True, False, []
    if entries_dir.is_symlink() or not entries_dir.is_dir():
        raise DocumentParseCacheLayoutError(
            "cache entry directory must be a regular managed directory"
        )
    try:
        candidates = sorted(entries_dir.iterdir(), key=lambda path: path.name)
    except OSError:
        raise DocumentParseCacheLayoutError(
            "cache entry directory could not be verified"
        ) from None

    verified: List[Path] = []
    for entry_path in candidates:
        match = _CACHE_ENTRY_FILENAME.fullmatch(entry_path.name)
        if match is None:
            raise DocumentParseCacheLayoutError(
                "cache entry has an unrecognized filename"
            )
        entry = _read_owned_cache_json(entry_path, "cache entry")
        key = match.group("key")
        if set(entry) != {"schema_version", "cache_key", "result"}:
            raise DocumentParseCacheLayoutError(
                "cache entry has an unrecognized schema"
            )
        if (
            entry.get("schema_version") != _CACHE_SCHEMA_VERSION
            or entry.get("cache_key") != key
            or not _is_valid_structure(entry.get("result"))
        ):
            raise DocumentParseCacheLayoutError(
                "cache entry failed ownership validation"
            )
        verified.append(entry_path)
    return True, True, verified


def clear_cache(
    *,
    app_settings: Optional[AppSettings] = None,
    config_file: Optional[PathLike] = None,
    environ: Optional[Mapping[str, str]] = None,
    structure_index: Optional[StructureIndex] = None,
    cache_path: Optional[PathLike] = None,
    managed_cache_root: Optional[PathLike] = None,
) -> None:
    """Remove only cache artifacts whose marker and entries are verified."""
    del structure_index  # retained for backward-compatible call signatures
    _, resolved_cache_path = resolve_document_parse_cache_path(
        app_settings=app_settings,
        config_file=config_file,
        environ=environ,
        cache_path=cache_path,
        managed_cache_root=managed_cache_root,
    )
    marker_present, entries_present, entries = _verify_cache_layout(
        resolved_cache_path
    )
    if not marker_present:
        return

    for entry_path in entries:
        entry_path.unlink()
    if entries_present:
        _cache_entries_dir(resolved_cache_path).rmdir()
    resolved_cache_path.unlink()
