# Document Management Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将项目收敛成一个可审核的 `DocumentManagementAgent` 工作流：按需加载 Skill，通过 Connector 读取明确文件，并写出 JSON/Markdown 审核产物及 SQLite 审计引用。

**Architecture:** 以 [三层架构规格](../specs/2026-07-30-document-management-agent-architecture.md) 为唯一基线。Agent 管目标、状态和门控；Skill 管解析、字段事实和审核交付；Connector 管文件、OCR、产物和 SQLite。现有 `file_organizer` 仅作为兼容实现来源，不再作为新主流程。

**Tech Stack:** Python 3.13、pytest、SQLite、`SqliteUnitOfWork`、`StorageBindingRegistry`、`FilesystemProjectionWriter`、节点可用的原生解析与 OCR Provider。

## Global Constraints

- 只处理显式输入；不扫描业务根目录。
- 源绑定只读；`prepare-review` 不得移动、改名、复制、删除或覆盖源文件。
- `results_root` 必须位于 Git 仓库和源业务目录之外。
- 不实现旧 `.doc`、项目归属、PageIndex 判断效果、PostgreSQL/群晖、真实归档或浏览器自动化。
- `document.json` 是事实来源；`review.md` 和 `run-summary.md` 只能由同次结构化结果渲染。
- 所有生产行为采用 TDD：先看到测试失败，再写最小实现，再跑目标与回归测试。
- 每个任务独立提交；不得提交真实文件、临时数据库、结果目录或凭据。

---

### Task 1: 建立工作流契约与状态

**Files:**
- Create: `contracts/agent_run.py`
- Create: `contracts/artifact_reference.py`
- Create: `contracts/document_result.py`
- Create: `agents/document_management/__init__.py`
- Create: `agents/document_management/state.py`
- Test: `tests/contracts/test_agent_run.py`
- Test: `tests/contracts/test_artifact_reference.py`
- Test: `tests/contracts/test_document_result.py`

**Interfaces:**
- `build_agent_run(*, run_id, goal, input_refs, status, artifacts) -> dict`
- `build_artifact_reference(*, kind, logical_uri, sha256, size_bytes) -> dict`
- `build_document_result(*, run, document_id, structured_document, facts, decision, artifacts) -> dict`
- `WorkflowState = RECEIVED, SCOPED, PROFILED, PARSED, FACTS_EXTRACTED, ARTIFACTS_PUBLISHED, AWAITING_REVIEW, COMPLETED`

- [ ] **Step 1: Write the failing tests**

```python
def test_document_result_preserves_parse_text_and_non_archive_decision():
    result = build_document_result(
        run={"run_id": "run-1", "goal": "解析", "status": "prepared"},
        document_id="doc-1",
        structured_document=success_document(text="正文"),
        facts={"document_type": {"value": "invoice", "confidence": 0.99, "evidence": []}},
        decision={"status": "needs_review", "suggested_action": "no_archive", "reasons": []},
        artifacts=[],
    )
    assert result["schema_version"] == "document_result.v1"
    assert result["parse"]["text"] == "正文"
    assert result["decision"]["suggested_action"] == "no_archive"
```

Add tests that non-`projection://` artifact URIs and unknown decision actions raise `ValueError`.

- [ ] **Step 2: Verify RED**

Run: `python -X utf8 -B -m pytest tests/contracts/test_agent_run.py tests/contracts/test_artifact_reference.py tests/contracts/test_document_result.py -q`

Expected: imports fail because the contract modules do not exist.

- [ ] **Step 3: Implement the minimal builders**

```python
def build_artifact_reference(*, kind, logical_uri, sha256, size_bytes):
    if kind not in {"document_json", "review_markdown", "run_json", "run_summary"}:
        raise ValueError("unsupported artifact kind")
    if not logical_uri.startswith("projection://"):
        raise ValueError("artifact logical URI must be projection scoped")
    return {
        "schema_version": "artifact_reference.v1",
        "kind": kind,
        "logical_uri": logical_uri,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }
```

Keep parsed text, pages and tables in `document_result.v1`; do not split them into an unversioned side object.

- [ ] **Step 4: Verify GREEN**

Run: `python -X utf8 -B -m pytest tests/contracts/test_agent_run.py tests/contracts/test_artifact_reference.py tests/contracts/test_document_result.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add document management workflow contracts`.

### Task 2: 实现 Artifact Store Connector

**Files:**
- Create: `connectors/artifacts/__init__.py`
- Create: `connectors/artifacts/run_artifact_store.py`
- Test: `tests/connectors/test_run_artifact_store.py`

