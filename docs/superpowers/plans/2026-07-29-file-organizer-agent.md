# 文件整理 Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个可由 Claude Code/Codex 调用的文件整理 Agent：解析用户明确选择的文件，输出整理建议，在确认后归档并记录 SQLite 位置历史。

**Architecture:** `agents/file_organizer` 只负责工作流与用户确认；`skills` 负责原生解析、OCR、业务查询和归档执行；SQLite 保存最小业务事实；PageIndex 是长文档章节证据的可选增强。原生解析优先，扫描件的声明式 OCR Provider Chain 固定为 RapidOCR → MinerU → EasyOCR；任一可选能力不可用时只降级当前文件。

**Tech Stack:** Python 3.13、SQLite、PyMuPDF、python-docx、openpyxl、RapidOCR、MinerU、EasyOCR、外部 PageIndex CLI。

## Global Constraints

- 所有源文件必须由调用者显式提供，禁止递归扫描业务根目录。
- 原文件、归档目录和运行工作区必须经 `StorageBinding` 配置；运行工件与 SQLite 必须位于节点本地非同步目录。
- 原生解析始终优先于 OCR；OCR Provider 不隐式安装、不隐式下载模型、不隐式外发内容。
- OCR 优先级为 RapidOCR → MinerU → EasyOCR；不可用/失败仅返回结构化降级结果。
- PageIndex 是可选长文档索引；不可用时不得阻塞解析、建议或归档。
- Agent 只归档用户确认的条目；禁止覆盖和删除；执行后必须回读并更新位置历史。
- `LoopEngine`、Agent Gateway、两阶段 Agent 响应文件和旧 `prepare_business_file_run.py` 不是本工作流依赖。
- 新生产行为必须先有失败测试，再写最小实现；所有测试使用临时目录和合成文件，不提交客户业务文件。

---

## File Structure

```text
agents/file_organizer/
  AGENT.md                         # Host 可读的 Agent 角色、工作流与确认规则
  __init__.py
  agent.py                          # FileOrganizerAgent：prepare / execute_confirmed

contracts/
  structured_document.py            # structured_document.v1、组织建议与确认输入的严格契约

skills/file_organizer/
  __init__.py
  document_parse.py                 # 复用现有原生解析并转为 structured_document.v1
  ocr.py                            # RapidOCR → MinerU → EasyOCR Provider Chain
  business_query.py                 # SQLite 项目/文件匹配与 PageIndex 证据查询
  archive.py                        # 已确认动作的校验、移动、回读、位置更新

ocr/providers/mineru_provider.py    # MinerU CLI Provider；不绑定本机路径
ocr/providers/rapidocr_provider.py  # 同时支持当前 RapidOCR 包与旧兼容包
ocr/provider_chain.py               # 显式 Provider 声明、探测、优先级与结果归一化
requirements-ocr.txt                # 可选 OCR 安装声明

infrastructure/file_organizer/
  __init__.py
  repository.py                     # SQLite Repository：项目、文件、位置、运行与条目

migrations/sqlite/0001_file_organization.sql
                                   # 首个生产业务 schema
scripts/run_file_organizer.py       # 受控 CLI：prepare、review、execute-confirmed
config/file-organizer.example.json  # 节点本地配置样例

tests/agents/test_file_organizer_agent.py
tests/contracts/test_structured_document.py
tests/ocr/test_provider_chain.py
tests/ocr/test_mineru_provider.py
tests/infrastructure/test_file_organizer_repository.py
tests/skills/file_organizer/test_document_parse.py
tests/skills/file_organizer/test_business_query.py
tests/skills/file_organizer/test_archive.py
tests/scripts/test_run_file_organizer.py
tests/integration/test_file_organizer_realistic_slice.py
```

## Task 1: 建立统一文档契约与 OCR Provider Chain

**Files:**
- Create: `contracts/structured_document.py`
- Create: `ocr/provider_chain.py`
- Create: `ocr/providers/mineru_provider.py`
- Create: `requirements-ocr.txt`
- Modify: `ocr/providers/__init__.py`
- Modify: `ocr/providers/rapidocr_provider.py`
- Test: `tests/contracts/test_structured_document.py`
- Test: `tests/ocr/test_provider_chain.py`
- Test: `tests/ocr/test_mineru_provider.py`

