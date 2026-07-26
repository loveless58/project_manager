# Business Judgement Usable Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先交付可实际运行的业务判断闭环，使原生可读文件完成多存储来源解析、业务候选检索、LLM 结构化裁决、人工复核和无副作用归档计划；OCR 识别放在该里程碑之后。

**Architecture:** 保留现有 `DataCleaningTools`、`StructureIndex`、复核队列和确认门，增加 StorageBinding 路由、受控业务上下文 Provider、RetrievalService、DocumentInterpretationService 和 ArchiveIntent。第一里程碑不扫描全部业务目录、不写权威账本、不执行真实归档；扫描件由显式 disabled OCR provider 返回 `blocked`，不触发 EasyOCR。

**Tech Stack:** Python 3.13、dataclasses、Protocol、pytest/unittest、requests、PyMuPDF、python-docx、openpyxl、现有 PageIndex StructureIndex 端口。

## Global Constraints

- SynologyDrive 只是一个 `StorageBinding`，不存在全局唯一业务根或默认归档目标。
- 运行工作区、缓存、LLM 响应和运行产物必须位于节点本地非同步目录。
- 首个里程碑处理原生可读 PDF、DOCX、XLSX、Markdown、XML；扫描件显式 `blocked`。
- 不安装、不调用 EasyOCR、PaddleOCR、RapidOCR、MinerU 或 OFD 转换器。
- PageIndex 只处理 RetrievalService 已筛选的长文档，不对完整业务绑定做全量索引。
- LLM 输出必须通过 `candidate_document_interpretation.v1`，携带证据、置信度、模型和策略版本。
- 不调用 `confirmed=True`，不移动、覆盖、重命名或删除业务文件。
- 兼容现有 `business_root`、三字段 `DocumentRef` 构造和 ToolRegistry 工具名称。
- 每个任务执行红灯测试、最小实现、绿灯测试、独立提交。

## Scope Boundary

本计划交付设计文档第 15 节第 1–6 项。LibreOffice `.doc`、MinerU、统一 OCR Registry 和 PaddleOCR/RapidOCR Provider 分属后续独立计划，避免输入能力阻塞业务闭环。

---

### Task 1: 多 StorageBinding 与只读路由

**Files:**
- Create: `platform_core/storage_bindings.py`
- Create: `integrations/document_store/router.py`
- Modify: `platform_core/models.py:7-14`
- Modify: `platform_core/settings.py:14-36,205-390`
- Modify: `platform_core/__init__.py`
- Modify: `integrations/document_store/__init__.py`
- Modify: `app_bootstrap/composition.py:35-92`
- Modify: `config/project-manager.example.json`
- Test: `tests/platform_core/test_storage_bindings.py`
- Test: `tests/integrations/test_document_store_router.py`
- Test: `tests/platform_core/test_settings.py`

**Interfaces:** Produces `StorageBinding`, `StorageBindingRegistry`, `DocumentStoreRouter`, `DocumentRef.binding_id` and `AppSettings.storage_bindings`; consumes existing `LocalDocumentStore`.

- [ ] **Step 1: Write failing compatibility and ambiguity tests**

```python
def test_legacy_root_becomes_one_binding(tmp_path):
    settings = load_app_settings(
        config_file="",
        environ={"PROJECT_MANAGER_BUSINESS_ROOT": str(tmp_path / "business")},
    )
    assert [item.binding_id for item in settings.storage_bindings] == ["legacy-business-root"]


def test_nested_roots_are_ambiguous(tmp_path):
    registry = StorageBindingRegistry([
        StorageBinding("shared", "local", "node", "business://shared/", tmp_path / "shared", ("source",), True, False),
        StorageBinding("incoming", "local", "node", "business://incoming/", tmp_path / "shared" / "incoming", ("source",), True, False),
    ])
    with pytest.raises(AmbiguousStorageBindingError):
        registry.document_ref_from_path(tmp_path / "shared" / "incoming" / "a.md")
```

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/platform_core/test_storage_bindings.py tests/platform_core/test_settings.py -q`
Expected: FAIL because binding models do not exist.

- [ ] **Step 3: Implement compatible models and resolution**

```python
@dataclass(frozen=True)
class DocumentRef:
    storage_provider: str
    object_key: str
    logical_uri: str
    binding_id: str = ""

