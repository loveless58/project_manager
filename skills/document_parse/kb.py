"""document_parse 知识库(KB),基于 PageIndex + 文件名前缀双信号。

设计:
1. KB 文档(business_rules/document_parse/kb.md)按章节分类
2. PageIndex KB 索引(一次性,~3s):把 kb.md → structure.json
3. Query 时三信号评分:
   a. 文件名前缀(强信号): "合同"/"承诺函"/"授权委托"/"报名" 等 → +30
   b. KB 章节匹配(node 数): 多 node → +20, 单 node → +10
   c. 正文关键词(弱信号): +5
4. 取最高分类别,分数 ≥30 → high, ≥15 → medium, <15 → low
5. Cache:kb_cache.json(key=md_path + mtime)

详见 skills/document_parse/SKILL.md §Three-Layer Fallback。
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from integrations.pageindex.pageindex_client import PageIndexClient


_KB_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DOC_PATH = os.path.abspath(
    os.path.join(_KB_DIR, "..", "..", "business_rules", "document_parse", "kb.md")
)
KB_CACHE_PATH = os.path.join(_KB_DIR, "kb_cache.json")


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


def _cache_key(md_path: str) -> str:
    """cache key = abs path + mtime(nanosecond 浮点)+ size。

    用 mtime 浮点(保留小数,实际是微秒精度)+ 文件大小作为双重检测。
    防止: 1 秒内多次修改 .md 时 mtime 整数化撞 key(原 :.0f 漏洞)。
    """
    st = os.stat(md_path)
    return f"{os.path.abspath(md_path)}:m={st.st_mtime:.6f}:s={st.st_size}"


def _is_valid_structure(structure: Any) -> bool:
    """cache 完整性校验。

    检测: PageIndex 输出 schema 变了,或者 structure 损坏,
    返回 False 触发强制 reindex。
    """
    if not isinstance(structure, dict):
        return False
    if structure.get("status") != "success":
        return False
    nodes = structure.get("structure", [])
    if not nodes:
        return False
    # 至少要有一个顶层节点 + 它有子节点
    top = nodes[0]
    if not isinstance(top, dict):
        return False
    if not top.get("nodes"):
        return False
    return True


def _load_cache() -> Dict[str, Any]:
    if not os.path.exists(KB_CACHE_PATH):
        return {}
    try:
        with open(KB_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    with open(KB_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_kb_structure(force_reindex: bool = False) -> Dict[str, Any]:
    """获取 KB PageIndex structure,带 cache。

    第一次跑会调 PageIndex index_md(GPUStack LLM, ~3s),
    后续 cache 命中瞬时返回。

    Cache 失效条件(三选一):
    1. mtime + size 变化(.md 被改)
    2. force_reindex=True
    3. cache structure 校验失败(损坏 / schema 不匹配)
    """
    if not os.path.exists(KB_DOC_PATH):
        raise FileNotFoundError(f"KB 文档不存在: {KB_DOC_PATH}")

    cache = _load_cache()
    key = _cache_key(KB_DOC_PATH)

    if not force_reindex and key in cache:
        cached = cache[key]
        if _is_valid_structure(cached):
            return cached
        # cache 损坏 → 删 key,fall through 到下面的 PageIndexClient reindex 逻辑
        del cache[key]

    client = PageIndexClient()
    result = client.index_md(KB_DOC_PATH)

    if result.get("status") != "success":
        raise RuntimeError(f"KB PageIndex index 失败: {result.get('error')}")

    cache[key] = result
    _save_cache(cache)
    return result


def _get_filename(path: str) -> str:
    """basename 不含扩展名。"""
    return os.path.splitext(os.path.basename(path))[0]


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
) -> Optional[Dict[str, Any]]:
    """基于 raw_data + 文件名查 KB。

    Returns:
        business_judgement dict,或 None(KB 匹配不上,降级到 hard_code / LLM)。
    """
    try:
        kb_structure = get_kb_structure(force_reindex=force_reindex)
    except Exception:
        return None

    text = raw_data.get("raw_text", "") or ""
    filename = _get_filename(source_path) if source_path else ""

    client = PageIndexClient()

    # 对每个类别评分
    scored: List[Tuple[int, str, List[Dict[str, Any]]]] = []
    for category, triggers in CATEGORY_TRIGGERS.items():
        matched = []
        seen = set()
        for nk in triggers["node_keywords"]:
            for n in client.find_nodes_by_title(kb_structure, nk):
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


def clear_cache() -> None:
    """清 KB cache(下次 query 重新 index)。"""
    if os.path.exists(KB_CACHE_PATH):
        os.unlink(KB_CACHE_PATH)