**Interfaces:**
- Produces `StructuredDocument`, `OcrAttempt`, `OrganizationProposal`, `validate_structured_document(payload)` from `contracts.structured_document`.
- Produces `OcrProviderChain(providers).extract(path) -> dict[str, object]` from `ocr.provider_chain`.
- Produces `MineruProvider(command: str | None = None).extract(path) -> dict[str, object]` from `ocr.providers.mineru_provider`.
- Later tasks consume only normalized `structured_document.v1`; they do not inspect provider-specific output.

- [ ] **Step 1: Write the failing structured-document contract tests**

```python
def test_contract_accepts_native_parse_result_with_bounded_text():
    payload = build_structured_document(
        source_ref=SOURCE_REF,
        content_hash="a" * 64,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        parser="native_docx",
        text="合同正文",
        pages=[],
        tables=[],
        fields={"contract_code": "SYN-002"},
    )
    assert validate_structured_document(payload)["schema_version"] == "structured_document.v1"


def test_contract_rejects_text_segment_larger_than_utf8_limit():
    with pytest.raises(StructuredDocumentError):
        build_structured_document(
            source_ref=SOURCE_REF, content_hash="a" * 64,
            media_type="text/plain", parser="native_text",
            text="中" * 2_000, pages=[], tables=[], fields={},
        )
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/contracts/test_structured_document.py -q`  
Expected: FAIL because `contracts.structured_document` does not exist.

- [ ] **Step 3: Implement the minimal strict contract**

```python
def build_structured_document(*, source_ref, content_hash, media_type, parser, text, pages, tables, fields):
    bounded_text = truncate_utf8(text, max_bytes=4096)
    payload = {
        "schema_version": "structured_document.v1",
        "source_ref": source_ref,
        "content_hash": content_hash,
        "media_type": media_type,
        "status": "success",
        "parser": parser,
        "text": bounded_text,
        "pages": pages,
        "tables": tables,
        "fields": fields,
    }
    return validate_structured_document(payload)
```

Implement `truncate_utf8` using encoded-byte slicing that removes incomplete trailing UTF-8 bytes; never append an ellipsis beyond the byte limit.

- [ ] **Step 4: Run the contract tests and verify GREEN**

Run: `python -X utf8 -B -m pytest tests/contracts/test_structured_document.py -q`  
Expected: PASS.

- [ ] **Step 5: Write failing Provider Chain tests**

```python
def test_chain_uses_rapidocr_before_mineru_and_easyocr(tmp_path):
    attempts = []
    chain = OcrProviderChain([
        FakeProvider("rapidocr", attempts, "blocked"),
        FakeProvider("mineru", attempts, "success"),
        FakeProvider("easyocr", attempts, "success"),
    ])
    result = chain.extract(str(tmp_path / "scan.pdf"))
    assert attempts == ["rapidocr", "mineru"]
    assert result["provider"] == "mineru"


def test_chain_returns_needs_review_when_all_declared_providers_are_unavailable(tmp_path):
    result = OcrProviderChain([
        FakeProvider("rapidocr", [], "blocked"),
        FakeProvider("mineru", [], "blocked"),
        FakeProvider("easyocr", [], "blocked"),
    ]).extract(str(tmp_path / "scan.pdf"))
    assert result["status"] == "needs_review"
    assert result["reason"] == "OCR.PROVIDERS_UNAVAILABLE"
```

- [ ] **Step 6: Run Provider Chain tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/ocr/test_provider_chain.py -q`  
Expected: FAIL because `OcrProviderChain` does not exist.

- [ ] **Step 7: Implement declared Provider Chain and MinerU Provider**

```python
class OcrProviderChain:
    def __init__(self, providers):
        self.providers = tuple(providers)

    def extract(self, path):
        attempts = []
        for provider in self.providers:
            result = provider.extract(path)
            attempts.append({"provider": provider.name, "status": result["status"]})
            if result["status"] == "success":
                return {**result, "attempts": attempts}
        return {"status": "needs_review", "reason": "OCR.PROVIDERS_UNAVAILABLE", "attempts": attempts}
