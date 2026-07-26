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

## Fix round 2: canonical bytes and artifact-bound acceptance

Synthetic-source reuse now compares each existing file with a fresh reference
generated entirely from runner-owned constants. Markdown and XML use exact byte
digests. DOCX and XLSX use sorted ZIP-member names plus decompressed member
bytes; duplicate members are rejected, and the only normalization is
openpyxl's rewritten `dcterms:modified` value. PDF reuse hashes the page count,
xref structure, catalog, every indirect object, and every raw stream while
excluding volatile trailer IDs. The source-owned marker is still checked, but
cannot authorize reuse. A forged marker therefore cannot hide an invoice vector
drawing, a DOCX header/relationship, or an XLSX hyperlink/relationship.

The final acceptance decision is now derived from strict, persisted artifacts,
not stage status strings. The gate validates and binds the review queue,
adversarial verification, audit review, feedback JSON/Markdown, and archive
plan to the same run and exact expected artifact paths. It also requires exact
synthetic review coverage, unique review/finding identities, immutable feedback
snapshot hashes, expected verdicts and agent roles, all review questions to be
non-empty, and returned archive actions to equal the validated persisted plan.
Exceptions in any post-run stage fail closed as `DRY_RUN.ACCEPTANCE_FAILED`.

### Fix-round-2 TDD evidence

Canonical-source RED:

```text
3 failed, 31 deselected
```

The three forged-marker attacks were incorrectly accepted. After structural
digest binding, the same attacks plus legal explicit reuse produced:

```text
4 passed, 30 deselected in 2.95s
```

Artifact-contract RED:

```text
10 failed, 34 deselected in 4.05s
```

The counterexamples covered corrupt and non-passing verification verdicts,
cross-run verification/audit/feedback/review artifacts, minimal audit/feedback
returns, fake review items, and duplicate review identities. After persisted
contract and run/path binding:

```text
10 passed, 34 deselected in 4.03s
```

Fresh final verification:

```text
python.exe -m pytest tests/integration/test_local_agent_judgement_dry_run.py -q -p no:cacheprovider
44 passed in 14.44s

python.exe -m pytest tests/integration/test_local_agent_judgement_dry_run.py tests/integration/test_business_judgement_usable_slice.py tests/scripts/test_prepare_business_file_run.py -q -p no:cacheprovider
59 passed in 16.86s
```

`py_compile` and `git diff --check` completed with exit code `0`. Fresh
`governance/validate.py repository` and `all` runs still report only the same
six upstream baseline findings; no Task 5 file is named.

## Fix round 3: raw-byte trust and process-derived acceptance

Canonical reuse now compares complete SHA-256 digests of the original source
bytes against a freshly generated trusted reference. PDF generation suppresses
volatile trailer IDs. Generated DOCX/XLSX packages are deterministically
repacked with sorted members, fixed ZIP metadata, and a fixed XLSX modified
timestamp. Consequently, parser-invisible content is still part of the trust
decision: bytes after `%%EOF` or ZIP EOCD, ZIP comments, extras, metadata, and
all package members must match exactly. The source marker remains a secondary
integrity memo and cannot authorize any source.

Reference generation no longer uses a system temporary directory. It creates a
fixed controlled reference directory under the supplied dry-run root, audits
the complete path for locality and reparse points, generates deterministic
files there, reads their raw digests, and explicitly removes every generated
file and the reference directory before continuing. A test guards every Python
temporary-path API and permits it only when its explicit `dir` is inside the
supplied root; this preserves the artifact layer's root-local atomic writes.

Acceptance is now re-derived from the persisted process inputs:

- The review queue is rebuilt from the strict input manifest, agent requests,
  candidate interpretations, and archive intents, including fixed questions,
  policies, evidence projections, risks, source hashes, and ordering.
- The plan uses the strict Task 5 collection contract and must contain exactly
  one unique action and intent for each of the five native synthetic sources.
- Verification findings and verdict are recomputed from strict extracted
  artifacts and the validated plan using the production verification rules.
- Audit violations, verdict, feedback requirements, next actions, artifact
  paths, and empty loop trace are recomputed using the production audit rules.
- The complete feedback JSON is rebuilt from the trusted review/audit/verify
  chain, and the persisted Markdown must exactly equal its renderer output.