@dataclass(frozen=True)
class StorageBinding:
    binding_id: str
    provider: str
    node_id: str
    logical_root: str
    physical_root: Path
    roles: tuple[str, ...]
    readable: bool
    writable: bool
    enabled: bool = True
```

`document_ref_from_path` rejects zero matches with `StorageBindingNotFoundError` and multiple matches with `AmbiguousStorageBindingError`. Only when explicit bindings are absent, convert `business_root` to `legacy-business-root`.

- [ ] **Step 4: Implement router and composition**

`DocumentStoreRouter.stat/open_read` require non-empty `binding_id` and route through `stores_by_binding`. Add registry/router to `RuntimeAdapters` while retaining `document_store` compatibility. Build one local store per enabled local binding.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/platform_core/test_storage_bindings.py tests/integrations/test_document_store_router.py tests/platform_core/test_settings.py tests/integrations/test_local_document_store.py tests/platform_core/test_composition.py -q`
Expected: PASS, including existing positional `DocumentRef` tests.

```bash
git add platform_core integrations/document_store app_bootstrap/composition.py config/project-manager.example.json tests/platform_core/test_storage_bindings.py tests/integrations/test_document_store_router.py tests/platform_core/test_settings.py
git commit -m "feat: add multi-storage document routing"
```

---

### Task 2: 发票专用语义与统一分类

**Files:**
- Create: `contracts/invoice_schema.py`
- Create: `business_rules/invoice_fields.py`
- Modify: `tools/data_cleaning_tools.py:653-759,881-926`
- Modify: `business_rules/archive_decision.py:10-160`
- Modify: `business_rules/semantic_document.py:13-104`
- Test: `tests/test_invoice_business_semantics.py`
- Test: `tests/test_archive_decision.py`
- Test: `tests/test_data_cleaning_ocr_provider.py`

**Interfaces:** Produces `extract_invoice_fields(text) -> dict` and `_extract_fields_for_document(text, document_type) -> dict`.

- [ ] **Step 1: Write the false-positive regression**

```python
def test_invoice_item_is_not_business_project(tmp_path):
    text = "电子发票\n购买方名称：合成甲方有限公司\n销售方名称：合成乙方有限公司\n项目名称 规格型号\n技术服务 标准版"
    fields = DataCleaningTools(workspace_dir=str(tmp_path))._extract_fields_for_document(text, "发票")
    assert "project_name" not in fields
    assert "deadline" not in fields
    assert fields["buyer"]["name"] == "合成甲方有限公司"
    assert fields["line_items"][0]["item_name"] == "技术服务"
```

Add a test proving project-governance Markdown has the same `DocumentClassification` during parsing and archive planning.

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/test_invoice_business_semantics.py tests/test_archive_decision.py -q`
Expected: FAIL because generic regex emits `project_name`.

- [ ] **Step 3: Implement invoice-only fields**

Allow invoice number/date, buyer, seller, untaxed amount, tax, total and line items. Never emit `project_name`, `project_code`, `deadline`, `bid_status` or `lifecycle_stage`. Classify before extracting fields.

- [ ] **Step 4: Unify classification**

Use one payload: `document_type`, `business_domain`, `project_phase`, `archive_phase`, `confidence`, `evidence`, `requires_review`. `evaluate_archive_decision` consumes it and does not independently reclassify governance files.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/test_invoice_business_semantics.py tests/test_archive_decision.py tests/test_data_cleaning_ocr_provider.py -q`
Expected: PASS.

```bash
git add contracts/invoice_schema.py business_rules/invoice_fields.py business_rules/archive_decision.py business_rules/semantic_document.py tools/data_cleaning_tools.py tests/test_invoice_business_semantics.py tests/test_archive_decision.py tests/test_data_cleaning_ocr_provider.py
git commit -m "fix: separate invoice items from project facts"
```

---

### Task 3: 受控业务上下文与 RetrievalService

