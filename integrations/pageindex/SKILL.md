---
name: pageindex
description: |
  PageIndex 通用客户端(fork of VectifyAI/PageIndex)。把 PDF / Markdown 文档生成
  层级化 tree 结构(TOC + 各章节 summary + page_index 范围),供下游 skill
  (ledger / bid_files / crm / Intent Router)消费做精细化决策。

  Use when:
  - 上游 skill 需要"这个 PDF 的章节结构 / 关键章节在第几页 / 文档摘要"
  - 跨页上下文检索(get_page_content 拉指定页文字)
  - 按章节标题定位(find_nodes_by_title 找节点)

  Don't use for:
  - 扫描件 PDF 文本提取(无文字层)→ 走 OCR (integrations/macos_vision_bridge)
  - 通用 OCR 文本提取(整篇文字流)→ 走 OCR
  - 项目归档决策 → 走 archive_files skill
  - 招标公告解析 → 走 bid_files skill

  Output: structure.json (含 doc_name + structure tree,每节点 title/node_id/start_index/end_index/summary)
  Input: pdf_path 或 md_path
---

# PageIndex Integration Skill

## Core Concept

```
1 PDF/MD file → PageIndex (LLM) → 1 structure.json
                                        │
                                        ├─ doc_name
                                        └─ structure (tree)
                                              │
                                              ├─ {title, node_id, start_index, end_index, summary, nodes}
                                              ├─ {title, node_id, start_index, end_index, summary, nodes}
                                              └─ ...
```

**关键不变量**:
- **schema 稳定**: `doc_name` + `structure` tree 是稳定契约
- **决策策略迭代**: LLM 行为可调参数(toc_check_pages / max_pages_per_node 等)

## Structured Output(中间产物契约)

### pageindex_result.v1 — 索引结果

```json
{
  "status": "success | failed",
  "engine": "pageindex",
  "doc_name": "2023-annual-report-truncated.pdf",
  "doc_id": "73e377c2-...",            // 自动生成 UUID
  "structure": [                       // tree 列表
    {
      "title": "Monetary Policy",
      "node_id": "0003",
      "start_index": 9,                // physical page (1-indexed)
      "end_index": 9,
      "summary": "...",
      "nodes": [...]                   // optional, 子节点
    }
  ],
  "structure_json_path": "/abs/path/results/xxx_structure.json",
  "elapsed_seconds": 65.29,
  "error": "..."                       // 仅 failed 时
}
```

**关键约束**:
- `start_index` / `end_index` 是 PDF 物理页码(1-indexed)
- `node_id` 是 zero-padded 字符串(`"0003"`),层内唯一
- `summary` 由 LLM 生成,长度取决于 `summary_token_threshold`
- 下游消费:`structure` 直接喂给 ledger / bid_files / crm 做精细化决策

## High-Level Workflow

```
[Caller] (Intent Router 路由过来 / 其他 skill 直接调)
   │
   ▼
[PageIndexClient]  (integrations/pageindex/pageindex_client.py)
   │
   ├─ index_pdf(pdf_path) ──────► subprocess.run(PageIndex .venv/bin/python + run_pageindex.py)
   │                              ↓
   │                            ./results/{pdf_name}_structure.json
   │                              ↓
   │                            load + 返回 pageindex_result.v1
   │
   ├─ get_page_content(pdf_path, "5-7") ──► PyPDF2 / PyMuPDF 读 PDF
   │                                          返回 [{"page":5, "content":"..."}, ...]
   │
   └─ find_nodes_by_title(index_result, keyword) ──► DFS 遍历 structure
                                                       返回匹配节点列表
```

## Three-Layer Query Pattern(数据驱动)

**优先级**(高到低):

| 层 | 工具 | 触发条件 | 失败处理 |
|---|---|---|---|
| 1. 结构化查询 | `find_nodes_by_title()` | 永远先尝试 | 无匹配 → 层 2 |
| 2. 页内容查询 | `get_page_content()` | 需要看具体内容 | PyPDF2 失败 → PyMuPDF → 返回空 |
| 3. 全文 OCR | OCR (Vision / RapidOCR) | 扫描件 PDF(无文字层) | 走 OCR fallback |

**注意**: 当前 `get_page_content` 对扫描件 PDF 返回空(无文字层)。后续增强: 检测到扫描件时自动 fallback 到 OCR (swift_ocr_bridge)。