**Interfaces:**
- `RunArtifactStore.publish_document(run, document_result) -> tuple[dict, dict]`
- `RunArtifactStore.publish_run(run, document_results) -> tuple[dict, dict]`
- Reuses `FilesystemProjectionWriter` and returns `artifact_reference.v1` objects.

- [ ] **Step 1: Write the failing tests**

```python
def test_publish_document_writes_run_scoped_json_and_markdown(tmp_path):
    store = RunArtifactStore(tmp_path, protected_roots=(tmp_path / "source",))
    json_ref, markdown_ref = store.publish_document(RUN, DOCUMENT_RESULT)
    assert (tmp_path / "runs/run-1/documents/doc-1/document.json").is_file()
    assert (tmp_path / "runs/run-1/documents/doc-1/review.md").is_file()
    assert json_ref["logical_uri"].startswith("projection://runs/run-1/")
    assert "源文件是否修改：否" in (
        tmp_path / "runs/run-1/documents/doc-1/review.md"
    ).read_text("utf-8")
```

Add tests that a protected source root is rejected and that a second write with different bytes to the same run/document/kind raises `ArtifactConflictError`.

- [ ] **Step 2: Verify RED**

Run: `python -X utf8 -B -m pytest tests/connectors/test_run_artifact_store.py -q`

Expected: import failure for `RunArtifactStore`.

- [ ] **Step 3: Implement over the existing projection writer**

```python
base = f"runs/{run['run_id']}/documents/{document_result['document']['document_id']}"
json_ref = writer.write(
    ProjectionRequest("json", f"{base}/document.json", canonical_json, "application/json")
)
markdown_ref = writer.write(
    ProjectionRequest("markdown", f"{base}/review.md", render_review(document_result), "text/markdown")
)
```

Canonical JSON uses `ensure_ascii=False`, sorted keys and a trailing newline. Markdown reads only `document_result.v1`. Convert each `ProjectionRef` into an artifact contract; never put a physical path in the output contract.

- [ ] **Step 4: Verify GREEN**

Run: `python -X utf8 -B -m pytest tests/connectors/test_run_artifact_store.py tests/integrations/test_filesystem_projection_writer.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add run artifact store connector`.

### Task 3: 实现 document_facts Skill（电子发票优先）

**Files:**
- Create: `skills/document_management/document_facts/SKILL.md`
- Create: `skills/document_management/document_facts/__init__.py`
- Create: `skills/document_management/document_facts/skill.py`
- Test: `tests/skills/document_management/test_document_facts.py`

**Interfaces:**
- `DocumentFactsSkill.extract(structured_document) -> dict[str, dict]`
- Field form: `{"value": object, "confidence": float | None, "evidence": list[dict]}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_invoice_skill_extracts_parties_amount_date_service_and_page_evidence():
    facts = DocumentFactsSkill().extract(invoice_document(INVOICE_TEXT))
    assert facts["document_type"]["value"] == "invoice"
    assert facts["invoice_number"]["value"] == "<synthetic-invoice-number>"
    assert facts["seller_name"]["value"] == "合成服务有限公司"
    assert facts["buyer_name"]["value"] == "合成采购股份有限公司"
    assert facts["total_amount"]["value"] == "48000.00"
    assert facts["service_description"]["value"] == "技术开发与服务"
    assert facts["seller_name"]["evidence"] == [
        {"page": 1, "text": "合成服务有限公司"}
    ]
```

Add a non-invoice test requiring only `document_type=unknown`, and a missing-amount test requiring `total_amount` to be absent rather than zero.

- [ ] **Step 2: Verify RED**

Run: `python -X utf8 -B -m pytest tests/skills/document_management/test_document_facts.py -q`

Expected: import failure for `DocumentFactsSkill`.

- [ ] **Step 3: Implement the Skill**

Normalize line text, identify an invoice only when an invoice marker and a number/tax/amount marker coexist, then extract labeled or adjacent values for invoice number, issue date, buyer/seller, taxpayer IDs, untaxed amount, tax amount, total amount, tax rate, service description and remarks. Evidence records original page and exact extracted text. Missing values stay absent; file names are never evidence.

`SKILL.md` states that the Skill is loaded only after parse text exists and returns candidates, not a project conclusion.

- [ ] **Step 4: Verify GREEN**

Run: `python -X utf8 -B -m pytest tests/skills/document_management/test_document_facts.py tests/skills/file_organizer/test_document_parse.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add invoice document facts skill`.

### Task 4: 声明 parse/review Skill 并记录产物引用

