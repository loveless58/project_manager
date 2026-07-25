# Task 4 — structured document interpretation report

## Scope

- Commit: `feat: add structured document interpretation` (this task's single implementation commit; its final hash is verified in the handoff).
- Baseline: `424d4427e121d0335f76bd6c4627f15f06977c5c`
- Adds the strict `candidate_document_interpretation.v1` contract, a controlled retrieval-to-LLM evidence boundary, a `DocumentInterpreter` port, and an OpenAI-compatible JSON-only adapter.

## RED

1. Before implementation, the requested contract/service/integration tests failed at collection with the expected missing `contracts.document_interpretation` module.
2. A relation-bearing `blocked` response initially failed because the service incorrectly downgraded it to `needs_review`; the regression test then passed after preserving `blocked`.
3. A synthetic response larger than 32,000 bytes initially passed the schema; the new regression test passed after the contract added a size ceiling.
4. The new public port export test initially failed with `ImportError`, then passed after the explicit `platform_core.ports` export was added.

## GREEN

Focused command (using the repository's Windows Python through WSL because WSL itself has no pytest):

```text
python -X utf8 -B -m pytest tests/contracts/test_document_interpretation.py tests/services/test_document_interpretation.py tests/integrations/test_openai_compatible_interpreter.py tests/test_final_fix_llm_error_redaction.py -q
```

Result: `17 passed in 0.20s`.

Coverage includes duplicate keys, unknown fields, non-finite numbers, type and confidence validation, required evidence/version fields, malformed adapter output, bounded response payloads, controlled evidence serialization, relation review gating, stable error redaction, 60-second timeout, `temperature=0.1`, JSON-only output, and fake transport only.

## Full suite

The WSL wrapper ends a single process at roughly 30 seconds, so the complete suite was executed in two disjoint sets:

- `--ignore=tests/database --ignore=tests/adversarial_verification`: `994 passed, 4 skipped, 4 warnings, 31 subtests passed in 26.87s`.
- `tests/database tests/adversarial_verification`: `342 passed, 1 skipped in 9.67s`.

Combined result: `1336 passed, 5 skipped`; the four warnings are pre-existing optional dependency warnings. `git diff --check` passed.

## Self-review

- Only the normalized parse artifact reference, type hint, declared candidate fields/text segments, and allow-listed retrieval evidence cross the LLM boundary. Candidate document paths and unknown input keys do not.
- The response is fail-closed: duplicate keys, unknown schema fields, NaN/Infinity, invalid types/ranges, missing evidence/version fields, untraceable evidence, model/adapter identity mismatch, and oversized payloads are all blocked while retaining `parse_artifact_ref`.
- No SQL authority exists in this slice: `confirmed` is always `False`; a non-empty relation upgrades only `success` to `needs_review` and never weakens `blocked`.
- The adapter does not expose exception text, headers, credentials, URLs, or paths; it returns stable public codes and never makes a real request in tests.

## Concerns

- The existing task worktree has a Windows ACL issue that rejects the app-level patch utility for edits to pre-existing files. The minimal changes to `common/provider_config.py` and port exports were applied as audited unified diffs through WSL's `git apply`; all normal source/test changes remain reviewable in the final commit.
- The adapter intentionally uses the existing project model default when `LLM_MODEL` is unset, preserving document-parse compatibility while centralizing URL/key/model resolution helpers in `common.provider_config`.
