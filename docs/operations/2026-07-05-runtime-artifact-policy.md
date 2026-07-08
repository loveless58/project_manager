# Runtime Artifact Policy - 2026-07-05

## Decision

Runtime artifacts are not committed as project capability code.

The repository keeps source code, contracts, tests, PRDs, and compact operational notes. Real business outputs under `state/`, transient OCR verification files, and mutable runtime logs stay local unless a future audit workflow explicitly promotes a sanitized fixture.

## Current Classification

Keep as local-only runtime evidence:

- `state/all_execution_recheck/`
- `state/loop_structured_runs/`
- `state/real_scene_structured/`
- `state/semantic_eval_project_execution/`
- `state/targeted_field_quality_recheck/`
- `tmp_ocr_verify/`
- `logs/*.json`

Do not commit directly:

- real business extracted JSON
- OCR temporary images or PDFs
- mutable loop trace JSON
- generated full-run reports containing stale absolute paths or business content

Commit only after sanitization:

- small fixtures that remove sensitive business text
- aggregate eval summaries without raw source text
- schema examples that are manually reviewed

## Rationale

The runtime outputs are useful for local debugging, but they are not stable capability definitions. Committing them would mix code with machine-specific and business-specific artifacts, making future diffs harder to review and increasing the risk of leaking real business material.

The durable improvement loop should be:

1. Run read-only extraction against local business files.
2. Summarize failures and field-quality gaps.
3. Convert only sanitized, minimal examples into tests or fixtures.
4. Keep raw generated outputs local or in a future explicitly designed audit area.