- Archive execution must exactly match the real non-executable intent response:
  correct schema/run/status/gate, `moved=0`, empty results, and a failed count
  equal to the five validated plan actions.

### Fix-round-3 TDD evidence

The first focused run reproduced every requested bypass against the round-2
implementation:

```text
15 failed, 44 deselected in 7.72s
```

It covered PDF/DOCX/XLSX trailing bytes plus forged markers; a forbidden
root-external reference temp path; a schema-valid forged question; a high-risk
finding hidden behind `pass`; a high audit violation with fake trace; five
forged feedback projection fields; forged feedback Markdown; duplicate archive
coverage; and a fake successful cross-run archive response.

Fresh focused GREEN:

```text
15 passed, 44 deselected in 7.54s
31 acceptance-gate tests passed, 28 deselected in 13.36s
```

Fresh final verification:

```text
python.exe -m pytest tests/integration/test_local_agent_judgement_dry_run.py -q -p no:cacheprovider
59 passed in 23.69s

python.exe -m pytest tests/integration/test_local_agent_judgement_dry_run.py tests/integration/test_business_judgement_usable_slice.py tests/scripts/test_prepare_business_file_run.py -q -p no:cacheprovider
74 passed in 25.81s
```

`py_compile` and `git diff --check` completed with exit code `0`. Fresh
`governance/validate.py repository` and `all` runs still report only the same
six upstream baseline findings; no Task 5 file is named.

## Fix round 4: in-memory trust anchors and strict verification projection

Trusted canonical source references no longer allocate any filesystem
directory or file. The runner now constructs all six canonical inputs as
bytes: PDF documents use PyMuPDF's in-memory output, DOCX is saved to a
`BytesIO` buffer and deterministically repacked, XLSX is a minimal valid OOXML
package assembled and normalized entirely in memory, and Markdown/XML are
UTF-8 bytes. Actual source creation writes those bytes only to the explicit
`root/source` directory. Reuse hashes existing raw bytes against a fresh
in-memory canonical byte map; there is no reference directory to clean up or
pollute across successful and rejected retries.

A strict pure projection,
`project_extracted_document_for_verification(payload, run_id)`, now validates
the persisted Task 5 extracted-document contract and maps it to the production
verifier's `file`, `document_type`, and `fields` inputs plus bounded provenance
metadata. Production verification and acceptance recomputation call the same
projection. A schema-valid contract artifact that omits required candidate
fields therefore produces a high `field_completeness` finding and a
`needs_correction` verdict; forging the returned and persisted report to
`pass` is rejected by acceptance recomputation.

The production verifier retains its existing legacy prepared-run capability
through a separate, exact-key compatibility branch. That branch accepts only
the historical wrapper version, matching run ID, complete historical key set,
and a dictionary `extraction`; it does not relax the strict Task 5 contract or
the acceptance boundary. Synthetic Task 5 inputs were completed with explicit
project and contract fields required by the existing production completeness
rules. Invoice project linkage is taken only from an explicit `采购名称` or
`标的名称` label; invoice line-item `项目名称` headers remain excluded.

Feedback Markdown acceptance now uses the same descriptor strategy as strict
JSON loading: `lstat`, regular-file/reparse rejection, byte limit, `O_NOFOLLOW`
where available, descriptor `fstat` device/inode binding, bounded reads, and
strict UTF-8 decoding. Universal newline normalization preserves the former
logical-text behavior on Windows. A replacement between `lstat` and open is
rejected.

### Fix-round-4 TDD evidence

The initial focused counterexamples all failed against round 3:

```text
5 failed in 1.29s
```

They covered the absent pure projection, production verification incorrectly
returning `pass` for missing strict candidate fields, path-based canonical
generation, Markdown path replacement, and a forged pass over ignored strict
fields. The same focused set passed after the boundary changes:

```text
5 passed in 1.13s
```

The invoice line-item non-inference regression failed before the explicit-label
guard and passed together with the positive explicit procurement-name case.
The complete legal round trip, two consecutive legal reuses, two forged retry
rejections, Markdown replacement, and forged verification acceptance checks
then passed as a focused seven-test set.

Fresh final verification:

```text
python.exe -m pytest tests/integration/test_local_agent_judgement_dry_run.py tests/contracts/test_archive_run_artifacts.py tests/test_adversarial_verification.py tests/test_business_judgement_run.py tests/test_invoice_business_semantics.py -q
122 passed in 25.49s
```

`py_compile` and `git diff --check` completed with exit code `0`. Fresh
UTF-8 `governance/validate.py repository` and `all` runs still report only the
same six upstream baseline findings; no Task 5 file is named.

### Fix-round-4 review follow-up: ordered invoice labels

The review found that the invoice guard only checked whether an explicit
`采购名称` or `标的名称` existed, then delegated to the generic extractor. If an
invoice line-item `项目名称 规格型号` header appeared first, the generic match
could promote `规格型号` to `project_name` even though a valid explicit project
label appeared later.

Parameterized counterexamples for both explicit labels reproduced the defect:

```text
2 failed in 0.19s
```

The invoice branch now independently captures and cleans only the value bound
to a line-anchored `采购名称` or `标的名称`. It never delegates project-name
selection to the generic extractor. Existing no-explicit-label and explicit-
label-before-header cases plus both ordered counterexamples passed together:

```text
4 passed in 0.21s
```

The refreshed Task 5, contract, production-verification, business-judgement,
and invoice regression set passed `124 tests in 27.21s`. Targeted `py_compile`
and `git diff --check` completed with exit code `0`.

### Business-rule correction: invoices never carry a project name

The ordered-label follow-up above was superseded by an explicit business rule:
an invoice never owns a `project_name`. Invoice parsing must not infer one from
`项目名称`, `采购名称`, `标的名称`, line items, remarks, or any other invoice text.
Candidate ownership continues through invoice parties, business context,
candidate evidence, and subsequent Agent judgement.

The corrected RED set covered explicit labels before and after an invoice item
header, a project-name header, a project-like remark, no explicit project
label, the production completeness verdict, and the strict Task 5 synthetic
invoice artifact. Before implementation, four project-name assertions failed
while the no-label invariant already passed. Production verification also
returned `pass_with_warnings` because invoices fell through to the unknown-
document `project_name` requirement.

The `发票` and `invoice` extraction branches now retain only invoice-specific
fields and defensively remove `project_name`. The required-fields contract
explicitly maps both types to an empty project-field requirement, while the
unknown-document fallback remains unchanged. The Task 5 invoice artifact
contains no `project_name`; its business-context relation still resolves
candidate `C-001`, and its interpretation remains `needs_review`.

The seven focused production, parser, and Task 5 cases passed. The refreshed
Task 5, contract, adversarial-verification, business-judgement, and invoice
regression set passed `126 tests in 25.85s`. Targeted `py_compile` and
`git diff --check` completed with exit code `0`.

## Final Important hardening review (2026-07-27)

The final review baseline was `510d162`. The implementation head before this
evidence-only update was `416f20f`. The hardening series from `9726526`
through `416f20f` did not modify invoice production code or invoice contracts;
the invoice security follow-up remains explicitly deferred.

### Closed findings

1. Storage binding overlap is rejected symmetrically. Runtime, database,
   projection, and data-cleaning paths may neither contain nor be contained by
   any configured storage binding; they are not required to be mutually
   disjoint. Settings validation, composition, the filesystem projection
   writer, and data-cleaning tools share the same path-locality contract.
2. Agent resume is bound to immutable prepared inputs. Each prepared run
   persists an `agent_judgement_input_snapshot.v1` containing ordered
   `request_id`, `request_hash`, `source_ref`, `content_hash`,
   `parse_artifact_ref`, and `extracted_digest` items. The public resume
   boundary requires the exact ordered source set and revalidates the
   manifest, snapshot, extracted artifact, request, and source content before
   response processing and again
   under the run lock. Missing or altered inputs block with
   `AGENT_JUDGEMENT.RUN_INPUT_INVALID`.
3. Agent responses are local-exchange-only. Resume accepts regular files only
   below `<runtime_workspace>/agent-host-responses`, rejects traversal,
   network paths, protected storage roots, links/reparse points, and
   non-regular files, and preserves the existing capability-disabled outcome
   when an otherwise valid local response file is absent.
