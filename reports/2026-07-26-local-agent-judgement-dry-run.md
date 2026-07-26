# Local Agent Judgement Dry-run Evidence

## Scope and isolation

This milestone exercised only generated synthetic documents under one explicit
dry-run root. It did not read a business root or connect to Synology,
PostgreSQL, PageIndex, Lucky, VPN, or any public network service.

The generated topology is root-relative:

- `source/`: one read-only `dry-run-source` binding containing six generated inputs.
- `runtime/`: run artifacts, projections, host responses, and SQLite state.
- `config/`: a materialized local node config and synthetic business-context catalog.
- `archive/`: optional, independent `dry-run-archive` binding; absent by default.

SQLite is node-local and root-relative at
`runtime/state/project_manager.sqlite3`. It is outside every source or archive
storage binding.

## Synthetic acceptance result

Illustrative run identity: `run_00000000000000000000000000000000`. Actual
temporary run identities are deliberately not copied into this report.

| Generated artifact | Expected result |
| --- | --- |
| Native-text invoice PDF | `needs_review`; candidate contract `C-001` |
| Contract DOCX | `needs_review` |
| Governance Markdown | `needs_review` |
| Project XLSX | `needs_review` |
| Project XML | `needs_review` |
| Image-only scanned PDF | blocked with `OCR.CAPABILITY_DISABLED` |

Source SHA-256 values were identical before and after the run. Every review
queue item had a non-empty `id` and `question`. Every planned archive action
remained review-only with `confirmed=false`, and no `archive_result.json` was
created.

The persisted run package included these artifact names:

- `input_manifest.json`
- `agent_judgement_requests.json`
- `agent_judgement_consumption.json`
- `candidate_interpretations.json`
- `archive_intents.json`
- `review_queue.json`
- `planned_archive_actions.json`
- `feedback_form.json`
- `feedback_form.md`
- `trace.json`
- `adversarial_verification.json`
- `audit_review.json`

The deterministic host received only the persisted request artifact path. It
read that artifact before writing a strict response collection. The existing
schema, request-hash binding, evidence binding, one-response consumption, and
review transaction validated the response before any review artifact changed.

OCR recognition providers were not installed or invoked in this milestone.

No configured LLM was constructed or called. Tests also replaced every real
OCR probe entry point and configured-interpreter constructor with a fail-fast
guard.

## Fail-closed coverage

Acceptance tests covered a missing host response, altered response request
hash, unsafe physical-path content, a relation without evidence, an external
source path, an existing source without the explicit synthetic-reuse flag, and
an unrelated non-empty root. Each case returned a stable blocked code, kept
source hashes unchanged when source artifacts existed, and created no archive
result.

## RED and GREEN evidence

RED 1:

```text
python -X utf8 -B -m pytest tests/integration/test_local_agent_judgement_dry_run.py -q
FAILED: ModuleNotFoundError: No module named 'scripts.run_local_agent_dry_run'
1 failed
```

RED 2:

```text
python -X utf8 -B -m pytest tests/integration/test_local_agent_judgement_dry_run.py -q
FAILED: unrelated existing root returned needs_review instead of blocked
1 failed, 4 passed
```

Focused GREEN:

```text
python -X utf8 -B -m pytest tests/integration/test_local_agent_judgement_dry_run.py -q
7 passed
```

Required local regression GREEN:

```text
python -X utf8 -B -m pytest tests/integration/test_local_agent_judgement_dry_run.py tests/integration/test_business_judgement_usable_slice.py tests/scripts/test_prepare_business_file_run.py -q
22 passed
```

Syntax and diff verification completed with exit code `0`:

```text
python -X utf8 -B -m py_compile scripts/run_local_agent_dry_run.py tests/integration/test_local_agent_judgement_dry_run.py
git diff --check
```

The required `governance/validate.py repository` and
`governance/validate.py all` commands both reported six pre-existing findings
in `contracts/agent_judgement.py` and its contract, interpreter, and gateway
tests. None of the Task 5 files was named in the governance output. Those
earlier-task files were intentionally left unchanged to preserve this task's
approved scope; the exact affected files and validator output are recorded in
the task report.
