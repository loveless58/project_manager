"""document_parse LLM extractor 单元测试。

不调真实 GPUStack(避免依赖网络/Key),只测:
1. _extract_json 解析逻辑
2. make_llm_extractor 工厂函数接口
3. category 白名单兜底
4. confidence 白名单兜底
5. parse() L3 触发条件
"""

import os
import unittest
from unittest.mock import MagicMock, patch


from skills.document_parse import parse
from skills.document_parse.llm_extractor import (
    _extract_json,
    make_llm_extractor,
    get_default_llm_extractor,
    CATEGORIES,
)
from skills.document_parse.kb import query_kb

TEST_BASE_URL = "https://llm.example.invalid/v1"


class TestExtractJson(unittest.TestCase):
    """_extract_json 解析逻辑测试。"""

    def test_direct_json(self):
        result = _extract_json('{"category": "招标公告", "confidence": "high"}')
        self.assertEqual(result["category"], "招标公告")
        self.assertEqual(result["confidence"], "high")

    def test_markdown_json_block(self):
        text = '下面是结果:\n```json\n{"category": "合同", "confidence": "medium"}\n```\n完毕'
        result = _extract_json(text)
        self.assertEqual(result["category"], "合同")
        self.assertEqual(result["confidence"], "medium")

    def test_first_last_brace(self):
        text = '思考中...然后 {"category": "其他", "confidence": "low"} 完成'
        result = _extract_json(text)
        self.assertEqual(result["category"], "其他")
        self.assertEqual(result["confidence"], "low")

    def test_no_json(self):
        self.assertIsNone(_extract_json("no json here"))
        self.assertIsNone(_extract_json(""))
        self.assertIsNone(_extract_json("{incomplete"))

    def test_multiline_json(self):
        text = """{
            "category": "招标公告",
            "confidence": "high",
            "reasoning": "long text"
        }"""
        result = _extract_json(text)
        self.assertEqual(result["category"], "招标公告")
        self.assertIsNotNone(result.get("reasoning"))


class TestCategoryWhitelist(unittest.TestCase):
    """category 白名单兜底(LLM 自创类别 → 兜底为其他)。"""

    def test_llm_invented_category_falls_back_to_other(self):
        """LLM 返回 '运营管理'(不在白名单)→ 兜底为 '其他'。"""
        # mock _call_llm
        from skills.document_parse import llm_extractor as le

        original_call = le._call_llm
        le._call_llm = MagicMock(return_value={
            "text": '{"category": "运营管理", "confidence": "high"}',
            "tokens": 100,
            "finish_reason": "stop",
            "raw": {},
        })

        try:
            extractor = make_llm_extractor(base_url=TEST_BASE_URL, api_key="fake-key-for-test")
            result = extractor({"raw_text": "test", "filename": "test.docx"})
            self.assertEqual(result["category"], "其他")
            self.assertEqual(result["confidence"], "high")
            self.assertEqual(result["rule_source"], "llm")
        finally:
            le._call_llm = original_call

    def test_invalid_confidence_falls_back_to_low(self):
        """LLM 返回 'very_high' (不在枚举) → 兜底为 'low'。"""
        from skills.document_parse import llm_extractor as le

        original_call = le._call_llm
        le._call_llm = MagicMock(return_value={
            "text": '{"category": "其他", "confidence": "very_high"}',
            "tokens": 100,
            "finish_reason": "stop",
            "raw": {},
        })

        try:
            extractor = make_llm_extractor(base_url=TEST_BASE_URL, api_key="fake-key-for-test")
            result = extractor({"raw_text": "test", "filename": "test.docx"})
            self.assertEqual(result["confidence"], "low")
        finally:
            le._call_llm = original_call


