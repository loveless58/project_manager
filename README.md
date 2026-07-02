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

## Verification

Run the three standard gates after each refactor round:

```powershell
python -X utf8 -B tests\test_loop.py
python -X utf8 -B -m unittest tests.test_goal_validation
python -X utf8 -B governance\validate.py tools
```

Use `python -X utf8` on Windows so Chinese output and emoji in existing diagnostics do not fail under a GBK console.

## Environment

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `LLM_API_KEY` | only for LLM mode | unset | API key for `LLMPlanner` |
| `LLM_BASE_URL` | no | `http://172.18.125.202:9990/v1` | API endpoint |
| `LLM_MODEL` | no | `minimax-m3-mxfp8` | model name |

Without `LLM_API_KEY`, `planner_mode="auto"` falls back to `RuleBasedPlanner`.

## License

MIT
