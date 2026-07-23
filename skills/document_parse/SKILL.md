---
name: document_parse
description: |
  文档解析 skill。把 docx/xlsx/pdf/wps/url 等异构输入源解析为结构化数据,
  应用业务规则(关键词分类 / 字段抽取),输出 document_parse.v1 + schema 校验。

  Use when:
  - 上层调用方请求"解析这份文件/这个 URL"
  - 需要从异构文档(docx/xlsx/wps/url)提取结构化业务字段
  - 批量处理多个异构文件,需要统一 schema 输出

  Don't use for:
  - 文件归档决策(应该归到哪个项目目录) → archive_files skill
  - 项目账本写入 → ledger skill
  - 调 CloudCC/CRM → crm skill

  Output: document_parse.v1 (含 extracted_data + business_judgement + validation)
  Input: file_path or url + parse_intent
---

# Document Parse Skill

## Core Concept

```
1 input source (file_path | url)
  → 1 ParseExecutor (docx | xlsx | pdf | wps | url)
  → 3-layer rule application (knowledge_base | hard_code | llm)
  → 1 DocumentParseResult (document_parse.v1)
```

**关键不变量**:
- **Executor 纯技术**:只负责打开文件、提取原始数据;不 import `business_rules` / `business_knowledge`
- **Skill 纯业务**:只负责路由、规则应用、schema 校验;不直接 `import docx` / `import openpyxl`
- **Schema 稳定**: `document_parse.v1` 字段语义版本化(未来 v2 向后兼容)

## Structured Output(中间产物契约)

### document_parse.v1 — 解析结果

```json
{
  "schema_version": "document_parse.v1",
  "run_id": "run-2026-07-23-001",
  "status": "success | needs_review | blocked",
  "source_type": "file | url",
  "source_path": "/abs/path/file.docx | https://example.com/page.html",
  "file_type": ".docx | .xlsx | .pdf | .wps | .html",
  "executor_used": "docx | xlsx | pdf | wps | url",
  "parse_intent": "structured_business_fields | raw_content | metadata_only",
  "extracted_data": {
    "paragraphs": ["..."],
    "tables": [[["..."]]],
    "metadata": {"author": "...", "created": "...", "modified": "..."},
    "raw_text": "..."
  },
  "business_judgement": {
    "category": "招标公告 | 投标文件 | 合同文件 | 报名材料 | 其他",
    "extracted_fields": {
      "project_name": "...",
      "bid_deadline": "...",
      "amount": "..."
    },
    "confidence": "high | medium | low",
    "rule_source": "knowledge_base | hard_code | llm"
  },
  "validation": {
    "schema_valid": true,
    "required_fields_present": true,
    "missing_fields": [],
    "warnings": []
  },
  "blockers": [],
  "reason": "success | knowledge_base_no_match | knowledge_base_low_confidence | executor_not_implemented | human_review_required | parse_error | source_missing | schema_invalid",
  "knowledge_base_used": false,
  "executor_implementation_status": "implemented | stub",
  "elapsed_seconds": 1.23
}
```

### executor_input.v1 / executor_output.v1 — executor 契约(单一能力)

详见 [`integrations/executors/base.py`](../../integrations/executors/base.py) `ParseExecutor`。

```python
class ParseExecutor(ABC):
    @abstractmethod
    def can_handle(self, source: str) -> bool: ...
    
    @abstractmethod
    def extract(self, source: str) -> Dict[str, Any]: ...
    
    @abstractmethod
    def get_supported_types(self) -> List[str]: ...
```

## High-Level Workflow

```
Step 1: 接收 input (file_path | url + parse_intent)
Step 2: 路由 — 判断 source 类型 → 选 executor (docx | xlsx | pdf | wps | url)
Step 3: executor.extract(source) → 原始数据 (raw_data)
Step 4: 应用业务规则 (3-layer fallback)
   L1: 知识库匹配 (business_rules/document_parse/*.md)
   L2: 硬编码 fallback (简单正则)
   L3: LLM 提取 (last resort, common/llm_adapter)
Step 5: schema 校验 (document_parse.v1)
Step 6: 返回 DocumentParseResult
```

## Three-Layer Fallback

