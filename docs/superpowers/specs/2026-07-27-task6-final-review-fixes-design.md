# Task 6 Final Review Fixes Design

## Goal

Close the final Important findings for local agent judgement without adding invoice-specific semantics: enforce bidirectional storage/runtime isolation, bind resume to immutable prepared inputs, restrict response import to a node-local exchange directory, and map known configured-LLM configuration failures to a stable safe block.

## Invariants

1. A runtime-owned path and a storage binding must be disjoint. Equality, runtime below binding, and binding below runtime are all invalid for `runtime_workspace`, `projection_root`, and local `sqlite_path`.
2. The invariant is enforced both while loading Settings and at runtime composition/direct `DataCleaningTools` boundaries. `FilesystemProjectionWriter` also receives protected storage roots so an invalid direct construction cannot overwrite a source sentinel.
3. Phase-1 agent input is immutable. A new `agent_judgement_input_snapshot.v1` artifact binds every ordered item to its request ID, logical `source_ref`, content hash, parse artifact reference, and canonical extracted-artifact digest.
4. Resume requires the original ordered source files at the public `DataCleaningTools`/Gateway boundary. Under the existing consumption lock, it recomputes source identity, logical reference, and content hash, then cross-checks snapshot, manifest, extracted artifact, and request. A mismatch is blocked before review artifacts are committed.
5. Agent responses are read only from `<runtime_workspace>/agent-host-responses`. Network syntax, runtime-external paths, storage-binding paths, symlink/reparse paths, and non-files are rejected. External import is not implemented.
6. `ProviderConfigurationError` is an expected configured-LLM capability/configuration block: redacted JSON, `LLM.CAPABILITY_DISABLED`, exit `2`. Unrelated exceptions remain `BUSINESS_FILE_RUN.UNEXPECTED`, exit `1`.
7. No agent response grants feedback, ledger, archive, confirmation, or invoice authority.

## Components and data flow

### Path isolation

A shared path-overlap predicate treats either path being relative to the other as overlap. `load_app_settings` applies it after canonical path and node-local validation. `build_runtime_adapters` repeats the check for manually constructed `AppSettings`. `DataCleaningTools` applies the runtime-workspace subset for direct construction. The filesystem writer takes protected roots and refuses an overlapping root or protected target.

### Prepared-input snapshot and resume

Phase 1 continues to emit the existing host-facing request schema. After extracted artifacts and the manifest are written, the owner writes a separate snapshot:

```json
{
  "schema_version": "agent_judgement_input_snapshot.v1",
  "run_id": "run_<32 hex>",
  "items": [
    {
      "request_id": "agent-request:<hash-prefix>",
      "source_ref": {"storage_provider": "local", "object_key": "contract.md", "logical_uri": "business://source/contract.md", "binding_id": "source"},
      "content_hash": "<sha256>",
      "parse_artifact_ref": "artifact:parsed:<hash-prefix>",
      "extracted_digest": "<canonical-json-sha256>"
    }
  ]
}
```

`resume_agent_judgement_run(run_id, response_path, files)` forwards all three inputs to the Gateway. The Gateway validates response locality and schema, then calls the owner consumer with the ordered files. The owner acquires the existing feedback lock, validates all immutable bindings, derives candidate/review payloads, rechecks one-time consumption, and atomically commits. Invalid static input is prevalidated once before locking and revalidated under the lock; both checks use the same owner method.

### Response exchange

The response root is fixed from the owner workspace and created during phase 1. Host tools must write their strict response JSON there. The Gateway validates raw network syntax before resolution and rejects every existing symlink/reparse component from the exchange root through the response file. It never copies an external file into runtime.

### CLI errors

The CLI catches only `ProviderConfigurationError` in the new safe branch. Missing/blank endpoint sources or credential produce a capability block. A blank standard endpoint may still resolve through a documented legacy endpoint. Other exceptions continue through the existing unexpected branch.

## Testing

- Table-driven Settings and runtime tests cover source/archive bindings equal to or below runtime, projection, and SQLite roots, plus the existing opposite direction.
- A real `FilesystemProjectionWriter` sentinel test proves protected source content is byte-identical.
- Gateway tests mutate manifest `source_ref`, extracted content, request binding, source content, and same-content logical path; every invalid resume leaves prior artifacts unchanged.
- Locality tests cover runtime-external, storage binding, network syntax, symlink/reparse, and valid exchange response paths.
- CLI tests cover endpoint/key missing, blank standard values, legacy fallback, and a genuine unexpected exception.
- Final evidence records fresh focused, full pytest, governance, compile, and diff results for `510d162..new HEAD`.

## Deferred scope

PostgreSQL/Synology coordination, external response import, and invoice PDF/OCR/action/schema/ledger/classification refinements remain deferred.