4. Configured-LLM provider setup failures are mapped to the stable capability
   block (`LLM.CAPABILITY_DISABLED`, exit code `2`) for absent
   or blank endpoint/key configuration. The documented legacy endpoint
   fallback remains compatible.
5. The two new negative-test fixtures construct synthetic credential and UNC
   inputs at runtime so repository hygiene continues to reject literal secret
   bindings and network paths in tracked text without weakening either
   production guard or governance policy.

### Commits

| Commit | Purpose |
|---|---|
| `9726526` | Record the approved final-hardening design and execution plan. |
| `0f5eb0b` | Isolate storage bindings from runtime paths. |
| `a94b02e` | Bind resume to immutable prepared inputs. |
| `a47b46b` | Confine agent responses to the local exchange. |
| `d3b7888` | Map missing configured-LLM setup to the capability block and update operator docs. |
| `416f20f` | Keep negative-test fixtures synthetic under repository hygiene. |

### Final verification evidence

| Check | Exact result |
|---|---|
| Focused hygiene and changed-fixture regression | `4 passed in 2.97s` |
| Full pytest | `1666 passed, 5 skipped, 4 warnings, 183 subtests passed in 59.31s` |
| `governance/validate.py tools` | `0 errors, 0 warnings` |
| `governance/validate.py repository` | `0 errors, 0 warnings`; `tracked=337`, `scanned_text=336`, `skipped_binary=1`, `decode_errors=0` |
| `governance/validate.py all` | `0 errors, 0 warnings`; 4 loop packages consistent and repository hygiene clean |
| Changed production-module `py_compile` | exit code `0` for the eight hardening modules |
| `git diff --check 510d162..HEAD` and working-tree diff check | exit code `0`, no output |

The four full-suite warnings are pre-existing EasyOCR/PyTorch quantization
deprecation warnings. No push was performed. The user-owned untracked plan at
`docs/superpowers/plans/2026-07-26-local-agent-judgement-dry-run.md` remained
unmodified, unstaged, and uncommitted.

## Independent final-review follow-up (2026-07-27)

This follow-up supersedes the final verification counts above. Its
implementation commit is `be11365`; the evidence-only commit follows it.
Invoice production logic and invoice contracts remain outside this scope.

### Request and extracted-input sealing

Each `agent_judgement_input_snapshot.v1` item now also persists the original
validated `request_hash`. Resume rebuilds the formal request artifact through
the production validator, requires the validated hash to equal the snapshot
hash, and independently requires `extracted.source_ref` to equal the manifest
source reference. These checks run before response consumption and again under
the run lock through the existing owner validation boundary.

The public `DataCleaningTools.resume_agent_judgement_run()` regression modifies
a saved request candidate, recomputes a self-consistent request hash, supplies
a response bound to that modified hash, and verifies
`AGENT_JUDGEMENT.RUN_INPUT_INVALID` with every pre-consumption run artifact
byte unchanged. A second public regression changes the extracted source
reference and its snapshot digest together; it is rejected by the independent
manifest binding.

### Projection target protection

`FilesystemProjectionWriter._resolve()` now executes its target-level protected
root check before returning. Constructor overlap rejection remains unchanged.
The new regression changes the adapter's protected-root binding after safe
construction and proves the write-time guard preserves existing source bytes.

### Fresh verification

| Check | Exact result |
|---|---|
| Gateway and ProjectionWriter suites | `47 passed in 1.75s` |
| Expanded gateway, projection, CLI, dry-run, and archive-gate suites | `146 passed, 23 subtests passed in 25.53s` |
| Full pytest | `1669 passed, 5 skipped, 4 warnings, 183 subtests passed in 59.34s` |
| Governance tools | `0 errors, 0 warnings` |
| Governance repository | `0 errors, 0 warnings`; `tracked=337`, `scanned_text=336`, `skipped_binary=1`, `decode_errors=0` |
| Governance all | `0 errors, 0 warnings`; four loop packages consistent |
| Changed production-module `py_compile` | exit code `0` |
| `git diff --check 510d162..be11365` and working-tree diff check | exit code `0`, no output |

The four warnings remain the pre-existing EasyOCR/PyTorch quantization
deprecation warnings. The stable configured-LLM capability code is
`LLM.CAPABILITY_DISABLED`.