**Files:**
- Create: `platform_core/ports/business_context.py`
- Create: `integrations/business_context/json_provider.py`
- Create: `services/retrieval_service.py`
- Modify: `platform_core/models.py`
- Modify: `platform_core/ports/__init__.py`
- Test: `tests/services/test_retrieval_service.py`
- Test: `tests/integrations/test_json_business_context_provider.py`

**Interfaces:** Produces `BusinessContextQuery`, `BusinessContextEvidence`, `BusinessContextProvider.search(query)` and `RetrievalService.find_business_candidates(query)`.

- [ ] **Step 1: Write candidate-first tests**

```python
class StaticContext:
    def __init__(self, records): self.records = records
    def search(self, query): return list(self.records)

class RecordingIndex:
    name = "recording"
    def __init__(self): self.requests = []
    def probe(self): return CapabilityReport("ready", self.name, "test-v1", "ready")
    def index(self, request):
        self.requests.append(request)
        return StructureIndexResult("success", self.name, "recording://1", (), "", "")

def test_no_candidate_means_no_pageindex():
    index = RecordingIndex()
    query = BusinessContextQuery("发票", {"buyer": {"tax_id": "9131"}}, ())
    result = RetrievalService(StaticContext([]), index).find_business_candidates(query)
    assert result.status == "needs_review"
    assert index.requests == []
```

Add one matching contract test: return the candidate first, then index only its declared 30-page document.

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/services/test_retrieval_service.py tests/integrations/test_json_business_context_provider.py -q`
Expected: FAIL because interfaces are absent.

- [ ] **Step 3: Implement strict context loading**

Load one explicitly supplied `business_context_catalog.v1`. Reject duplicate IDs, unknown fields, missing parties and paths outside configured bindings with `BUSINESS_CONTEXT.CATALOG_INVALID`. Never recursively discover context.

- [ ] **Step 4: Implement retrieval**

```python
@dataclass(frozen=True)
class BusinessContextQuery:
    document_type: str
    candidate_fields: Mapping[str, Any]
    text_segments: Sequence[Mapping[str, str]]

@dataclass(frozen=True)
class BusinessContextEvidence:
    status: str
    candidates: Sequence[Mapping[str, Any]]
    evidence_refs: Sequence[Mapping[str, Any]]
    conflicts: Sequence[Mapping[str, Any]]
    diagnostics: Sequence[Mapping[str, Any]]
```

Score exact tax ID, normalized legal name, contract/project code, amount, date, then weak path hints. Index only top three candidate documents of at least 10 pages or explicit `requires_structure_index=true`.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/services/test_retrieval_service.py tests/integrations/test_json_business_context_provider.py tests/integrations/test_pageindex_structure_index.py tests/test_pageindex_error_contract.py -q`
Expected: PASS.

```bash
git add platform_core/models.py platform_core/ports integrations/business_context services tests/services/test_retrieval_service.py tests/integrations/test_json_business_context_provider.py
git commit -m "feat: add evidence-based business retrieval"
```

---

### Task 4: LLM 结构化业务裁决

**Files:**
- Create: `contracts/document_interpretation.py`
- Create: `platform_core/ports/document_interpreter.py`
- Create: `integrations/llm/openai_compatible_interpreter.py`
- Create: `services/document_interpretation.py`
- Modify: `common/provider_config.py`
- Test: `tests/contracts/test_document_interpretation.py`
- Test: `tests/services/test_document_interpretation.py`
- Test: `tests/integrations/test_openai_compatible_interpreter.py`

**Interfaces:** Produces `DocumentInterpreter.complete_json(request)` and `DocumentInterpretationService.interpret(evidence_pack)`.

- [ ] **Step 1: Write malformed and valid-schema tests**

```python
class MalformedInterpreter:
    name = "malformed"
    model = "synthetic-model"
    def complete_json(self, request): return {"unexpected": "shape"}

def test_malformed_response_blocks_and_keeps_parse_ref():
    service = DocumentInterpretationService(StaticRetrieval(), MalformedInterpreter())
    result = service.interpret({
        "parse_artifact_ref": "artifact:parsed:1",
        "document_type_hint": "发票",
        "candidate_fields": {},
        "text_segments": [],
    })
    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "DOCUMENT_INTERPRETATION.SCHEMA_INVALID"
    assert result["parse_artifact_ref"] == "artifact:parsed:1"
```

