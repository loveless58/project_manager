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

## Fix round 1: hardened boundaries and executable acceptance gate

The follow-up review findings were resolved without changing the production
agent-judgement contracts or their tests.

Before any root resolution, existence scan, directory creation, or write, the
runner now performs lexical network-location rejection and a component-by-
component reparse audit. On Windows, the audit uses `os.lstat`,
`st_file_attributes`, and `stat.FILE_ATTRIBUTE_REPARSE_POINT`; it also checks
`stat.S_ISLNK` for portable symbolic-link coverage. The raw root and each fixed
child (`source`, `runtime`, `config`, and `archive`) are audited before
resolution. Every resolved fixed child must be a strict descendant of the
resolved root. Tests prove that root symlinks and fixed-child junctions cannot
modify an external sentinel tree or SQLite state.

Obvious network roots are rejected lexically with `DRY_RUN.ROOT_NETWORK` before
constructing a `Path` or invoking filesystem APIs. Covered forms are UNC,
forward-slash UNC, SMB, NFS, and AFP.

Synthetic-source reuse no longer treats the source-owned marker as its trust
anchor. The marker remains an integrity memo, while authorization is bound to
deterministic normalized content defined in the runner's code trust domain:
exact Markdown and XML; exact DOCX paragraphs without tables or inline shapes;
exact XLSX workbook, sheet, and cell values without external links; exact PDF
text/image structure; and exact raster pixel content for the scan. A forged
file accompanied by a matching forged marker is rejected as
`DRY_RUN.SOURCE_UNSAFE` before runtime changes.

A single acceptance gate now controls every `needs_review` success response.
It requires all five native formats to be `needs_review`, the scan failure to
be `OCR.CAPABILITY_DISABLED`, the invoice candidate to be `C-001`, source
hashes to remain identical, review items to be non-empty and valid, verify,
audit, and feedback stages to complete, returned and persisted archive actions
to remain non-empty with every `confirmed` flag exactly `false`, and no
`archive_result.json` anywhere under runtime. Any counterexample returns
`DRY_RUN.ACCEPTANCE_FAILED`, and the CLI exits non-zero.

The scan fixture is now a real raster image-only PDF: one 64-by-64 RGB image is
embedded across the page, with no text layer or vector drawings. Unexpected
CLI exceptions emit only `DRY_RUN.UNEXPECTED` to stderr and do not expose the
physical root. A separate end-to-end case exercises the independent
`dry-run-archive` binding and proves that it remains empty and review-only.

### Fix-round TDD evidence

Each class was driven from a focused failing test to a focused passing test:

- Reparse boundary: `5 failed` to `5 passed` (root symlink plus
  `source`/`runtime`/`config`/`archive` junctions).
- Network root: `5 failed` to `5 passed` (UNC, forward UNC, SMB, NFS, AFP),
  with a fail-fast filesystem spy.
- Reuse trust domain: forged file plus forged marker produced `1 failed`, then
  forged rejection and legitimate reuse produced `2 passed`.
- Acceptance gate: all ten scan/hash/archive/check/review counterexamples first
  produced `10 failed`, then `10 passed`.
- Raster, stderr, and archive binding: raster and stderr first produced
  `2 failed, 1 passed`, then the complete focused set (including reuse)
  produced `4 passed`.

Fresh focused and required regression results:

```text
python.exe -X utf8 -B -m pytest tests/integration/test_local_agent_judgement_dry_run.py -q
31 passed in 8.82s

python.exe -X utf8 -B -m pytest tests/integration/test_local_agent_judgement_dry_run.py tests/integration/test_business_judgement_usable_slice.py tests/scripts/test_prepare_business_file_run.py -q
46 passed in 10.57s
```

The first fix-round repository governance run exposed three Task 5 static
findings in addition to the six known baseline findings. Synthetic identifiers
were then constructed without embedding unmarked business-like literals, and
synthetic absolute-path test vectors received the repository's audited inline
exemption. Fresh `repository` and `all` runs returned only the same six
pre-existing findings; neither command named a Task 5 file.
