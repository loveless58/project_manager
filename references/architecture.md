# Architecture: Progressive Project Manager Agent

## Core Shape

The agent is intentionally small at the control-plane level:

```text
User goal
  -> SkillRouter
  -> active Skill
  -> active ToolRegistry
  -> one LoopEngine
  -> structured artifacts
```

There is one `LoopEngine`. New skills do not create new loop engines.

## Three Disclosure Levels

### Level 0: Skill Index

The agent first sees skill summaries only. This keeps routing simple and avoids
overloading the prompt with unrelated tools.

Current skill index:

```text
data_cleaning_file_organization
project_management
opportunity_management
cloudcc_crm
```

### Level 1: Skill Contract

After routing, the agent loads the selected skill contract:

```text
skills/<skill>.md
```

The first active contract is:

```text
skills/data_cleaning_file_organization.md
```

### Level 2: Tool Schemas

Only tools owned by the active skill are registered into the runtime loop.

Example for `data_cleaning_file_organization`:

```text
scan_raw_files
extract_pdf
classify_document
batch_process
save_structured
update_project_ledger
```

The full 27-tool registry still exists for governance validation:

```text
main._build_registry()
```

Normal runtime should use:

```text
main._build_registry_for_skill(skill_name)
```

## First Loop Contract

The first loop to keep stable is data cleaning and file organization:

```text
candidate facts + evidence
  -> update_project_ledger
  -> ProjectLedger
  -> state/project_ledgers/<project>/项目总览.md
  -> state/project_ledgers/<project>/project_ledger.json
  -> state/project_ledgers/<project>/decision_log.jsonl
```

This is the minimum usable product for the project ledger architecture.

## Boundaries

- Skill files define business capability contracts.
- Tools execute bounded actions.
- `ProjectLedger` manages project facts, evidence, conflicts, and decision logs.
- `LoopEngine` owns retry, budget, trace, dedup, and stop conditions.
- LLM output can recommend or classify, but cannot directly overwrite ledger facts.
- External-system writes, including CloudCC/CRM, must remain gated.

## Source Of Truth

| Concern | File |
|---|---|
| Disclosure and runtime contract | `agent.md` |
| Skill contract | `skills/<skill>.md` |
| Tool parameter schema | `governance/project_schema.json` |
| Loop behavior | `common/loop_engine.py` |
| Project ledger behavior | `ledger/project_ledger.py` |
