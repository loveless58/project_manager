from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List


def build_candidate_test_manifest(
    *,
    run_id: str,
    parser_candidates: List[Dict[str, Any]],
    rule_candidates: List[Dict[str, Any]],
    artifacts: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "schema_version": "candidate_test_generation.v1",
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "status": "success" if parser_candidates or rule_candidates else "empty",
        "parser_candidate_count": len(parser_candidates),
        "rule_candidate_count": len(rule_candidates),
        "artifacts": artifacts,
        "next_actions": ["review_generated_tests", "promote_approved_tests_to_repo"],
        "boundary": {
            "modified_repo_tests": False,
            "updated_business_rules": False,
            "archive_plan_executed": False,
        },
    }


def render_parser_candidate_tests(parser_candidates: List[Dict[str, Any]]) -> str:
    return _render_candidate_unittest(
        class_name="ParserCandidateDraftTests",
        candidates=parser_candidates,
        test_method_body="""
        for item in CANDIDATES:
            self.assertEqual(item.get("schema_version"), "parser_test_candidate.v1")
            self.assertTrue(item.get("feedback_id"))
            self.assertTrue(item.get("run_id"))
            self.assertTrue(item.get("item_id"))
            self.assertTrue(item.get("field"))
            self.assertTrue(item.get("input_pattern") or item.get("expected_value"))
            self.assertEqual(item.get("status"), "pending_test_authoring")
""",
    )


def render_rule_candidate_tests(rule_candidates: List[Dict[str, Any]]) -> str:
    return _render_candidate_unittest(
        class_name="RuleCandidateDraftTests",
        candidates=rule_candidates,
        test_method_body="""
        for item in CANDIDATES:
            self.assertEqual(item.get("schema_version"), "rule_candidate.v1")
            self.assertTrue(item.get("feedback_id"))
            self.assertTrue(item.get("feedback_type"))
            self.assertTrue(item.get("run_id"))
            self.assertTrue(item.get("item_id"))
            self.assertTrue(item.get("decision"))
            self.assertTrue(item.get("risk_level"))
            self.assertIs(item.get("requires_test"), True)
            self.assertEqual(item.get("status"), "pending_rule_approval")
""",
    )


def _render_candidate_unittest(
    *,
    class_name: str,
    candidates: List[Dict[str, Any]],
    test_method_body: str,
) -> str:
    payload = json.dumps(candidates, ensure_ascii=False, indent=2, sort_keys=True)
    return f'''# -*- coding: utf-8 -*-
"""Generated candidate test draft.

This file is intentionally self-contained. It validates feedback-derived
candidate shape so reviewers can promote approved cases into repository tests.
"""
import json
import unittest


CANDIDATES_JSON = r"""{payload}"""
CANDIDATES = json.loads(CANDIDATES_JSON)


class {class_name}(unittest.TestCase):
    def test_candidates_are_well_formed(self):
        self.assertIsInstance(CANDIDATES, list)
{test_method_body.rstrip()}


if __name__ == "__main__":
    unittest.main()
'''