```

`MineruProvider` must resolve only an explicit command argument or `PROJECT_MANAGER_MINERU_COMMAND`; it runs `mineru -p <input> -o <temporary-node-local-output> -b pipeline`, reads MinerU JSON/Markdown output, and normalizes it. It returns a structured blocked result when the command, model, output, or input is unavailable. It never invokes a network endpoint or performs a package/model installation.

Update `RapidOcrProvider` to prefer modern `rapidocr` plus `onnxruntime`, while retaining `rapidocr_onnxruntime` as a backward-compatible probe. Add `requirements-ocr.txt` with separate commented groups for `rapidocr`, `onnxruntime`, `mineru[all]`, and `easyocr`; do not add these heavyweight packages to mandatory `requirements.txt`.

- [ ] **Step 8: Run OCR tests and verify GREEN**

Run: `python -X utf8 -B -m pytest tests/ocr/test_provider_chain.py tests/ocr/test_mineru_provider.py tests/test_rapidocr_provider.py tests/test_ocr_provider_registry.py -q`  
Expected: PASS; tests must use fake executable/output fixtures and must not download OCR models.

- [ ] **Step 9: Commit Task 1**

```bash
git add contracts/structured_document.py ocr requirements-ocr.txt tests/contracts/test_structured_document.py tests/ocr/test_provider_chain.py tests/ocr/test_mineru_provider.py
git commit -m "feat: add structured document OCR provider chain"
```

## Task 2: 实现 SQLite 文件整理业务 Repository

**Files:**
- Create: `migrations/sqlite/0001_file_organization.sql`
- Create: `infrastructure/file_organizer/__init__.py`
- Create: `infrastructure/file_organizer/repository.py`
- Test: `tests/infrastructure/test_file_organizer_repository.py`
- Modify: `README.md`

**Interfaces:**
- Consumes `SqliteUnitOfWork` and `structured_document.v1` payloads from Task 1.
- Produces `FileOrganizationRepository(connection)` with `upsert_document`, `record_location`, `find_project_candidates`, `create_run`, `record_item`, `confirm_item`, and `complete_archive`.
- Later tasks write no SQL directly; they use this Repository inside a write/read Unit of Work.

- [ ] **Step 1: Write failing repository persistence tests**

```python
def test_repository_records_document_current_location_and_history(database_path):
    migrate(database_path)
    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        document_id = repository.upsert_document(STRUCTURED_DOCUMENT)
        repository.record_location(document_id, SOURCE_LOCATION, current=True)
        repository.record_location(document_id, ARCHIVE_LOCATION, current=True)
        uow.commit()
    with SqliteUnitOfWork(database_path) as uow:
        history = FileOrganizationRepository(uow.connection).locations_for(document_id)
    assert [entry["logical_uri"] for entry in history] == [SOURCE_URI, ARCHIVE_URI]
    assert history[-1]["is_current"] is True


def test_repository_keeps_project_link_as_candidate_until_confirmed(database_path):
    migrate(database_path)
    with SqliteUnitOfWork(database_path, mode="write") as uow:
        repository = FileOrganizationRepository(uow.connection)
        synthetic_project_ref = repository.create_project("合成项目 A", "SYN-001")
        document_id = repository.upsert_document(STRUCTURED_DOCUMENT)
        link_id = repository.link_document_to_project(document_id, synthetic_project_ref, "candidate", "project_code", "medium")
        repository.confirm_project_link(link_id)
        uow.commit()
    assert read_link_state(database_path, link_id) == "confirmed"
```

- [ ] **Step 2: Run repository tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/infrastructure/test_file_organizer_repository.py -q`  
Expected: FAIL because the migration and `FileOrganizationRepository` do not exist.

- [ ] **Step 3: Add the first production migration and Repository**

Create tables `projects`, `documents`, `document_locations`, `document_project_links`, `organization_runs`, and `organization_items`. Add unique indexes for `documents.content_hash`, one current location per document, and idempotent `(run_id, document_id)` organization items. Use TEXT primary keys generated by Repository, ISO-8601 UTC timestamps, CHECK constraints for `candidate|confirmed` and `pending|confirmed|skipped|executed|failed` states, and foreign keys.