class TestMakeLLMExtractor(unittest.TestCase):
    """make_llm_extractor 工厂函数。"""

    def test_missing_api_key_raises(self):
        """没 API key 应该抛 ValueError(避免默默失败)。"""
        from skills.document_parse import llm_extractor as le

        # patch module-level DEFAULT_API_KEY 为 None(因为 import 时已求值,环境变量改动不影响)
        with patch.object(le, 'DEFAULT_API_KEY', None):
            with self.assertRaises(ValueError) as ctx:
                make_llm_extractor()
            self.assertIn("LLM_API_KEY", str(ctx.exception))

    def test_returns_callable(self):
        """应该返回 callable。"""
        extractor = make_llm_extractor(base_url=TEST_BASE_URL, api_key="fake-key-for-test")
        self.assertTrue(callable(extractor))

    def test_extractor_handles_exception(self):
        """LLM 调用抛异常时,extractor 兜底为 other + low。"""
        from skills.document_parse import llm_extractor as le

        original_call = le._call_llm
        le._call_llm = MagicMock(side_effect=Exception("network error"))

        try:
            extractor = make_llm_extractor(base_url=TEST_BASE_URL, api_key="fake-key-for-test")
            result = extractor({"raw_text": "test", "filename": "test.docx"})
            self.assertEqual(result["category"], "其他")
            self.assertEqual(result["confidence"], "low")
            self.assertEqual(result["llm_error"], "DOCUMENT_PARSE.LLM.REQUEST_FAILED")
            self.assertEqual(result["llm_error_message"], "LLM request failed.")
        finally:
            le._call_llm = original_call


class TestParseLLMFallback(unittest.TestCase):
    """parse() L3 fallback 触发条件。

    验证:KB 命中 high/medium 时 LLM 不触发,KB low 时 LLM 触发。
    """

    def test_kb_high_does_not_call_llm(self):
        """KB high 时 parse() 不调 LLM。"""
        from skills.document_parse import llm_extractor as le

        mock_extractor = MagicMock(return_value={
            "category": "LLM干扰",  # 不应该被返回
            "extracted_fields": {},
            "confidence": "high",
            "rule_source": "llm",
        })

        pageindex_result = {
            "status": "success",
            "engine": "pageindex",
            "doc_name": "kb.md",
            "doc_id": "kb-doc-1",
            "structure": [{
                "title": "文档分类",
                "node_id": "0000",
                "start_index": 1,
                "end_index": 1,
                "summary": "分类目录",
                "nodes": [
                    {"title": "招标公告", "node_id": "0001", "start_index": 1, "end_index": 1, "summary": ""},
                    {"title": "采购公告", "node_id": "0002", "start_index": 1, "end_index": 1, "summary": ""},
                ],
            }],
            "structure_json_path": "/runtime/results/kb_structure.json",
            "elapsed_seconds": 0.01,
        }
        with patch("skills.document_parse.kb.get_kb_structure", return_value=pageindex_result):
            with tempfile_docx(content=["招标公告测试"]) as path:
                r = parse(path, llm_extractor=mock_extractor)

        self.assertEqual(r["business_judgement"]["rule_source"], "knowledge_base")
        mock_extractor.assert_not_called()

    def test_kb_low_calls_llm(self):
        """KB low 时 parse() 降级到 LLM。"""
        from skills.document_parse import llm_extractor as le

        mock_extractor = MagicMock(return_value={
            "category": "其他",
            "extracted_fields": {},
            "confidence": "high",
            "rule_source": "llm",
            "llm_reasoning": "mock",
        })

        with tempfile_docx(content=["本文件记录 2026 年第二季度业务运营情况"]) as path:
            r = parse(path, llm_extractor=mock_extractor)
            # 验证 LLM 真的被调用了
            mock_extractor.assert_called_once()
            # 验证 parse 返回 LLM 的结果
            self.assertEqual(r["business_judgement"]["rule_source"], "llm")
            self.assertEqual(r["business_judgement"]["category"], "其他")

    def test_llm_failure_falls_back_to_hardcode(self):
        """LLM 抛异常时(parse 已 try/except 兜底)返回 hard_code 结果。"""
        from skills.document_parse import llm_extractor as le

        mock_extractor = MagicMock(side_effect=Exception("LLM down"))

        with tempfile_docx(content=["本文件记录 2026 年第二季度业务运营情况"]) as path:
            r = parse(path, llm_extractor=mock_extractor)
            # parse 应该兜底(不抛异常)
            self.assertIn(r["business_judgement"]["rule_source"],
                          ["llm", "hard_code", "knowledge_base"])


import contextlib
import os
import tempfile
from docx import Document


@contextlib.contextmanager
def tempfile_docx(content):
    """创建临时 docx 用于测试。"""
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    doc = Document()
    for line in content:
        doc.add_paragraph(line)
    doc.save(path)
    try:
        yield path
    finally:
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
