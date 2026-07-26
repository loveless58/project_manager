# Platform foundation second-round review fixes

Date: 2026-07-25

Scope: repository hygiene, synthetic business fixtures, and node-local documentation boundaries.

## Review closure

- Replaced tracked business examples with explicitly synthetic projects, organizations, people, identifiers, amounts, tickets, and source paths.
- Extended credential detection to prefixed variable names while retaining environment-placeholder allowances.
- Removed substring-based absolute-path exemptions. Only strict synthetic fixture metadata or the explicit same-line synthetic-path marker is accepted.
- Added structured real-sample collection detection and dense structured-business-content detection, with negative tests for policy prose and synthetic examples.
- Updated the bid-files integration guide to derive paths through `AppSettings` and separate `business_root` input from node-local runtime output.
- Added a prominent correction to the historical platform plan and updated every audited runtime, SQLite, projection, environment, and directory-contract snippet.

## Verification evidence

- Repository hygiene tests: 30 passed.
- Loop tests: 96 passed, 16 subtests passed; 4 third-party deprecation warnings.
- Settings, documentation, repository-hygiene, and document-parse focused suite: 127 passed.
- Documentation and settings follow-up: 49 passed.
- Governance `all` under UTF-8 mode: 0 errors, 0 warnings; repository scan clean.
- `git diff --check`: clean.

The initial exact regression run produced the expected RED failures for prefixed credentials, path-substring exemptions, structured sample collections, structured business content, and the two tracked business-example files. Documentation contract tests likewise began with two expected failures before the corrections were applied.

## Final synthetic-fixture closure

- Lowered repository business-content detection to a single explicit organization, person, project, identifier, or subject value.
- Added bare YAML/INI/env credential detection while retaining strict environment and template placeholders.
- Added direct parsing for local-business JSON manifests regardless of filename hints.
- Added an independent tracked-tree synthetic-content contract with Python AST, recursive JSON-field, labeled-value, organization, identifier, and provenance checks.
- Preserved the ledger non-merge regression with two synthetic names whose normalized similarity is greater than 0.90 while their project directories remain distinct.
- Restored semantic field keys, business status enums, OCR labels, and parser input shapes after fixture substitution; only entity values remain synthetic.

## Final verification evidence

- Repository hygiene and independent content-contract suite: 60 passed.
- Contracts and archive focused suite: 58 passed.
- Adversarial verification and OCR focused suite: 43 passed.
- Feedback, field quality, goal validation, review queue, and document parsing focused suite: 44 passed.
- Loop suite: 96 passed, 16 subtests passed.
- Full suite: 938 passed, 5 skipped, 31 subtests passed; 4 third-party deprecation warnings.
- Governance `all` under UTF-8 mode: 0 errors, 0 warnings; repository hygiene scanned the complete tracked boundary and remained clean.
- `git diff --check`: clean.

## Business-file judgement usable-slice follow-up

The integration slice now uses an explicit synthetic C-001 catalog fixture and
two independent temporary bindings (source and archive). It exercises the real
native parsers, strict evidence/interpretation boundary, catalog provider,
retrieval ranking, PageIndex adapter, archive-plan writer, and CLI; only
network transports are faked.

Boundary corrections made during this follow-up:

- Native parser text is trimmed before it enters strict LLM evidence segments;
  the interpretation schema itself remains strict.
- Only interpretation-contract fields cross the retrieval/LLM boundary; full
  validated native fields remain in the extracted audit artifact.
- Scalar invoice-party normalization is limited to invoice document types, so
  malformed nested fields in contracts continue to block before downstream
  retrieval or interpretation.
- A successful PageIndex invocation is an indexing side effect, not
  business-match evidence. Its opaque provider reference therefore does not
  enter the strict LLM evidence request.
- Flattened invoice party fields are matched by both the JSON catalog provider
  and retrieval scoring, preserving exact candidate resolution without using
  invoice line items as project evidence.

Verification after the corrections:

- Document interpretation, retrieval, JSON catalog provider, Task 8
  integration, safe CLI, and business-judgement regression suite: 106 passed
  in 1.90s.
- The integration slice verifies retrieval -> PageIndex -> LLM, five native
  reviewable inputs, a blocked image-only PDF (OCR.CAPABILITY_DISABLED),
  preserved source hashes, a normalized review queue, no archive_result.json,
  and safe CLI native/scan summaries.
