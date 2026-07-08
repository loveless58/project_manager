# Project Manager Agent

`project_manager` is a local project-management agent built around one ReAct-style `LoopEngine`, active-skill tool disclosure, structured observations, and auditable project artifacts.

It currently covers four domains:

- project status, risks, milestones, reports, and local project views
- data cleaning and file organization into project ledgers
- opportunity parsing and local bid context preparation
- controlled CloudCC/CRM read-only checks, drafts, confirmation gates, and readback boundaries

## Quick Start

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run through `main.run()`:

```python
import sys
sys.path.insert(0, r"E:\Dev\Projects\project_manager")
from main import run

result = run("今天有什么风险项目", planner_mode="rule")
```

Run a file-organization task in an isolated workspace:

```python
result = run(
    r"请整理文件并归档 C:\path\to\采购公告.docx",
    planner_mode="rule",
    data_workspace_dir=r"C:\tmp\project_manager_data_workspace",
    trace_dir=r"C:\tmp\project_manager_traces",
)
```

Default business workspace for synced business files:

```powershell
$env:PROJECT_MANAGER_BUSINESS_ROOT = "E:\SynologyDrive"
$env:PROJECT_MANAGER_WORKSPACE_DIR = "E:\SynologyDrive\_project_manager_workspace"
```

When these variables are unset, runtime paths are resolved by `common.workspace_config`.
The production default is:

```text
business root: E:\SynologyDrive
runtime workspace: E:\SynologyDrive\_project_manager_workspace
```

The runtime workspace owns generated run packages, project ledgers, data-cleaning outputs,
opportunity context, state, logs, and HTML overviews. Source files under `E:\SynologyDrive`
are read as inputs; physical moves still require `execute_archive_plan(..., confirmed=True)`.

## Architecture

```text
main.run(goal)
  -> route one active skill
  -> build active ToolRegistry
  -> choose LLMPlanner or RuleBasedPlanner
  -> LoopEngine plan/act/observe
  -> write trace and domain artifacts
```

The runtime source of truth is intentionally narrow:

| Concern | Source |
|---|---|
| active skill ownership | `main.SKILL_TOOL_MAP` |
| executable tools and parameters | `ToolRegistry` registrations in `main.py` |
| rule-mode fallback sequencing | `skill_policies/` |
| observation evaluation and trace | `common/loop_engine.py` |
| runtime workspace and business paths | `common/workspace_config.py` |
| cloud/synced file readiness gates | `common/file_readiness.py` |
| schema drift checks | `governance/validate.py` |

Skill markdown files describe boundaries and model guidance. They are not complete tool registries.

## Hard Rules

- Only the active skill's tools are exposed to the loop.
- `blocked` is not success and not a negative finding.
- `needs_confirmation` stops at the human boundary.
- CRM/CloudCC writes must remain gated.
- File moves, renames, overwrites, and deletes require an explicit action plan and confirmation gate.
- Project ledger facts must enter as candidate facts with evidence; LLM summaries do not directly overwrite ledger state.

## File Organization Loop

The validated file-organization path is:

```text
prepare run package
  -> extract structured fields
  -> update project ledger
  -> emit review queue and archive plan
  -> apply human review when required
  -> execute archive plan only after confirmation
  -> generate derived progress views
```

Partial failures still preserve successful structured outputs, failure evidence, review queues, archive plans, traces, and run reports.

Source files are read from `WorkspaceConfig.business_root` by default. The data-cleaning
workspace is for generated artifacts such as `runs/` and structured outputs; it does
not require a visible `00-原始文件（待处理）` intake directory.

Before extracting or archiving synced files, the loop probes source readability. If a cloud-drive
placeholder or failed sync makes a visible file unreadable, the run records a structured failure:

```json
{
  "stage": "source_readiness",
  "error": "source_not_local_or_unreadable",
  "blocked_reason": "cloud_placeholder_or_sync_failure"
}
```

Archive execution repeats the source-readability check and validates the target directory is writable before moving any file.

## OCR/PDF Provider Boundary

PDF and image extraction is routed through the project-level `ocr.provider_registry`
module. This keeps OCR capability checks separate from business rules and archive
execution.

Provider order is intentionally conservative:

1. `.ocr.txt` sidecar files next to the source document.
2. PyMuPDF text extraction for text-based PDFs when `fitz` is installed.
3. A caller-provided `ocr_adapter`, used by tests or an explicitly configured runtime.
4. Structured `blocked` diagnostics when no provider can handle the file.

When a PDF or image is readable but cannot be parsed on the current machine, the
run records provider diagnostics instead of throwing an unhandled exception:

```json
{
  "status": "blocked",
  "blocked_reason": "ocr_adapter_unavailable",
  "engine_candidates": [
    {"engine": "sidecar_text", "available": false, "reason": "sidecar_missing"},
    {"engine": "pymupdf_text", "available": false, "reason": "module_not_installed"},
    {"engine": "tesseract", "available": false, "reason": "binary_not_found"},
    {"engine": "easyocr", "available": false, "reason": "module_not_installed"}
  ],
  "next_action": "install_pymupdf_or_provide_ocr_sidecar"
}
```

## Verification

Run the three standard gates after each refactor round:

```powershell
python -X utf8 -B tests\test_loop.py
python -X utf8 -B tests\test_workspace_config.py
python -X utf8 -B tests\test_file_readiness.py
python -X utf8 -B tests\test_ocr_provider_registry.py
python -X utf8 -B tests\test_data_cleaning_ocr_provider.py
python -X utf8 -B -m unittest tests.test_goal_validation
python -X utf8 -B governance\validate.py tools
python -X utf8 -B governance\validate.py dirs
```

Use `python -X utf8` on Windows so Chinese output and emoji in existing diagnostics do not fail under a GBK console.

## Environment

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `LLM_API_KEY` | only for LLM mode | unset | API key for `LLMPlanner` |
| `LLM_BASE_URL` | no | `http://172.18.125.202:9990/v1` | API endpoint |
| `LLM_MODEL` | no | `minimax-m3-mxfp8` | model name |
| `PROJECT_MANAGER_BUSINESS_ROOT` | no | `E:\SynologyDrive` | root containing real business files |
| `PROJECT_MANAGER_WORKSPACE_DIR` | no | `E:\SynologyDrive\_project_manager_workspace` | generated runtime artifacts, ledgers, logs, and outputs |
| `LOOP_PROJECT_BASE_DIR` | no | unset | governance compatibility override for directory validation |

Without `LLM_API_KEY`, `planner_mode="auto"` falls back to `RuleBasedPlanner`.

## License

MIT