`StaticRetrieval.find_business_candidates` returns an empty `BusinessContextEvidence`. Add a valid invoice-contract response test with evidence, confidence, model, prompt and policy versions.

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/contracts/test_document_interpretation.py tests/services/test_document_interpretation.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement fail-closed schema**

Reject duplicate keys, unknown fields, NaN/infinity, confidence outside 0–1, missing evidence and versions. Valid statuses: `success`, `needs_review`, `blocked`. Non-empty relations remain at least `needs_review` without SQL authority.

- [ ] **Step 4: Implement OpenAI-compatible adapter**

Reuse `resolve_llm_base_url`, `LLM_API_KEY`, `LLM_MODEL`; timeout 60 seconds, temperature 0.1, JSON-only system prompt. Redact keys, headers, paths and unbounded output.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/contracts/test_document_interpretation.py tests/services/test_document_interpretation.py tests/integrations/test_openai_compatible_interpreter.py tests/test_final_fix_llm_error_redaction.py -q`
Expected: PASS.

```bash
git add contracts/document_interpretation.py platform_core/ports common/provider_config.py integrations/llm services/document_interpretation.py tests/contracts/test_document_interpretation.py tests/services/test_document_interpretation.py tests/integrations/test_openai_compatible_interpreter.py
git commit -m "feat: add structured document interpretation"
```

---

### Task 5: 归档意图与运行包集成

**Files:**
- Create: `contracts/archive_intent.py`
- Create: `services/archive_targets.py`
- Modify: `tools/data_cleaning_tools.py:189-209,1049-1159,1322-1576,2360-2462`
- Modify: `business_rules/archive_decision.py`
- Modify: `app_bootstrap/composition.py`
- Modify: `main.py:129-181,286-302`
- Test: `tests/test_business_judgement_run.py`
- Test: `tests/test_archive_execution_gate.py`
- Test: `tests/test_central_runtime.py`

**Interfaces:** Produces `archive_intent.v1`, `candidate_interpretations.json`, `archive_intents.json` and compatible `archive_actions`.

- [ ] **Step 1: Write integrated preparation tests**

Build temporary binding/router, static context and schema-valid fake interpreter. Assert:

```python
result = tools.prepare_file_organization_run([str(source)])
assert result["candidate_interpretations"][0]["business_relation"]["candidate_contract_id"] == "C-001"
assert result["archive_intents"][0]["destination_status"] == "unresolved"
assert result["archive_actions"][0]["status"] == "needs_review"
assert Path(result["artifacts"]["candidate_interpretations"]).is_file()
assert not list(tmp_path.rglob("archive_result.json"))
```

Add nested-binding input and assert `STORAGE_BINDING.AMBIGUOUS` before parsing.

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/test_business_judgement_run.py tests/test_archive_execution_gate.py -q`
Expected: FAIL because interpretation/intent artifacts are absent.

- [ ] **Step 3: Implement independent ArchiveIntent**

```python
@dataclass(frozen=True)
class ArchiveIntent:
    source_ref: DocumentRef
    destination_status: str
    candidate_target_binding_ids: tuple[str, ...]
    project_id: str
    archive_phase: str
    content_hash: str
```

Explicit target must be enabled, writable, role `archive_target` and unique. Otherwise keep `unresolved`. Never default to source or SynologyDrive.

- [ ] **Step 4: Integrate in fixed order**

