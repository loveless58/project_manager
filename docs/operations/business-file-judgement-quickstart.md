# Safe business-file judgement CLI quickstart

`prepare_business_file_run.py` creates a reviewable business-file judgement run. It never automatically applies feedback and never physically archives, moves, overwrites, renames, or deletes a source file.

## Safety boundary

- Every run supplies `--config`, `--context`, `--source-binding`, and explicit files. `--target-binding` is optional; if supplied it must be enabled, writable, and have the `archive_target` role.
- Binding IDs and logical URIs identify data across nodes. A physical path belongs only in the local node configuration. SynologyDrive is only one possible storage binding, never a unique root or default archive target.
- Runtime workspace, SQLite, caches, LLM responses, and run artifacts must remain on the current node's local non-synced disk and outside every storage binding.
- The CLI injects `DisabledOcrProvider`. Native text PDF, DOCX, XLSX, Markdown, and XML use their native parsers. A scanned or image-only PDF returns `OCR.CAPABILITY_DISABLED`; this CLI does not probe or run EasyOCR, RapidOCR, PaddleOCR, MinerU, Tesseract, macOS Vision, or an OFD converter.

## Select a mode

| Mode | Configuration and fallback | Lifecycle | Archive authority |
|---|---|---|---|
| `configured_llm` (default) | Requires an endpoint and credential. Missing either fails closed; it never silently changes to `agent`, rules, or another model. `LLM_MODEL` is optional as described below. | Full review, adversarial-verification, audit, feedback, and plan path | None |
| `agent` | Interactive, local-only, two-phase host handoff; no discovery or fallback; requires an explicit strict response | Phase 1 request, then phase 2 response consumption and review package | None |
| `disabled` | Explicit capability-off check | Early block with `LLM.CAPABILITY_DISABLED`; no run is created | None |

All modes are review-only. Neither `configured_llm` nor `agent` enables physical archive execution, and `agent` is not a substitute for the separate human confirmation required by a later archive workflow.

## Configure bindings

Copy the repository example configuration to a protected local configuration location and set the physical roots for the executing node. The following IDs can stay stable while each node uses its own physical mount:

```json
{
  "deployment_mode": "local",
  "runtime_workspace": "<node-local-runtime>",
  "database": {
    "provider": "sqlite",
    "sqlite_path": "<node-local-runtime>/state/project-manager.sqlite3"
  },
  "storage_bindings": [
    {"binding_id": "incoming", "provider": "local", "node_id": "win-ops", "logical_root": "business://incoming/", "physical_root": "<source-mount>", "roles": ["source"], "readable": true, "writable": false},
    {"binding_id": "archive", "provider": "local", "node_id": "win-ops", "logical_root": "business://archive/", "physical_root": "<archive-mount>", "roles": ["archive_target"], "readable": false, "writable": true}
  ]
}
```

On macOS, a node can map `physical_root` to a local mount while retaining the stable `business://incoming/` logical root. Runtime and SQLite still remain on that node's local, non-synced disk.
Remote Synology execution/provider integration is intentionally deferred. Never put runtime state on SynologyDrive, SMB/NFS, OneDrive, or another sync volume.

`configured_llm` constructs only the configured interpreter. It requires `LLM_API_KEY` and an endpoint resolvable from the standard `PROJECT_MANAGER_LLM_BASE_URL` or, for an existing deployment, the legacy `LLM_BASE_URL` / `OPENAI_API_BASE` fallback. New deployments should set `PROJECT_MANAGER_LLM_BASE_URL`; legacy deployments may temporarily retain a fallback variable while migrating. If no endpoint can be resolved or the credential is missing, the CLI returns `status="blocked"`, `error_code="LLM.CAPABILITY_DISABLED"`, and exit `2`. If `LLM_MODEL` is unset or blank, the current implementation uses `minimax-m3-mxfp8`. A deployment policy that requires an explicitly approved model must therefore set `LLM_MODEL`. The CLI never falls back to `agent`, a rule-based interpretation, another model, or an OCR provider.

## Catalog and LLM environment

`--context` names exactly one schema-valid `business_context_catalog.v1` JSON file; do not recursively discover a catalog from business directories. Keep LLM credentials in the execution environment only, not in config, catalog, stdout, or artifacts:

```powershell
$env:LLM_API_KEY = "<secret>"
$env:LLM_MODEL = "<approved-model>"
$env:PROJECT_MANAGER_LLM_BASE_URL = "https://<approved-endpoint>/v1"
```

## Run

Windows PowerShell:

```powershell
python -X utf8 -B scripts/prepare_business_file_run.py --config <node-local-config> --context <node-local-catalog> --source-binding incoming --target-binding archive <bound-source-file>
```

macOS shell:

```bash
python3 -X utf8 -B scripts/prepare_business_file_run.py --config /opt/project-manager/config.json --context /opt/project-manager/business_context_catalog.json --source-binding incoming <bound-source-file>
```

Exit behavior is mode-specific:

| Mode/phase | Exit `0` | Exit `2` | Run artifacts and archive check |
|---|---|---|---|
| `configured_llm` | The full review/audit/feedback package was prepared with no document failures. | Configuration, binding, parse, OCR, or another safe block; a scanned document with disabled OCR is one example. | Creates input, candidate, archive-intent, review, adversarial-verification, audit, feedback, plan, and trace artifacts. It then calls `execute_archive_plan(run_id, confirmed=False)` exactly once. Strict intents remain review-only and do not produce `archive_execution_gate.json` or `archive_result.json`. |
| `agent`, phase 1 | Returns `status="awaiting_agent_judgement"`. | Request/input validation or preparation is blocked. | Creates only request/input-side artifacts: `input_manifest.json`, `agent_judgement_input_snapshot.json`, native extracted staging under `extracted/`, and `agent_judgement_requests.json`. It also creates the dedicated local exchange directory `<runtime_workspace>/agent-host-responses/`. There is no candidate, review, feedback, plan, or archive-gate artifact. |
| `agent`, phase 2 | A strict response from the dedicated exchange is consumed and the candidate/review package is prepared. | The response is missing, malformed, mismatched, outside the exchange, or already consumed; changed/reordered source input returns `AGENT_JUDGEMENT.RUN_INPUT_INVALID`. | Adds `agent_judgement_consumption.json`, `candidate_interpretations.json`, `archive_intents.json`, `review_queue.json`, `feedback_form.json`, `feedback_form.md`, `planned_archive_actions.json`, and `trace.json`. It does not run an archive gate or create `archive_execution_gate.json` / `archive_result.json`. |
| `disabled` | Never. | Always returns `LLM.CAPABILITY_DISABLED` after argument validation. | Blocks before loading node configuration or creating a run; there are no run artifacts and no archive-gate call. |

Exit `1` is reserved for an unexpected error. Stdout is redacted JSON: it contains run ID, binding IDs, statuses, failure codes, and artifact names, but never physical paths or credentials.

## Agent handoff mode

The default `configured_llm` mode keeps the command above unchanged. `agent` is an explicit interactive, local-only operator workflow; it is not an invisible CLI model and it never discovers a host automatically.

### Phase 1 — prepare the request

```powershell
python -X utf8 -B scripts/prepare_business_file_run.py --config <node-local-config> --context <node-local-catalog> --source-binding incoming --interpreter-mode agent <bound-source-file>
```

Phase 1 returns exit `0` with `status="awaiting_agent_judgement"` and writes the request/input-side artifacts listed above. It creates no candidate, review, feedback, archive plan, or archive gate. It does not construct a configured LLM interpreter or discover a Codex, Claude, or another model automatically.

### Phase 2 — resume the same run

A human-operated host inspects the redacted request, writes exactly one strict `agent_judgement_responses.v1` response, and resumes the same local run:

```powershell
python -X utf8 -B scripts/prepare_business_file_run.py --config <node-local-config> --context <node-local-catalog> --source-binding incoming --interpreter-mode agent --resume-run <run_id> --agent-response <node-local-runtime>/agent-host-responses/<strict-response-json> <bound-source-file>
```

`--agent-response` is valid only with `--interpreter-mode agent --resume-run`. The response must be a regular file beneath the fixed `<runtime_workspace>/agent-host-responses/` directory. Network locations, paths elsewhere in runtime, source/archive bindings, path traversal, and symlink/reparse components are rejected; importing an external response is not supported. Malformed, missing, disabled, or already-consumed exchanges fail closed with exit `2`; never retry a consumed run with another response.

Supply the same ordered, unchanged source files from the original binding. The public resume boundary verifies their logical references and content hashes against `agent_judgement_input_snapshot.json`, the manifest, extracted artifacts, and saved requests both before processing and again while holding the consumption lock.

A successful phase 2 writes the response-consumption, candidate, review, feedback, plan, and trace artifacts listed above, but does not run an archive gate. Completing either judgement mode still leaves archive actions review-only. A human must use the separate archive-confirmation workflow; an agent response cannot supply or imply that confirmation.

Use `--interpreter-mode disabled` when a model is intentionally unavailable. After argument validation it returns `LLM.CAPABILITY_DISABLED` without loading node configuration, building adapters, reading bindings or source files, creating a run or run artifacts, invoking a model, running an archive gate, or falling back to another model.

## Deferred integrations and invoice scope

PostgreSQL-backed central coordination and remote Synology execution/provider integration are intentionally deferred. This quickstart supports a local node using local storage bindings, node-local runtime state, and node-local SQLite.

Invoice-specific PDF/OCR metadata propagation, stricter invoice action/schema behavior, ledger integration, and high-confidence classification belong to a separate plan. Until then, invoice handling remains at the existing minimum review-only boundary and must not infer a project name from the filename or physical path.

## Inspect artifacts

Inspect `runtime_workspace/runs/<run_id>/` on the local node. The exact list depends on the mode and phase: use the lifecycle table above instead of expecting every file after every successful exit.

Only `configured_llm` calls `execute_archive_plan(run_id, confirmed=False)`, exactly once and only after the full review package is prepared. The current strict-intent plan is rejected as `archive_intent_not_executable` before legacy executable-plan gate evaluation, so neither `archive_execution_gate.json` nor `archive_result.json` is written. `agent` and `disabled` never call that method. No mode calls `confirmed=True` or a feedback-application method, and no mode performs a physical archive.

A scanned-file result with `OCR.CAPABILITY_DISABLED` is an expected safety stop: supply a natively readable PDF/DOCX/XLSX/Markdown/XML or use a separately approved OCR workflow.

## Verified usable slice

The integration test at tests/integration/test_business_judgement_usable_slice.py
exercises the operator-facing path with two independent temporary storage
bindings. It uses real native PDF, DOCX, XLSX, Markdown, and XML parsers; the
real JsonBusinessContextProvider, retrieval service, PageIndex adapter, and
archive-plan writer; and fakes only the PageIndex network client and
OpenAI-compatible HTTP transport.

The test verifies a unique C-001 candidate relation, hash-preserved source
files, a normalized review queue, and needs_review archive actions. It also
records the required order retrieval -> PageIndex -> LLM. The image-only PDF
continues to stop with OCR.CAPABILITY_DISABLED, so no implicit OCR provider is
selected. The test invokes the CLI for both a native file and a scan to assert
the exit/status contract described above.
