# Safe business-file judgement CLI quickstart

`prepare_business_file_run.py` creates a reviewable business-file judgement run. It never automatically applies feedback and never physically archives, moves, overwrites, renames, or deletes a source file.

## Safety boundary

- Every run supplies `--config`, `--context`, `--source-binding`, and explicit files. `--target-binding` is optional; if supplied it must be enabled, writable, and have the `archive_target` role.
- Binding IDs and logical URIs identify data across nodes. A physical path belongs only in the local node configuration. SynologyDrive is only one possible storage binding, never a unique root or default archive target.
- Runtime workspace, SQLite, caches, LLM responses, and run artifacts must remain on the current node's local non-synced disk and outside every storage binding.
- The CLI injects `DisabledOcrProvider`. Native text PDF, DOCX, XLSX, Markdown, and XML use their native parsers. A scanned or image-only PDF returns `OCR.CAPABILITY_DISABLED`; this CLI does not probe or run EasyOCR, RapidOCR, PaddleOCR, MinerU, Tesseract, macOS Vision, or an OFD converter.

## Configure bindings

Copy the repository example configuration to a protected local configuration location and set the physical roots for the executing node. The following IDs can stay stable while each node uses its own physical mount:

```json
{
  "deployment_mode": "local",
  "runtime_workspace": "<node-local-runtime>",
  "storage_bindings": [
    {"binding_id": "incoming", "provider": "local", "node_id": "win-ops", "logical_root": "business://incoming/", "physical_root": "<source-mount>", "roles": ["source"], "readable": true, "writable": false},
    {"binding_id": "archive", "provider": "local", "node_id": "win-ops", "logical_root": "business://archive/", "physical_root": "<archive-mount>", "roles": ["archive_target"], "readable": false, "writable": true}
  ]
}
```

On macOS, a node can map `physical_root` to `<mac-source-mount>`; on a remote Synology node it may be `<synology-source-mount>`. Both can still use `business://incoming/`, but each must use a node-local runtime. Never put runtime state on SynologyDrive, SMB/NFS, OneDrive, or another sync volume.

## Catalog and LLM environment

`--context` names exactly one schema-valid `business_context_catalog.v1` JSON file; do not recursively discover a catalog from business directories. Keep LLM credentials in the execution environment only, not in config, catalog, stdout, or artifacts:

```powershell
$env:LLM_API_KEY = "<secret>"
$env:LLM_MODEL = "<approved-model>"
$env:LLM_BASE_URL = "https://<approved-endpoint>/v1"
```

## Run

Windows PowerShell:

```powershell
python -X utf8 -B scripts/prepare_business_file_run.py --config <node-local-config> --context <node-local-catalog> --source-binding incoming --target-binding archive <bound-source-file>
```

macOS or Synology shell:

```bash
python3 -X utf8 -B scripts/prepare_business_file_run.py --config /opt/project-manager/config.json --context /opt/project-manager/business_context_catalog.json --source-binding incoming <bound-source-file>
```

Exit `0` means review artifacts were prepared; exit `2` means a safe block; exit `1` is an unexpected error. Stdout is redacted JSON: it contains run ID, binding IDs, statuses, failure codes, and artifact names, but never physical paths or credentials.

## Inspect artifacts

Inspect `runtime_workspace/runs/<run_id>/` on the local node for `input_manifest.json`, `candidate_interpretations.json`, `archive_intents.json`, `review_queue.json`, `adversarial_verification.json`, `audit_review.json`, `feedback_form.json`, `feedback_form.md`, `planned_archive_actions.json`, and trace data.

The CLI calls `execute_archive_plan(run_id, confirmed=False)` exactly once. It does not call `confirmed=True` or a feedback-application method. Therefore it does not create `archive_result.json` and performs no physical archive. A scanned-file result with `OCR.CAPABILITY_DISABLED` is an expected safety stop: supply a natively readable PDF/DOCX/XLSX/Markdown/XML or use a separately approved OCR workflow.
