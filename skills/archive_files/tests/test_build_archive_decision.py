"""test_build_archive_decision.py — build_archive_decision 三层 fallback 单元测试

按 #8 skill 收束：只测核心边界，不做过度设计。
- 5 个用例覆盖三层 fallback 的所有分支
- mock _search_knowledge_base / _llm_general_fallback，不调真实 LLM
- 不引入 pytest 等额外依赖（用 unittest stdlib）

硬编码 confidence 规则（evaluate_archive_decision）：
- 无 project_name：confidence = 0.0
- 有 project_name + 非丢标：confidence = 0.45（<0.5，触发 LLM 兜底）
- 有 project_name + 项目丢标：confidence = 0.72（>=0.5，走 hard 路径）
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from skills.archive_files.scripts import build_archive_decision as bad


def _run(source_file: str, project_name: str = "测试项目"):
    """调用 build_archive_action。

    extracted 含 project_name 让 evaluate_archive_decision 的 subject_name 有值。
    """
    return bad.build_archive_action(
        run_id="test-run-001",
        source_file=source_file,
        project_name=project_name,
        extracted={
            "document_type": "测试文档",
            "fields": {"project_name": project_name},  # evaluate_archive_decision 从 fields 取
        },
        ledger_result={"business_judgement": {}},
        project_files_dir="/tmp/test_archive",
    )


class ThreeLayerFallbackTests(unittest.TestCase):

    def test_kb_high_confidence_uses_kb(self):
        """知识库高置信度（>=0.7）→ 用知识库的 phase，reason=success。"""
        kb_result = {
            "knowledge_base_used": True,
            "phase": "项目执行",
            "confidence": 0.9,
            "reason": "success",
            "reasoning": "知识库明确命中",
        }
        with patch.object(bad, "_search_knowledge_base", return_value=kb_result):
            result = _run("/tmp/test/file.pdf")
        self.assertEqual(result["reason"], "success")
        self.assertTrue(result["knowledge_base_used"])
        self.assertEqual(result["archive_decision"]["archive_phase"], "项目执行")

    def test_kb_low_confidence_uses_hard_no_llm(self):
        """知识库低置信度 → 走硬编码，reason=knowledge_base_low_confidence（不触发 LLM 兜底）。

        边界约束（硬规则 #6）：kb_low_confidence 不触发 LLM 通用兜底。
        """
        kb_result = {
            "knowledge_base_used": True,
            "phase": "unknown",
            "confidence": 0.4,
            "reason": "knowledge_base_low_confidence",
            "reasoning": "知识库置信度不足",
        }
        with patch.object(bad, "_search_knowledge_base", return_value=kb_result), \
             patch.object(bad, "_llm_general_fallback") as mock_llm:
            # 用项目丢标路径 + 有 project_name → hard confidence = 0.72
            result = _run("/tmp/项目丢标/test/file.pdf")
        mock_llm.assert_not_called()
        self.assertEqual(result["reason"], "knowledge_base_low_confidence")
        self.assertTrue(result["knowledge_base_used"])

    def test_kb_missing_hard_sufficient_uses_hard(self):
        """知识库缺失 + 硬编码置信度足够 → 用硬编码，reason=success，不触发 LLM。"""
        kb_result = {
            "knowledge_base_used": False,
            "phase": "unknown",
            "confidence": 0.0,
            "reason": "knowledge_base_missing",
            "reasoning": "业务知识库目录为空",
        }
        with patch.object(bad, "_search_knowledge_base", return_value=kb_result), \
             patch.object(bad, "_llm_general_fallback") as mock_llm:
            # 项目丢标 + 有 project_name → hard confidence = 0.72 >= 0.5
            result = _run("/tmp/项目丢标/test/file.pdf")
        mock_llm.assert_not_called()
        self.assertTrue(result["knowledge_base_used"])
        self.assertEqual(result["reason"], "success")

    def test_kb_missing_hard_insufficient_triggers_llm(self):
        """知识库缺失 + 硬编码置信度不够 → 触发 LLM 通用兜底。"""
        kb_result = {
            "knowledge_base_used": False,
            "phase": "unknown",
            "confidence": 0.0,
            "reason": "knowledge_base_missing",
            "reasoning": "业务知识库目录为空",
        }
        llm_result = {
            "phase": "项目投标",
            "confidence": 0.6,
            "llm_fallback_used": True,
            "reasoning": "LLM 兜底推断",
        }
        with patch.object(bad, "_search_knowledge_base", return_value=kb_result), \
             patch.object(bad, "_llm_general_fallback", return_value=llm_result):
            # 路径不含 phase + 有 project_name → hard confidence = 0.45 < 0.5
            result = _run("/tmp/test/file.pdf")
        self.assertFalse(result["knowledge_base_used"])
        self.assertEqual(result["reason"], "success")

    def test_all_paths_fail_reason_source_missing(self):
        """所有路径都失败 + source_missing blocker → reason=source_missing（blockers 优先）。"""
        kb_result = {
            "knowledge_base_used": False,
            "phase": "unknown",
            "confidence": 0.0,
            "reason": "knowledge_base_no_match",
            "reasoning": "知识库无匹配",
        }
        llm_result = {
            "phase": "unknown",
            "confidence": 0.0,
            "llm_fallback_used": True,
            "reasoning": "LLM 也无法判断",
        }
        # 不存在的 source_file → source_missing blocker
        with patch.object(bad, "_search_knowledge_base", return_value=kb_result), \
             patch.object(bad, "_llm_general_fallback", return_value=llm_result):
            result = _run("/nonexistent/path/file.pdf")
        self.assertEqual(result["reason"], "source_missing")


if __name__ == "__main__":
    unittest.main()