```python
class FileOrganizationRepository:
    def upsert_document(self, structured_document: dict) -> str: ...
    def record_location(self, document_id: str, location: dict, *, current: bool) -> str: ...
    def create_project(self, name: str, project_code: str | None = None) -> str: ...
    def find_project_candidates(self, fields: dict) -> list[dict]: ...
    def create_run(self, goal: str) -> str: ...
    def record_item(self, run_id: str, document_id: str, proposal: dict) -> str: ...
    def confirm_item(self, item_id: str, target_location: dict) -> None: ...
    def complete_archive(self, item_id: str, target_location: dict) -> None: ...
```

- [ ] **Step 4: Run repository and database regression tests and verify GREEN**

Run: `python -X utf8 -B -m pytest tests/infrastructure/test_file_organizer_repository.py tests/database -q`  
Expected: PASS.

- [ ] **Step 5: Document the SQLite authority boundary and commit Task 2**

Add a README section stating that SQLite is the first Agent's local authority for project/file/location/organization-run records; JSON and Markdown remain export or review views only.

```bash
git add migrations/sqlite/0001_file_organization.sql infrastructure/file_organizer tests/infrastructure/test_file_organizer_repository.py README.md
git commit -m "feat: persist file organization records in sqlite"
```

## Task 3: 实现解析、业务查询与 PageIndex Hook Skills

**Files:**
- Create: `skills/file_organizer/__init__.py`
- Create: `skills/file_organizer/document_parse.py`
- Create: `skills/file_organizer/business_query.py`
- Modify: `app_bootstrap/composition.py`
- Modify: `platform_core/settings.py`
- Modify: `config/project-manager.example.json`
- Test: `tests/skills/file_organizer/test_document_parse.py`
- Test: `tests/skills/file_organizer/test_business_query.py`

**Interfaces:**
- Consumes existing `DataCleaningTools.extract_document`, `OcrProviderChain`, `FileOrganizationRepository`, `StorageBindingRegistry`, and `StructureIndex` port.
- Produces `DocumentParseSkill.parse(source_path) -> structured_document.v1` and `BusinessQuerySkill.propose(structured_document, goal) -> organization_proposal.v1`.
- `BusinessQuerySkill` returns an empty `pageindex_evidence` collection if index capability is disabled or unavailable.

- [ ] **Step 1: Write failing parse-skill tests**

```python
def test_parse_skill_prefers_native_docx_parser(tmp_path, source_binding):
    source = create_docx(tmp_path / "contract.docx", "合同编号：SYN-002")
    result = DocumentParseSkill(native_parser=REAL_NATIVE_PARSER, ocr_chain=FORBIDDEN_OCR).parse(str(source))
    assert result["status"] == "success"
    assert result["parser"] == "native_docx"


def test_parse_skill_uses_provider_chain_for_scanned_pdf(tmp_path, source_binding):
    source = create_image_only_pdf(tmp_path / "scan.pdf")
    result = DocumentParseSkill(native_parser=REAL_NATIVE_PARSER, ocr_chain=SUCCESSFUL_OCR).parse(str(source))
    assert result["parser"] == "mineru"
```

- [ ] **Step 2: Run parse-skill tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/skills/file_organizer/test_document_parse.py -q`  
Expected: FAIL because `DocumentParseSkill` does not exist.

- [ ] **Step 3: Implement DocumentParseSkill**

Call the existing native parser first. Normalize DOCX/XLSX/PDF/Markdown/XML output into Task 1's contract. For image-only/blocked PDFs and image inputs, invoke `OcrProviderChain`; keep native parser status, provider attempts, page evidence and structured fields. The skill must preserve `source_ref` and calculate a SHA-256 content hash before parsing.

- [ ] **Step 4: Write failing business-query tests**

```python
def test_business_query_proposes_confirmed_project_code_match(repository, fake_index):
    repository.create_project("项目 A", "SYN-001")
    proposal = BusinessQuerySkill(repository, fake_index, pageindex_min_text_length=100).propose(
        structured_document(project_code="SYN-001", text="付款条款见第三章"),
        goal="按项目整理",
    )
    assert proposal["candidate_project"]["name"] == "项目 A"
    assert proposal["status"] == "needs_confirmation"
    assert proposal["pageindex_evidence"][0]["page"] == 3