Extend `DataCleaningTools` with keyword-only registry/router/retrieval/interpretation/resolver; retain existing arguments. Per document: resolve ref → native parse → evidence → retrieval → LLM → intent → normalized review → stop. Pure rules may provide hints but cannot set ready relation/target.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/test_business_judgement_run.py tests/test_archive_execution_gate.py tests/test_central_runtime.py tests/test_loop_package.py tests/test_agent_roles.py -q`
Expected: PASS; `confirmed=False` has no side effect.

```bash
git add contracts/archive_intent.py services/archive_targets.py tools/data_cleaning_tools.py business_rules/archive_decision.py app_bootstrap/composition.py main.py tests/test_business_judgement_run.py tests/test_archive_execution_gate.py tests/test_central_runtime.py
git commit -m "feat: gate archive plans on business interpretation"
```

---

### Task 6: 复核与反馈契约修复

**Files:**
- Modify: `contracts/review_queue_schema.py:21-110`
- Modify: `contracts/feedback_form_schema.py:8-65`
- Modify: `tools/data_cleaning_tools.py:1577-1851,2419-2462`
- Test: `tests/test_review_queue.py`
- Test: `tests/test_feedback_form.py`
- Test: `tests/test_feedback_loop.py`
- Test: `tests/test_adversarial_verification.py`

**Interfaces:** Produces one normalized `review_queue.v2` and complete `feedback_form.v1`.

- [ ] **Step 1: Write both regressions**

```python
def test_adversarial_error_is_actionable():
    item = normalize_review_queue("run-1", [{"type": "adversarial_verification_error"}])["items"][0]
    assert item["id"] and item["question"]
    assert item["allowed_decisions"] == ["retry_verification", "defer", "accept_risk"]

def test_feedback_reads_overall_verdict():
    form = build_feedback_form(
        run_id="run-1", review_queue={"items": []}, audit_review={},
        adversarial_verification={"overall_verdict": "needs_human_review"},
    )
    assert form["verification_verdict"] == "needs_human_review"
```

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/test_review_queue.py tests/test_feedback_form.py -q`
Expected: FAIL.

- [ ] **Step 3: Normalize exactly once**

Collect extraction, relation, target and verification findings, then call `normalize_review_queue` once. Add explicit defaults for `adversarial_verification_error`, `business_relation_review` and `archive_target_review`.

- [ ] **Step 4: Fix verdict precedence**

Use `verification_verdict or overall_verdict or status or ""`. Copy evidence refs, conflicts, candidate IDs, DocumentRef and destination status without secrets.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/test_review_queue.py tests/test_feedback_form.py tests/test_feedback_loop.py tests/test_adversarial_verification.py tests/adversarial_verification -q`
Expected: PASS with no blank item.

```bash
git add contracts/review_queue_schema.py contracts/feedback_form_schema.py tools/data_cleaning_tools.py tests/test_review_queue.py tests/test_feedback_form.py tests/test_feedback_loop.py tests/test_adversarial_verification.py
git commit -m "fix: normalize business review feedback"
```

---

### Task 7: 安全 CLI 与禁用扫描识别

**Files:**
- Create: `ocr/providers/disabled_provider.py`
- Create: `scripts/prepare_business_file_run.py`
- Create: `docs/operations/business-file-judgement-quickstart.md`
- Modify: `ocr/providers/__init__.py`
- Modify: `skills/data_cleaning_file_organization.md`
- Modify: `README.md`
- Test: `tests/scripts/test_prepare_business_file_run.py`
- Test: `tests/test_data_cleaning_ocr_provider.py`

**Interfaces:** CLI consumes explicit config/context/source binding/optional target/files and produces reviewable artifacts without movement.

- [ ] **Step 1: Write CLI and no-EasyOCR tests**

```python
def test_scan_blocks_without_easyocr(tmp_path, monkeypatch):
    monkeypatch.setattr(
        DataCleaningTools, "_ocr_with_easyocr",
        lambda *args: (_ for _ in ()).throw(AssertionError("EasyOCR called")),
    )
    scan = create_image_only_pdf(tmp_path / "scan.pdf")
    tools = DataCleaningTools(
        workspace_dir=str(tmp_path / "runtime"),
        ocr_adapter=DisabledOcrProvider().extract,
    )
    result = tools.prepare_file_organization_run([str(scan)])
    assert result["failures"][0]["blocked_reason"] == "OCR.CAPABILITY_DISABLED"
```

Define `create_image_only_pdf` with PyMuPDF in the test. CLI test writes two bindings and one contract under `tmp_path`, injects fake LLM, asserts exit 0 and no archive result.

- [ ] **Step 2: Run red tests**

Run: `python -X utf8 -B -m pytest tests/scripts/test_prepare_business_file_run.py tests/test_data_cleaning_ocr_provider.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement safe CLI**

