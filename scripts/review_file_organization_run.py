#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from tools.data_cleaning_tools import DataCleaningTools  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or apply a file-organization feedback form.")
    parser.add_argument("--workspace", required=True, help="Data-cleaning workspace root.")
    parser.add_argument("--run-id", required=True, help="Run id under <workspace>/runs.")
    parser.add_argument("--form", default="", help="Path to a filled feedback_form.json. Defaults to run artifact.")
    parser.add_argument("--apply", action="store_true", help="Apply a filled feedback form.")
    parser.add_argument("--prepare-only", action="store_true", help="Only prepare feedback_form artifacts.")
    parser.add_argument("--no-generate-tests", action="store_true", help="Do not generate candidate tests after apply.")
    args = parser.parse_args()

    tools = DataCleaningTools(workspace_dir=args.workspace)
    if args.apply and not args.prepare_only:
        result = tools.apply_feedback_form(
            run_id=args.run_id,
            feedback_form_path=args.form,
            generate_tests=not args.no_generate_tests,
        )
    else:
        result = tools.prepare_feedback_form(args.run_id)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