**Files:**
- Create: `skills/document_management/document_parse/SKILL.md`
- Create: `skills/document_management/document_parse/__init__.py`
- Create: `skills/document_management/document_parse/skill.py`
- Create: `skills/document_management/document_review/SKILL.md`
- Create: `skills/document_management/document_review/__init__.py`
- Create: `skills/document_management/document_review/skill.py`
- Create: `migrations/sqlite/0002_document_artifacts.sql`
- Modify: `infrastructure/file_organizer/repository.py`
- Test: `tests/skills/document_management/test_document_review.py`
- Test: `tests/infrastructure/test_file_organizer_artifacts.py`

**Interfaces:**
- `DocumentParseWorkflowSkill.parse(path, *, source_ref)` delegates to tested `skills.file_organizer.document_parse.DocumentParseSkill`.
- `DocumentReviewSkill.build(run, document_id, structured_document, facts) -> document_result.v1` always uses `suggested_action="no_archive"`.
- `FileOrganizationRepository.record_artifact(run_id, document_id, artifact) -> str`.
- `FileOrganizationRepository.artifacts_for_run(run_id) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_skill_keeps_parse_failure_reviewable_without_archive_target():
    result = DocumentReviewSkill().build(
        RUN, "doc-1", unavailable_document("NATIVE.UNSUPPORTED_MEDIA"), {}
    )
    assert result["parse"]["status"] == "needs_review"
    assert result["decision"] == {
        "status": "needs_review",
        "suggested_action": "no_archive",
        "reasons": ["NATIVE.UNSUPPORTED_MEDIA"],
    }

def test_repository_records_document_and_review_artifact_refs(database_path):
    repository, run_id, document_id = prepared_repository(database_path)
    repository.record_artifact(run_id, document_id, document_json_ref())
    repository.record_artifact(run_id, document_id, review_markdown_ref())
    assert [x["artifact_kind"] for x in repository.artifacts_for_run(run_id)] == [
        "document_json", "review_markdown"
    ]
```

Add a duplicate-kind test: idempotent only when URI and hash agree.

- [ ] **Step 2: Verify RED**

Run: `python -X utf8 -B -m pytest tests/skills/document_management/test_document_review.py tests/infrastructure/test_file_organizer_artifacts.py -q`

Expected: missing Skill/repository API or migration table failure.

- [ ] **Step 3: Implement minimal adapters and migration**

```sql
CREATE TABLE document_artifacts (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES organization_runs(id) ON DELETE RESTRICT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
    artifact_kind TEXT NOT NULL,
    logical_uri TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    created_at_utc TEXT NOT NULL,
    UNIQUE(run_id, document_id, artifact_kind)
);
```

The parse adapter only delegates. The review Skill only builds a contract and never consults projects, PageIndex or archive targets. Validate `artifact_reference.v1` before SQL insert. Store logical URI/hash/size only.

- [ ] **Step 4: Verify GREEN**

Run: `python -X utf8 -B -m pytest tests/skills/document_management/test_document_review.py tests/infrastructure/test_file_organizer_artifacts.py tests/database/test_sqlite_migrations.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add review skills and artifact audit`.

### Task 5: 实现单一 DocumentManagementAgent 工作流

**Files:**
- Create: `agents/document_management/AGENT.md`
- Create: `agents/document_management/catalog.py`
- Create: `agents/document_management/agent.py`
- Test: `tests/agents/test_document_management_agent.py`

**Interfaces:**
- `SkillCatalog.for_document(parsed) -> tuple[str, ...]`.
- `DocumentManagementAgent.prepare(files, *, goal) -> agent_run.v1`.
- Constructor receives database path, storage registry, parse/facts/review Skills, artifact store and source binding ID.

- [ ] **Step 1: Write the failing workflow tests**

```python
def test_agent_progressively_loads_parse_facts_review_and_publishes_artifacts(
    tmp_path, database_path
):
    agent, source = build_agent(tmp_path, database_path, parsed_invoice())
    result = agent.prepare([str(source)], goal="解析发票，不归档")
    assert result["status"] == "awaiting_review"
    assert result["loaded_skills"] == [
        "document_parse", "document_facts", "document_review"
    ]
    assert result["items"][0]["decision"]["suggested_action"] == "no_archive"
    assert source.read_text("utf-8") == "source sentinel"
    assert result["artifacts"]["run_summary"]["logical_uri"].startswith("projection://runs/")
```

Add a parse-failure test requiring only `document_parse` and `document_review`, and a non-invoice test requiring no invoice fact extraction while still publishing review artifacts.