def test_business_query_degrades_when_pageindex_is_unavailable(repository, blocked_index):
    proposal = BusinessQuerySkill(repository, blocked_index, pageindex_min_text_length=1).propose(
        structured_document(text="足够长的合同正文"), goal="按项目整理"
    )
    assert proposal["status"] == "needs_review"
    assert proposal["pageindex_evidence"] == []
    assert "PAGEINDEX.UNAVAILABLE" in proposal["reasons"]
```

- [ ] **Step 5: Run business-query tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/skills/file_organizer/test_business_query.py -q`  
Expected: FAIL because `BusinessQuerySkill` does not exist.

- [ ] **Step 6: Implement BusinessQuerySkill and optional PageIndex configuration**

Add `providers.pageindex_min_text_length` to the strict AppSettings schema, defaulting to `16000` UTF-8 bytes. `BusinessQuerySkill` must query SQLite first by project code, contract code, company name and prior confirmed relation. It creates a proposal with one of `needs_confirmation`, `needs_review`, or `unmatched`; it never writes a confirmed project link.

When text length crosses the threshold and media type is PDF/Markdown, call `StructureIndex.probe()` then `index()`. Convert only returned structure node title, page range and index reference into `pageindex_evidence`. On every unavailable/failed result, retain the proposal and append a stable reason; never raise an exception to the Agent.

- [ ] **Step 7: Run Skill tests and targeted PageIndex regressions and verify GREEN**

Run: `python -X utf8 -B -m pytest tests/skills/file_organizer/test_document_parse.py tests/skills/file_organizer/test_business_query.py tests/integrations/test_pageindex_structure_index.py tests/test_pageindex_error_contract.py -q`  
Expected: PASS.

- [ ] **Step 8: Commit Task 3**

```bash
git add skills/file_organizer app_bootstrap/composition.py platform_core/settings.py config/project-manager.example.json tests/skills/file_organizer
git commit -m "feat: add file organization parse and query skills"
```

## Task 4: 实现归档 Skill、文件整理 Agent 与 Host CLI

**Files:**
- Create: `skills/file_organizer/archive.py`
- Create: `agents/file_organizer/__init__.py`
- Create: `agents/file_organizer/agent.py`
- Create: `agents/file_organizer/AGENT.md`
- Create: `scripts/run_file_organizer.py`
- Create: `config/file-organizer.example.json`
- Test: `tests/skills/file_organizer/test_archive.py`
- Test: `tests/agents/test_file_organizer_agent.py`
- Test: `tests/scripts/test_run_file_organizer.py`

**Interfaces:**
- Consumes Task 1 structured documents, Task 2 Repository, and Task 3 Skills.
- Produces `FileOrganizerAgent.prepare(files, goal) -> organization_run.v1` and `FileOrganizerAgent.execute_confirmed(run_id, confirmations) -> archive_results.v1`.
- CLI exposes `prepare`, `review`, and `execute-confirmed`, with physical paths redacted from stdout.

- [ ] **Step 1: Write failing archive-skill tests**

```python
def test_archive_skill_moves_only_confirmed_item_and_updates_location(tmp_path, repository):
    source = write_text(tmp_path / "source" / "contract.md", "contract")
    target = tmp_path / "archive" / "项目A" / "合同文件" / "contract.md"
    item_id = create_confirmed_item(repository, source, target)
    result = ArchiveSkill(repository, registry).execute(item_id)
    assert result["status"] == "executed"
    assert not source.exists()
    assert target.read_text(encoding="utf-8") == "contract"
    assert repository.current_location_for_item(item_id)["logical_uri"] == "business://archive/项目A/合同文件/contract.md"


def test_archive_skill_rejects_unconfirmed_item_without_touching_source(tmp_path, repository):
    source = write_text(tmp_path / "source" / "contract.md", "contract")
    item_id = create_pending_item(repository, source)
    result = ArchiveSkill(repository, registry).execute(item_id)
    assert result == {"status": "blocked", "reason": "ARCHIVE.NOT_CONFIRMED"}
    assert source.exists()
```