## Critical Requirements(硬规则)

1. **不调 LLM 二次加工** — PageIndex 输出是 ground truth,下游直接消费
2. **不修改 PageIndex 仓库** — fork 复制,改动要 upstream PR 或本地记录
3. **subprocess 必须 cwd=PageIndex_dir** — 否则 `import pageindex` 失败 + results/ 相对路径错位
4. **structure.json 不动** — PageIndex 写到 ./results/,客户端只读不写
5. **timeout 默认 600s** — 50 页 PDF 约 55s,大文档按比例放大
6. **扫描件 PDF 不走 get_page_content** — 文字层为空,需先 OCR

## Other Capabilities Consumption

pageindex_result.v1 是稳定契约,以下 skill 可消费:

| 上游(产生 PDF/MD) | 下游(消费 pageindex_result) |
|---|---|
| archive_files (归档触发索引) | ledger skill (按章节写账本) |
| bid_files (招标文件索引) | crm skill (项目分类 / 客户识别) |
| Intent Router (路由决策) | extract_project_fields (按章节提取关键字段) |
| 外部调用方 (任意 skill) | human review UI (结构化浏览) |

**消费示例** (ledger skill):

```python
index = client.index_pdf("contract.pdf")
for node in index["structure"]:
    if "签约" in node["title"] or "签署" in node["title"]:
        # 拉这一章的具体内容
        pages_content = client.get_page_content("contract.pdf",
                                                f"{node['start_index']}-{node['end_index']}")
        full_text = "\n".join(p["content"] for p in pages_content)
        ledger.write_chapter_summary(
            project_name=extract_project_name(full_text),
            chapter_title=node["title"],
            chapter_text=full_text,
        )
```

## Implementation Layer

SKILL.md 描述契约 + 工作流;具体实现在 `pageindex_client.py`:

- `pageindex_client.py` — **核心**。导出 `PageIndexClient` 类,3 个核心方法:
  - `index_pdf(pdf_path, **kwargs)` — subprocess 调 PageIndex .venv/bin/python
  - `index_md(md_path, **kwargs)` — 同上 (Markdown 版本)
  - `get_page_content(pdf_path, pages)` — 纯 Python,PyPDF2 / PyMuPDF 读 PDF
  - `get_page_content_from_index(index_result, pages)` — 从 index_result 推断 pdf_path
  - `find_nodes_by_title(index_result, keyword)` — DFS 找节点

## Resources(按需加载)

- **[README.md](README.md)** — 安装 + 配置步骤
- **[pageindex_client.py](pageindex_client.py)** — 客户端源码(主实现)
- **PageIndex 仓库** — `/Users/zhang/Desktop/工作文件/PageIndex/`
  - `run_pageindex.py` — CLI 入口
  - `pageindex/page_index.py` — 核心 LLM 调用 + 安全加固
  - `pageindex/retrieve.py` — tool 函数(get_document / get_document_structure / get_page_content)

## Known Limitations

- **本地 PageIndex 仓库是 fork 复制**(git history 丢失,remote URL 未配)
  - 包含 prompt injection 加固独有改动(`_INJECTION_PATTERNS` / `_sanitize_doc_text` / `_wrap_doc_text` / `_SYSTEM_HARDENING` / `_secure_doc_text` / `_validate_physical_indices`)
  - 上游 VectifyAI/PageIndex 没有这些
- **扫描件 PDF get_page_content 返回空** — 文字层为空,需 OCR fallback(后续增强)
- **PyPDF2 confidence / 错误处理较简** — 文本型 PDF 足够,扫描件无效
- **LLM 调用 GPUStack**(`http://172.18.125.202:9990/v1`, model=`minimax-m3-mxfp8`)
  - 网络 / API 不通时整个 index_pdf 失败
- **structure.json 留在 PageIndex/results/** — 不清理,会累积(后续加 retention policy)

## Iteration Log

- **v0.1.0 (2026-07-23)**: 初版 — 通用化 PageIndex 客户端,3 个核心 API (index_pdf / index_md / get_page_content / find_nodes_by_title);subprocess + PyPDF2/PyMuPDF 双路径;实测 50 页 Federal Reserve PDF 65.29s 出 6 顶层节点
