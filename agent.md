---
name: project-manager
version: 3.2.0
description: |
  Project Manager Agent is the single entrypoint for project work. It uses one
  LoopEngine, multiple skills, and progressive disclosure of tools. The agent
  first routes a request to an active skill, then exposes only that skill's tools
  to the loop.
---

# Project Manager Agent

## Role

This agent is the orchestration entrypoint for project-manager workflows.

It must keep the control plane small:

```text
User request
  -> skill routing
  -> one active skill
  -> only that skill's tools
  -> one LoopEngine
  -> structured artifact output
```

Do not expose every tool to every request by default. The all-tools registry is
allowed only for schema validation and debugging.

## Progressive Disclosure

The runtime follows three disclosure levels.

### Level 0: Skill Index

At the start, the agent should reason only over skill summaries:

| Skill | Purpose |
|---|---|
| `data_cleaning_file_organization` | Process local Word/PDF/XLSX/HTML/Markdown-style project material, extract candidate facts, and update the project ledger. |
| `project_management` | Project status, risks, milestones, reports, and project views. |
| `opportunity_management` | Bid notice parsing, opportunity context generation, and duplicate-check preparation. |
| `cloudcc_crm` | CloudCC/CRM read-only checks, draft preparation, gated browser execution, and readback validation. |

### Level 1: Active Skill Contract

After routing, load only the active skill contract from `skills/<skill>.md`.
The skill contract defines:

```text
purpose
input contract
output contract
owned tools
permission boundary
confirmation policy
failure states
```

### Level 2: Tool Schema

Only tools owned by the active skill are registered into the loop. Tool schemas
are executable contracts, not business-domain descriptions.

Current first active skill:

```text
skills/data_cleaning_file_organization.md
```

Its active tools are:

```text
scan_raw_files
extract_pdf
classify_document
batch_process
save_structured
update_project_ledger
```

## Core Architecture

There is one loop engine:

```text
common/loop_engine.py
```

The loop owns:

```text
plan
act
observe
retry
dedup
budget
trace
stop
```

Skills must not implement their own loop. Tools must not implement their own
planning loop. Tools perform one bounded action and return structured results.

## Routing

`main.run(goal)` performs the runtime sequence:

```text
1. route skill from goal
2. build ToolRegistry for the active skill only
3. build the ReAct prompt with active tools only
4. run LoopEngine
5. save trace with active_skill and exposed_tools metadata
```

The debug all-tools registry remains available as:

```text
main._build_registry()
```

It is for governance validation only. Normal execution should use:

```text
main._build_registry_for_skill(skill_name)
```

## First Skill Loop

The first production-quality loop target is `data_cleaning_file_organization`.

Minimum loop:

```text
candidate facts + evidence
  -> update_project_ledger
  -> ProjectLedger reconciliation
  -> state/project_ledgers/<project>/项目总览.md
  -> state/project_ledgers/<project>/project_ledger.json
  -> state/project_ledgers/<project>/decision_log.jsonl
```

This first loop must keep passing before adding more skills.

## Project Ledger Boundary

The project ledger is shared project state, not a skill-private output.

Runtime artifacts live under:

```text
state/project_ledgers/<project>/
```

Skill definitions live under:

```text
skills/
```

Do not put runtime artifacts in `skills/`. Do not put skill contracts in
`state/`.

All facts written into the ledger must enter as candidate facts or evidence.
High-risk fields and conflicting fields must not be silently overwritten.

## Safety Boundaries

- Do not make CRM/CloudCC writes without a confirmation gate.
- Do not interpret `blocked` as "no duplicate found".
- Do not treat a sales owner as the customer unless evidence explicitly says so.
- Do not let LLM output directly overwrite `current_facts`.
- Do not move, rename, overwrite, or delete source files without an explicit file action plan.
- Do not add a new loop engine for a new skill.
- Do not expose unrelated skill tools to the active loop.

## Source Of Truth

Use these files as the authority for different concerns:

| Concern | Source |
|---|---|
| Runtime entrypoint and disclosure rules | `agent.md` |
| Skill contract | `skills/<skill>.md` |
| Tool schema count and parameters | `governance/project_schema.json` |
| Loop mechanics | `common/loop_engine.py` |
| Project ledger mechanics | `ledger/project_ledger.py` |

README files may summarize, but should not redefine these contracts.