- [ ] **Step 2: Run archive-skill tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/skills/file_organizer/test_archive.py -q`  
Expected: FAIL because `ArchiveSkill` does not exist.

- [ ] **Step 3: Implement ArchiveSkill**

The skill must resolve source and target through `StorageBindingRegistry`; verify content hash under a read handle, reject target existence, create only target directories within the archive binding, move with `os.replace` only after all checks, re-hash/read the target, then persist the location update and item state in one SQLite write Unit of Work. Any failure before move leaves the source untouched; any post-move database failure must surface `ARCHIVE.RECONCILIATION_REQUIRED` with both logical locations.

- [ ] **Step 4: Write failing Agent workflow tests**

```python
def test_agent_returns_one_proposal_per_explicit_file_and_continues_after_parse_failure(tmp_path, agent):
    good = create_docx(tmp_path / "good.docx", "项目编号：SYN-001")
    unreadable = tmp_path / "bad.pdf"
    unreadable.write_bytes(b"not a PDF")
    run = agent.prepare([str(good), str(unreadable)], goal="按项目整理")
    assert len(run["items"]) == 2
    assert run["items"][0]["proposal"]["status"] == "needs_confirmation"
    assert run["items"][1]["proposal"]["status"] == "needs_review"


def test_agent_executes_only_user_confirmed_items(tmp_path, agent):
    run = agent.prepare([str(create_docx(tmp_path / "a.docx", "合同"))], goal="按项目整理")
    result = agent.execute_confirmed(run["run_id"], [{"item_id": run["items"][0]["item_id"], "decision": "confirmed"}])
    assert result["items"][0]["status"] == "executed"
```

- [ ] **Step 5: Run Agent and CLI tests and verify RED**

Run: `python -X utf8 -B -m pytest tests/agents/test_file_organizer_agent.py tests/scripts/test_run_file_organizer.py -q`  
Expected: FAIL because `FileOrganizerAgent` and `run_file_organizer.py` do not exist.

- [ ] **Step 6: Implement Agent, host instructions and CLI**

`FileOrganizerAgent.prepare` validates explicit input paths, invokes `DocumentParseSkill` for each input, records documents and source locations, invokes `BusinessQuerySkill`, writes `organization_runs`/`organization_items`, and returns a host-readable proposal table. It must never require a configured external LLM to produce a deterministic initial proposal.

`FileOrganizerAgent.execute_confirmed` accepts only `confirmed`, `modified_target`, or `skipped` decisions. It persists confirmations before calling `ArchiveSkill`; it executes only `confirmed`/`modified_target` items and returns one result per requested item.

`agents/file_organizer/AGENT.md` tells Claude Code/Codex to call `prepare`, display the proposal table, ask for explicit confirmation, then call `execute-confirmed`. It explicitly prohibits using legacy Agent Gateway or moving unconfirmed items.

The CLI accepts a node-local config, a source binding, an archive binding, an explicit `--goal`, and explicit file paths. `prepare` writes node-local JSON/Markdown review output; `review` prints redacted proposals; `execute-confirmed` accepts a node-local confirmation JSON. It must not print physical paths or secrets.

- [ ] **Step 7: Run Agent, archive and CLI tests and verify GREEN**

Run: `python -X utf8 -B -m pytest tests/skills/file_organizer/test_archive.py tests/agents/test_file_organizer_agent.py tests/scripts/test_run_file_organizer.py -q`  
Expected: PASS.

- [ ] **Step 8: Commit Task 4**

```bash
git add agents/file_organizer skills/file_organizer/archive.py scripts/run_file_organizer.py config/file-organizer.example.json tests/skills/file_organizer/test_archive.py tests/agents/test_file_organizer_agent.py tests/scripts/test_run_file_organizer.py
git commit -m "feat: add confirmed file organizer agent workflow"
```

## Task 5: 端到端验证、运维文档与受控真实试运行

**Files:**
- Create: `docs/operations/file-organizer-agent-quickstart.md`
- Modify: `README.md`
- Test: `tests/integration/test_file_organizer_realistic_slice.py`
- Modify: `config/file-organizer.example.json`

**Interfaces:**
- Consumes the public Agent/CLI interfaces from Task 4.
- Produces a documented local-node workflow and a repeatable integration test using separate temporary source, archive and runtime roots.

- [ ] **Step 1: Write failing end-to-end integration test**

```python
def test_file_organizer_processes_native_and_scanned_documents_without_unconfirmed_moves(tmp_path):
    source_root, archive_root, runtime_root = make_node_roots(tmp_path)
    contract = create_docx(source_root / "contract.docx", "项目编号：SYN-001")
    scan = create_image_only_pdf(source_root / "scan.pdf")
    agent = configured_agent(source_root, archive_root, runtime_root, ocr_chain=FAKE_RAPIDOCR)

    prepared = agent.prepare([str(contract), str(scan)], goal="按项目整理")
    assert all(item["proposal"]["status"] in {"needs_confirmation", "needs_review"} for item in prepared["items"])
    assert contract.exists() and scan.exists()

    confirmed = choose_first_item(prepared)
    executed = agent.execute_confirmed(prepared["run_id"], [confirmed])
    assert executed["items"][0]["status"] == "executed"
    assert scan.exists()
