# Loop Package Rule Slimming Round 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start converting `project_manager` toward an AgentPlatform-style loop package by reducing duplicated explicit rules while preserving hard runtime gates.

**Architecture:** Keep the existing `LoopEngine`, tool implementations, and validated file-organization behavior. Move rule-based fallback sequencing for high-risk domains into small skill policy modules, keep hard gates enforced in code/tests, and downgrade repeated tool lists in skill docs to human-readable summaries that reference the registry as source of truth.

**Tech Stack:** Python stdlib, `unittest`, existing `ToolRegistry`, existing `LoopEngine`, existing governance validator.

---

### Scope For This Round

This round is a control-plane slimming pass, not a full loop-package rewrite.

**Keep hard rules in code:**
- `blocked` must not become a successful negative finding.
- `needs_confirmation` must stop the loop at the human boundary.
- CloudCC/CRM writes must remain gated.
- Archive execution must still require `confirmed=True` and review artifacts when review is required.
- Active skill registry must still expose only owned tools.

**Move or remove over-explicit rules:**
- Move data-cleaning/file-organization and CloudCC fallback sequencing out of `planner.py`.
- Stop treating skill docs as a second complete tool registry.
- Keep `governance/project_schema.json` as schema validation, not as narrative business workflow documentation.

### Files

- Create: `skill_policies/__init__.py`
- Create: `skill_policies/base.py`
- Create: `skill_policies/data_cleaning_file_organization.py`
- Create: `skill_policies/cloudcc_crm.py`
- Modify: `planner.py`
- Modify: `tests/test_loop.py`
- Modify: `skills/data_cleaning_file_organization.md`
- Modify: `skills/cloudcc_crm.md`
- Modify: `skills/project_management.md`
- Modify: `skills/opportunity_management.md`
- Modify: `skills/project_manager.md`
- Modify: `agent.md`
- Modify: `README.md`

### Verification Gates

Run after this round:

```powershell
python -X utf8 -B tests\test_loop.py
python -X utf8 -B -m unittest tests.test_goal_validation
python -X utf8 -B governance\validate.py tools
python -X utf8 -B -m compileall .
```

### Round 1 Completion Criteria

- `RuleBasedPlanner` delegates data-cleaning/file-organization fallback planning to `skill_policies.data_cleaning_file_organization`.
- `RuleBasedPlanner` delegates CloudCC fallback planning to `skill_policies.cloudcc_crm`.
- Active skill docs describe boundaries and hard rules without duplicating every tool in their active registry.
- README and `agent.md` point to runtime sources of truth instead of re-listing complete tool tables.
- Three standard validation gates pass.

### Next Round Candidate

Introduce a formal loop package manifest:

```text
Loop Package = Skill Interface + Loop Policy + Workset Schema + Validators + Fixtures + Trace Contract
```
