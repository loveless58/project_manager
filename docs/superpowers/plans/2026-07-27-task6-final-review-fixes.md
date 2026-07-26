# Task 6 Final Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four final security/compatibility findings and regenerate Task 6 evidence from base `510d162`.

**Architecture:** Centralize bidirectional path-disjointness, persist a separate immutable agent-input snapshot, move resume validation into the owner/Gateway boundary under the consumption lock, and constrain host responses to one node-local exchange directory. Known provider configuration failures become a stable safe block; unexpected failures retain exit `1`.

**Tech Stack:** Python 3.13, pathlib, SHA-256, strict JSON contracts, pytest, existing atomic feedback transaction and governance validators.

## Global Constraints

- Do not implement deferred invoice-specific PDF/OCR metadata, action/schema, ledger, or classification behavior.
- Do not weaken archive confirmation, review-only, redaction, strict JSON, or one-response semantics.
- Preserve the existing user-owned untracked `docs/superpowers/plans/2026-07-26-local-agent-judgement-dry-run.md`.
- Use TDD for every production behavior change and record the RED/GREEN evidence.
- Do not push from this task.

---

### Task 1: Bidirectional runtime/storage isolation

**Files:**
- Modify: `platform_core/path_locality.py`
- Modify: `platform_core/settings.py`
- Modify: `app_bootstrap/composition.py`
- Modify: `tools/data_cleaning_tools.py`
- Modify: `integrations/projections/filesystem_writer.py`
- Test: `tests/platform_core/test_settings.py`
- Test: `tests/platform_core/test_composition.py`
- Test: `tests/integrations/test_filesystem_projection_writer.py`
- Test: `tests/test_business_judgement_run.py`

- [ ] Add table-driven RED tests for source/archive binding roots equal to or below runtime, projection, and SQLite protected roots.
- [ ] Add a RED runtime-composition test using manually constructed settings.
- [ ] Add a RED writer sentinel test proving protected source bytes cannot be replaced.
- [ ] Run the focused tests and confirm failures identify the reverse-overlap gap.
- [ ] Add one shared symmetric overlap predicate and enforce it at Settings, composition, direct tool, and writer boundaries.
- [ ] Re-run focused tests to GREEN without changing valid sibling behavior.

### Task 2: Immutable prepared-input binding

**Files:**
- Modify: `services/agent_judgement_gateway.py`
- Modify: `tools/data_cleaning_tools.py`
- Modify: `scripts/prepare_business_file_run.py`
- Modify: `scripts/run_local_agent_dry_run.py`
- Test: `tests/services/test_agent_judgement_gateway.py`
- Test: `tests/scripts/test_prepare_business_file_run.py`
- Test: `tests/integration/test_local_agent_judgement_dry_run.py`
- Test: `tests/test_archive_execution_gate.py`

- [ ] Add RED tests for manifest `source_ref`, extracted artifact, request binding, source content, and same-content alternate logical path tampering; snapshot old run artifacts byte-for-byte.
- [ ] Add a RED direct public-boundary test proving resume cannot omit ordered source files.
- [ ] Run the focused tests and confirm each fails before any review transaction.
- [ ] Persist `agent_judgement_input_snapshot.v1` with ordered request/source/content/parse/extracted digest bindings.
- [ ] Change public resume APIs to require ordered source files and cross-check all four persisted inputs under the consumption lock.
- [ ] Remove CLI-only manifest/hash validation and pass the already binding-scoped file list to the public boundary.
- [ ] Update the local dry-run host and all compatibility callers.
- [ ] Re-run focused tests to GREEN and confirm old artifacts remain identical on every block.

### Task 3: Node-local agent-response exchange

**Files:**
- Modify: `services/agent_judgement_gateway.py`
- Modify: `tools/data_cleaning_tools.py`
- Modify: `scripts/run_local_agent_dry_run.py`
- Modify: `docs/operations/business-file-judgement-quickstart.md`
- Test: `tests/services/test_agent_judgement_gateway.py`
- Test: `tests/scripts/test_prepare_business_file_run.py`
- Test: `tests/integration/test_local_agent_judgement_dry_run.py`

- [ ] Add RED tests for response files outside runtime, under a storage binding, using network syntax, and through a symlink/reparse component.
- [ ] Add a valid response test under `<runtime>/agent-host-responses`.
- [ ] Run locality tests and confirm the current unrestricted `isfile` behavior fails them.
- [ ] Bind the Gateway to the owner workspace/storage roots and validate the dedicated exchange path before reading JSON.
- [ ] Update host fixtures and quickstart instructions to use the exchange directory.
- [ ] Re-run locality and phase-1/phase-2 tests to GREEN.

### Task 4: Stable configured-LLM configuration block

**Files:**
- Modify: `scripts/prepare_business_file_run.py`
- Modify: `docs/operations/business-file-judgement-quickstart.md`
- Test: `tests/scripts/test_prepare_business_file_run.py`

- [ ] Add RED CLI tests for missing endpoint, missing key, blank standard variables, and legacy endpoint fallback.
- [ ] Retain the existing unexpected-exception test as the exit-`1` mutation guard.
- [ ] Catch `ProviderConfigurationError` only and emit redacted `LLM.CAPABILITY_DISABLED` with exit `2`.
- [ ] Re-run CLI tests to GREEN and update the operator-facing exit contract.

### Task 5: Ledger, tracked evidence, and final verification

**Files:**
- Modify: `.superpowers/sdd/2026-07-26-local-agent-judgement-dry-run/progress.md` (local ignored ledger)
- Modify: `.superpowers/sdd/2026-07-26-local-agent-judgement-dry-run/task-6-report.md` (local ignored report)
- Modify: `reports/2026-07-26-local-agent-judgement-dry-run.md`

- [ ] Record every focused RED/GREEN result and final commit range from `510d162`.
- [ ] Run all focused agent/settings/projection/CLI tests.
- [ ] Run `python -X utf8 -B -m pytest -q -p no:cacheprovider` and record exact pass/skip/warning/subtest counts.
- [ ] Run `governance/validate.py tools`, `repository`, and `all`; require `0 errors, 0 warnings`.
- [ ] Compile all changed Python modules and run `git diff --check` plus `git diff --cached --check`.
- [ ] Commit production, tests, docs, ledger/evidence as appropriate; verify only the user-owned untracked plan remains.
