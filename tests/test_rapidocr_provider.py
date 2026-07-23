import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestRapidOcrProvider(unittest.TestCase):
    def setUp(self):
        # 清空 OCR provider 模块级缓存, 避免 mock 替换模块时跨测试污染 (commit bb579fe 后浮出)
        from ocr.providers.rapidocr_provider import _ENGINE_CACHE
        from ocr.providers.easyocr_provider import _READER_CACHE
        _ENGINE_CACHE.clear()
        _READER_CACHE.clear()

    def test_rapidocr_provider_is_optional_and_reports_missing_module(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        provider = RapidOcrProvider(dependency_probe=lambda name: False)

        result = provider.extract("scan.png")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["engine"], "rapidocr")
        self.assertEqual(result["blocked_reason"], "ocr_provider_unavailable")
        self.assertIn("rapidocr_onnxruntime", result["error"])

    def test_rapidocr_provider_extracts_text_when_module_is_available(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        # RapidOCR() 返回可调用实例;engine(path) -> (result, elapse)
        # result 格式: [[box, text, confidence], ...]
        # box 是 4 角点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        fake_engine = lambda path: (
            [
                [[[0, 0], [1, 0], [1, 1], [0, 1]], "项目名称：RapidOCR封装项目", 0.93],
                [[[0, 2], [1, 2], [1, 3], [0, 3]], "采购人：测试客户", 0.81],
            ],
            [0.05, 0.10, 0.20],  # elapse 三段耗时
        )
        fake_rapidocr = types.SimpleNamespace(RapidOCR=lambda: fake_engine)

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "scan.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_rapidocr}):
                provider = RapidOcrProvider(dependency_probe=lambda name: True)
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["engine"], "rapidocr")
        self.assertIn("RapidOCR封装项目", result["text"])
        self.assertIn("采购人", result["text"])
        # 置信度应该被记录下来
        mean_conf = result["pages"][0]["confidence"]
        self.assertIsNotNone(mean_conf)
        self.assertAlmostEqual(mean_conf, 0.87, places=2)

    def test_rapidocr_low_confidence_keeps_text_for_human_review(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        fake_engine = lambda path: (
            [
                [[[0, 0], [1, 0], [1, 1], [0, 1]], "收款人：中经国际招标集团有限公司", 0.33],
            ],
            [0.05, 0.10, 0.20],
        )
        fake_rapidocr = types.SimpleNamespace(RapidOCR=lambda: fake_engine)

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "receipt.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_rapidocr}):
                provider = RapidOcrProvider(dependency_probe=lambda name: True)
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "success")
        self.assertIn("收款人", result["text"])
        # 低置信度应该触发人工复核信号
        self.assertEqual(result["quality"]["quality"], "poor")
        self.assertTrue(result["quality"]["needs_human_review"])
        self.assertIn("low_mean_confidence", result["quality"]["reasons"])

    def test_rapidocr_engine_is_cached_across_provider_instances(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider
        import ocr.providers.rapidocr_provider as rapidocr_provider

        rapidocr_provider._ENGINE_CACHE.clear()
        engine_count = {"count": 0}

        def make_engine():
            engine_count["count"] += 1
            return lambda path: (
                [[[[0, 0], [1, 0], [1, 1], [0, 1]], "项目名称：缓存验证项目", 0.9]],
                [0.05, 0.10, 0.20],
            )

        fake_rapidocr = types.SimpleNamespace(RapidOCR=make_engine)

        with tempfile.TemporaryDirectory() as td:
            first = os.path.join(td, "first.png")
            second = os.path.join(td, "second.png")
            for path in (first, second):
                with open(path, "wb") as f:
                    f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_rapidocr}):
                RapidOcrProvider(dependency_probe=lambda name: True).extract(first)
                RapidOcrProvider(dependency_probe=lambda name: True).extract(second)

        # 两次调用只实例化一次引擎
        self.assertEqual(engine_count["count"], 1)

    def test_rapidocr_provider_renders_pdf_pages_before_ocr(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        seen_paths = []

        class FakePix:
            def save(self, path):
                seen_paths.append(path)
                with open(path, "wb") as f:
                    f.write(b"png")

        class FakePage:
            def get_pixmap(self, dpi=200):
                return FakePix()

        class FakeDoc:
            def __iter__(self):
                return iter([FakePage(), FakePage()])

            def close(self):
                pass

        fake_engine = lambda path: (
            [[[[0, 0], [1, 0], [1, 1], [0, 1]],
               f"PDF扫描项目-{os.path.basename(path)}", 0.91]],
            [0.05, 0.10, 0.20],
        )
        fake_rapidocr = types.SimpleNamespace(RapidOCR=lambda: fake_engine)
        fake_fitz = types.SimpleNamespace(open=lambda path: FakeDoc())

        with patch.dict(sys.modules, {
            "rapidocr_onnxruntime": fake_rapidocr,
            "fitz": fake_fitz,
        }):
            provider = RapidOcrProvider(
                dependency_probe=lambda name: name in {"rapidocr_onnxruntime", "fitz"}
            )
            result = provider.extract("scan.pdf")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(len(seen_paths), 2)
        self.assertTrue(all(path.endswith(".png") for path in seen_paths))
        self.assertIn("PDF扫描项目", result["text"])

    def test_rapidocr_provider_returns_blocked_when_pdf_renderer_missing(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        fake_engine = lambda path: ([], [0.0])
        fake_rapidocr = types.SimpleNamespace(RapidOCR=lambda: fake_engine)

        with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_rapidocr}):
            provider = RapidOcrProvider(
                dependency_probe=lambda name: name == "rapidocr_onnxruntime"
            )
            result = provider.extract("scan.pdf")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "pdf_renderer_unavailable")
        self.assertIn("fitz", result["error"].lower())

    def test_rapidocr_provider_empty_result_is_blocked(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        fake_engine = lambda path: ([], [0.05, 0.10, 0.20])
        fake_rapidocr = types.SimpleNamespace(RapidOCR=lambda: fake_engine)

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "blank.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_rapidocr}):
                provider = RapidOcrProvider(dependency_probe=lambda name: True)
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "ocr_empty_text")

    def test_rapidocr_provider_classified_network_error_as_model_unavailable(self):
        from ocr.providers.rapidocr_provider import RapidOcrProvider

        fake_rapidocr = types.SimpleNamespace(
            RapidOCR=lambda: (_ for _ in ()).throw(
                RuntimeError("Failed to download model: network unreachable")
            )
        )

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "scan.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_rapidocr}):
                provider = RapidOcrProvider(dependency_probe=lambda name: True)
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "ocr_model_unavailable")


if __name__ == "__main__":
    unittest.main()