| Layer | 触发条件 | 实现位置 | 失败后果 |
|---|---|---|---|
| **L1: Knowledge Base** | 默认 | `business_rules/document_parse/*.md` | 降级 L2 |
| **L2: Hard Code** | KB 不匹配 / 缺失 | skill 内部正则(尽量少,逐步迁 L1) | 降级 L3 |
| **L3: LLM** | L1+L2 都失败 | `common.llm_adapter` | 标记 `needs_review` |

**关键不变量**:
- 业务规则文档化在 `business_rules/document_parse/`,代码不写死规则
- 硬编码 fallback 只在 L2,且**逐步迁到 L1**
- LLM 调用有 token 预算上限 + timeout(后续版本加,当前无限)

## Critical Requirements

1. **Executor 边界硬约束**:executor 内**禁止** import `business_rules` / `business_knowledge`
2. **Skill 边界硬约束**:skill 内**禁止** 直接 `import docx` / `import openpyxl`(只能通过 executor)
3. **Schema 校验硬约束**:输出前必须校验 `document_parse.v1`;失败 → `status=blocked, reason=schema_invalid`
4. **URL 安全**:抓取前校验 URL 合法性(http/https only),timeout ≤ 30s
5. **失败处理**:executor 抛 `NotImplementedError` → 返回 `status=blocked + reason=executor_not_implemented`(不抛异常到调用方)

## Other Capabilities Consumption

| 能力 | 用途 |
|---|---|
| `integrations/executors/*` | 本 skill 的依赖(自身定义) |
| `ocr.engine` (Vision / RapidOCR / EasyOCR / Tesseract) | 扫描页 fallback(后续通过 pdf_executor 内部路径) |
| `common.llm_adapter` | LLM 提取 fallback |
| `business_rules/document_parse/*.md` | 业务规则源(类似 archive_files 知识库) |
| `archive_files skill` | **下游消费者**(解析 → 归档决策) |
| `data_cleaning_tools` | **兼容层**(旧代码通过 adapter 模式调本 skill) |

## Implementation Layer

- `integrations/executors/` — ParseExecutor 基类 + 4 个实现
  - `base.py` — 抽象基类 (`can_handle` / `extract` / `get_supported_types`)
  - `docx_executor.py` — **实现**(python-docx,paragraphs + tables + metadata)
  - `xlsx_executor.py` — **实现**(openpyxl,sheets + rows)
  - `pdf_executor.py` — **接口契约**(NotImplementedError + 详细 TODO)
  - `url_executor.py` — **接口契约**(NotImplementedError + 详细 TODO)
  - `wps_executor.py` — **接口契约**(NotImplementedError + 详细 TODO)
- `skills/document_parse/` — skill 入口
  - `SKILL.md` — 本文档
  - `schemas/document_parse.v1.json` — JSON Schema(jsonschema 库可选)
  - `router.py` — 路由(source 类型 → executor)
  - `parse.py` — 主入口(接收 input → 调 executor → 应用规则 → 校验 → 输出)
  - `validator.py` — schema 校验(手写 fallback + jsonschema 可选)

## Known Limitations

- **url_executor**:暂未实现,调用返回 `status=blocked + reason=executor_not_implemented`(待装 `bs4` / `html2text`,见 TODO)
- **wps_executor**:暂未实现,调用返回 `status=blocked + reason=executor_not_implemented`(待选型 `antiword` / `wps2text` / `wps→docx`,见 TODO)
- **pdf_executor**:暂未实现(暂复用 `data_cleaning_tools.extract_pdf`,后续迁移到本 skill)
- **LLM fallback 预算**:未配置,默认无限(后续加 budget)
- **业务规则文档**:第一版只迁 3 个核心规则(关键词分类 / 字段模式 / 业务分类),其余硬编码保留在 `data_cleaning_tools` 旧路径(adapter 兼容)

## Iteration Log

### v0.1.0 (2026-07-23)

- 初始版本:skill 框架 + 4 executor 接口(2 实现 + 2 stub)+ 3-layer fallback + schema 校验
- docx/xlsx executor 从 `data_cleaning_tools` 拆分(adapter 模式保留旧签名,225 现有测试不变)
- url/wps/pdf executor 留接口契约,实现为 NotImplementedError + 清晰 TODO
- 业务规则首版从硬编码迁出 3 个核心规则到 `business_rules/document_parse/`
- 集成测试覆盖 docx + xlsx 端到端,url/wps 验证 stub 返回 blocked
- Known Limitations 标出未实现项