```

- [ ] **Step 2: Run integration test and verify RED**

Run: `python -X utf8 -B -m pytest tests/integration/test_file_organizer_realistic_slice.py -q`  
Expected: FAIL until Tasks 1–4 are complete.

- [ ] **Step 3: Write the operator quickstart and config template**

Document exact installation commands:

```powershell
# 基础解析
python -m pip install -r requirements.txt

# 可选 OCR Provider；按节点能力选择，不要全部强制安装
python -m pip install -r requirements-ocr.txt

# 启用 PageIndex 前，在节点本地单独安装并配置 PageIndex
$env:PROJECT_MANAGER_PAGEINDEX_DIR = "C:\\Tools\\PageIndex"
```

Document the three runtime states: native-only, OCR-enabled, and PageIndex-enabled. Show `prepare` followed by explicit review/confirmation and `execute-confirmed`. State that real SynologyDrive runs require a local config whose runtime and SQLite folders are outside the synchronized root.

- [ ] **Step 4: Run full regression suite and verify GREEN**

Run: `python -X utf8 -B -m pytest -q`  
Expected: PASS with existing slow PageIndex tests skipped unless their explicit environment variables are set.

- [ ] **Step 5: Run a controlled real-file preparation only**

Create a node-local config outside every storage binding. Select 3–5 explicit non-OFD SynologyDrive files. Run only `scripts/run_file_organizer.py prepare`; inspect the proposal table and SQLite records. Do not execute confirmation or move a real file in this step.

Expected: every selected file has one proposal or a stable `needs_review` reason; no source file changes location or contents.

- [ ] **Step 6: Commit Task 5**

```bash
git add docs/operations/file-organizer-agent-quickstart.md README.md config/file-organizer.example.json tests/integration/test_file_organizer_realistic_slice.py
git commit -m "docs: add file organizer agent operations guide"
```

## Plan Self-Review

### Spec coverage

- 单个文件整理 Agent、Host 边界和显式确认：Task 4。
- 原生解析与统一结构化结果：Task 1、Task 3。
- RapidOCR → MinerU → EasyOCR 声明、优先级与降级：Task 1。
- SQLite 项目/文件/位置/关系/运行业务事实：Task 2。
- PageIndex 可选索引与降级：Task 3。
- 确认后归档、回读与位置历史：Task 4。
- 全量测试与真实文件仅准备试运行：Task 5。

### Placeholder scan

Plan steps包含明确文件、接口、测试命令、失败预期和最小实现轮廓；不依赖“后续再补”的未定义任务。

### Type consistency

Tasks 1–4 以 `structured_document.v1`、`FileOrganizationRepository`、`DocumentParseSkill`、`BusinessQuerySkill`、`ArchiveSkill` 和 `FileOrganizerAgent` 为唯一跨层接口；Task 5 只调用 Task 4 的公开 Agent/CLI 接口。