Command:

```text
python -X utf8 -B scripts/prepare_business_file_run.py --config <config> --context <catalog> --source-binding <id> [--target-binding <id>] <file> [<file> ...]
```

Build explicit settings/adapters, inject Disabled OCR, prepare/verify/audit/build feedback, and call `execute_archive_plan(run_id, confirmed=False)` exactly once. Never auto-apply review. Exit 0 for success/needs_review, 2 blocked, 1 unexpected failure; print redacted JSON.

- [ ] **Step 4: Write quickstart**

Include Windows/macOS bindings, catalog, LLM environment, command, artifact inspection, scan-blocked warning and no-physical-archive warning.

- [ ] **Step 5: Verify and commit**

Run: `python -X utf8 -B -m pytest tests/scripts/test_prepare_business_file_run.py tests/test_data_cleaning_ocr_provider.py tests/test_feedback_form.py tests/test_loop_package.py -q`
Run: `python -X utf8 -B governance/validate.py tools`
Expected: both exit 0.

```bash
git add ocr/providers/disabled_provider.py ocr/providers/__init__.py scripts/prepare_business_file_run.py docs/operations/business-file-judgement-quickstart.md skills/data_cleaning_file_organization.md README.md tests/scripts/test_prepare_business_file_run.py tests/test_data_cleaning_ocr_provider.py
git commit -m "feat: add safe business file judgement cli"
```

---

### Task 8: 只读业务可用性回归

**Files:**
- Create: `tests/integration/test_business_judgement_usable_slice.py`
- Create: `tests/fixtures/business_context_catalog.v1.json`
- Modify: `docs/operations/business-file-judgement-quickstart.md`
- Modify: `reports/2026-07-25-platform-foundation-review-fixes.md`

**Interfaces:** Consumes Tasks 1–7 and produces reproducible milestone evidence.

- [ ] **Step 1: Build realistic synthetic inputs**

Create native-text invoice PDF, contract DOCX, governance Markdown, project XLSX, XML and scanned PDF in temporary bindings. Record hashes. Fake only network LLM/PageIndex subprocess; use real parsers and artifact writers.

- [ ] **Step 2: Assert the milestone**

```python
assert hashes_after == hashes_before
assert native_statuses == {
    "invoice": "needs_review", "contract": "needs_review",
    "markdown": "needs_review", "xlsx": "needs_review", "xml": "needs_review",
}
assert scan_result["blocked_reason"] == "OCR.CAPABILITY_DISABLED"
assert invoice_interpretation["business_relation"]["candidate_contract_id"] == "C-001"
assert "project_name" not in invoice_parse["fields"]
assert all(item["id"] and item["question"] for item in review_queue["items"])
assert not list(runtime_workspace.rglob("archive_result.json"))
```

- [ ] **Step 3: Run integration verification**

Run: `python -X utf8 -B -m pytest tests/integration/test_business_judgement_usable_slice.py -q`
Expected: PASS for five native files, one blocked scan, unchanged hashes and no archive result.

- [ ] **Step 4: Run full verification**

Run: `python -X utf8 -B -m pytest -q`
Run: `python -X utf8 -B governance/validate.py all`
Run: `git diff --check`
Expected: all exit 0; record exact tests and existing skips.

- [ ] **Step 5: Update evidence and commit**

Report exact counts, artifact names and “OCR recognition providers were not installed or invoked in this milestone.”

```bash
git add tests/integration/test_business_judgement_usable_slice.py tests/fixtures/business_context_catalog.v1.json docs/operations/business-file-judgement-quickstart.md reports/2026-07-25-platform-foundation-review-fixes.md
git commit -m "test: verify business judgement usable slice"
```

---

## Completion Gate

Fresh verification must prove unique binding resolution, invoice semantic isolation, retrieval-before-PageIndex, validated interpretation or explicit block, independent source/target, complete review artifacts, safe CLI, scan blocking without EasyOCR, and passing full tests/governance.
