---
name: project-manager
version: 3.3.0
description: |
  Project Manager Agent is the single entrypoint for project work. It routes a
  request to one active skill, exposes only that skill's tools, and runs one
  LoopEngine with structured observations.
---

# Project Manager Agent

## Role

This agent is the orchestration entrypoint for project-manager workflows.

The control plane must stay small:

```text
User request
  -> skill routing
  -> one active skill
  -> active ToolRegistry only
  -> one LoopEngine
  -> structured artifacts and trace
```

## Runtime Principles

1. Use one `LoopEngine`; skills and tools do not implement nested planning loops.
2. Route to one active skill before exposing tool schemas.
3. Treat `ToolRegistry` and `main.SKILL_TOOL_MAP` as the runtime tool source of truth.
4. Keep skill docs as human/model guidance, not complete tool registries.
5. Keep hard safety rules in code, validators, and tests.

## Hard Rules

- Do not expose unrelated skill tools to the active loop.
- Do not convert `blocked` into success or "not found".
- Do not continue past `needs_confirmation` without explicit follow-up.
- Do not let LLM text overwrite project ledger facts directly.
- Do not move, rename, overwrite, delete, or externally write without a tool-level gate.
- Do not add a second loop engine for a new skill.

## Source Of Truth

| Concern | Source |
|---|---|
| Runtime entrypoint and skill routing | `main.py` |
| Active tool ownership | `main.SKILL_TOOL_MAP` |
| Tool schema and callable functions | `ToolRegistry` registrations in `main.py` |
| Fallback policy for rule mode | `skill_policies/` |
| Loop mechanics and observation evaluation | `common/loop_engine.py` |
| Project ledger mechanics | `ledger/project_ledger.py` |
| Governance checks | `governance/validate.py` |

README files and skill markdown files may summarize these contracts, but must not redefine full tool lists or execution semantics.

## Verification Gates

Use these commands after each refactor round:

```powershell
python -X utf8 -B tests\test_loop.py
python -X utf8 -B -m unittest tests.test_goal_validation
python -X utf8 -B governance\validate.py tools
```
