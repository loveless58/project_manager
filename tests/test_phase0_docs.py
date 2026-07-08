import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


class Phase0DocumentationTests(unittest.TestCase):
    def test_l3_loop_architecture_doc_captures_review_gated_boundary(self):
        doc_path = PROJECT_DIR / "docs" / "file_organization_l3_loop.md"

        self.assertTrue(doc_path.exists())
        content = doc_path.read_text(encoding="utf-8")
        for required in [
            "prepare_file_organization_run",
            "verify_file_organization_run",
            "audit_file_organization_run",
            "prepare_feedback_form",
            "apply_feedback_form",
            "apply_feedback_decisions",
            "generate_candidate_tests",
            "execute_archive_plan",
            "archive_execution_gate.json",
            "不移动文件",
            "提交分组",
        ]:
            self.assertIn(required, content)


if __name__ == "__main__":
    unittest.main()