- [ ] **Step 2: Verify RED**

Run: `python -X utf8 -B -m pytest tests/agents/test_document_management_agent.py -q`

Expected: import failure for `DocumentManagementAgent`.

- [ ] **Step 3: Implement explicit orchestration**

```python
parsed = self._parse_skill.parse(source_path, source_ref=source_ref)
document_id = repository.upsert_document(parsed)
skills = self._catalog.for_document(parsed)
facts = self._facts_skill.extract(parsed) if "document_facts" in skills else {}
result = self._review_skill.build(run, document_id, parsed, facts)
refs = self._artifact_store.publish_document(run, result)
```

Create the run first. For every explicit file, persist document/location/item and publish a result even if parsing fails. Record artifact references, then publish `run.json` and `run-summary.md`. Do not instantiate `BusinessQuerySkill`, `ArchiveSkill` or `PageIndexStructureIndex` in this workflow.

- [ ] **Step 4: Verify GREEN and compatibility**

Run: `python -X utf8 -B -m pytest tests/agents/test_document_management_agent.py tests/agents/test_file_organizer_agent.py tests/agents/test_file_organizer_parse_failure.py -q`

Expected: all pass; legacy archive behavior stays unchanged.

- [ ] **Step 5: Commit**

Commit message: `feat: add document management agent workflow`.

### Task 6: 配置、CLI 与安全验收

**Files:**
- Modify: `config/file-organizer.example.json`
- Modify: `scripts/run_file_organizer.py`
- Create: `tests/scripts/test_run_document_management_agent.py`
- Modify: `docs/operations/file-organizer-agent-quickstart.md`

**Interfaces:**
- Required config key: `results_root`.
- New CLI command: `prepare-review --goal <goal> <files...>`.
- Existing `prepare` and `execute-confirmed` remain legacy compatibility commands until callers migrate.

- [ ] **Step 1: Write the failing CLI test**

```python
def test_prepare_review_requires_results_root_and_never_changes_source(tmp_path):
    missing = node_config(tmp_path, include_results_root=False)
    assert main(["--config", str(missing), "prepare-review", "--goal", "解析", str(source)]) == 2
    config = node_config(tmp_path, include_results_root=True)
    assert main(["--config", str(config), "prepare-review", "--goal", "解析", str(source)]) == 0
    assert (tmp_path / "results" / "runs").exists()
    assert source.read_bytes() == original
```

- [ ] **Step 2: Verify RED**

Run: `python -X utf8 -B -m pytest tests/scripts/test_run_document_management_agent.py -q`

Expected: unknown command or ignored `results_root`.

- [ ] **Step 3: Compose only through layers**

Validate `results_root`, construct `FilesystemProjectionWriter(results_root, protected_roots=(all readable source roots,))`, then `RunArtifactStore`, parse/facts/review Skills and `DocumentManagementAgent`. `prepare-review` prints only `agent_run.v1`, never raw OCR text. Update quickstart with external results directory and no-archive guarantee.

- [ ] **Step 4: Verify GREEN and full regression**

Run: `python -X utf8 -B -m pytest tests/scripts/test_run_document_management_agent.py tests/platform_core/test_storage_bindings.py tests/integrations/test_filesystem_projection_writer.py -q`

Expected: all pass.

Run: `python -X utf8 -B -m pytest -q`

Expected: zero failures; record exact pass/skip/warning counts.

- [ ] **Step 5: Run real acceptance without archiving**

Create an external temporary `results_root` and SQLite database. Run `prepare-review` only for the user-designated scan PDF and electronic-invoice PDF. Inspect `run-summary.md`, both `document.json`, both `review.md`, SQLite artifact references and source hashes. Confirm no archive directory exists and source metadata is unchanged. Do not commit runtime results.

- [ ] **Step 6: Commit**

Commit message: `feat: expose document management review workflow`.

## Plan Self-Review

- Three-layer responsibilities are implemented in Tasks 1–6 without placing Connector behavior in Agent methods.
- Progressive disclosure is tested in Task 5 through the exact loaded Skill sequence for invoice, non-invoice and parse-failure cases.
- JSON/Markdown artifacts, result-root isolation and SQLite artifact references are covered by Tasks 2, 4 and 6.
- Invoice facts are covered by Task 3; no project decision, PageIndex effect, PostgreSQL, old DOC support or archive execution is introduced.
- Every introduced interface is defined before use: contracts in Task 1; artifact store in Task 2; facts in Task 3; review/repository APIs in Task 4; Agent in Task 5; CLI in Task 6.

