import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestOcrProviderRegistry(unittest.TestCase):
    def setUp(self):
        # 清空 OCR provider 模块级缓存, 避免 mock 替换模块时跨测试污染 (commit bb579fe 后浮出)
        from ocr.providers.rapidocr_provider import _ENGINE_CACHE
        from ocr.providers.easyocr_provider import _READER_CACHE
        _ENGINE_CACHE.clear()
        _READER_CACHE.clear()

    def test_pdf_without_available_provider_returns_blocked_diagnostics(self):
        from ocr.provider_registry import extract_pdf_or_image

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "scanned.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n% placeholder\n")

            result = extract_pdf_or_image(
                pdf_path,
                dependency_probe=lambda name: False,
                binary_probe=lambda name: None,
            )

            self.assertEqual(result["schema_version"], "document.extract.v1")
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blocked_reason"], "ocr_adapter_unavailable")
            self.assertIn("engine_candidates", result)
            candidates = {item["engine"]: item for item in result["engine_candidates"]}
            self.assertEqual(candidates["pymupdf_text"]["available"], False)
            self.assertEqual(candidates["sidecar_text"]["available"], False)
            self.assertEqual(candidates["tesseract"]["available"], False)
            self.assertEqual(candidates["easyocr"]["available"], False)
            self.assertEqual(result["next_action"], "install_pymupdf_or_provide_ocr_sidecar")

    def test_sidecar_provider_extracts_pdf_text_without_heavy_dependencies(self):
        from ocr.provider_registry import extract_pdf_or_image

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "notice.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n% placeholder\n")
            with open(f"{pdf_path}.ocr.txt", "w", encoding="utf-8") as f:
                f.write("项目名称：旁路OCR项目\n采购人：测试客户")

            result = extract_pdf_or_image(
                pdf_path,
                dependency_probe=lambda name: False,
                binary_probe=lambda name: None,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["extract_method"], "ocr")
            self.assertEqual(result["ocr"]["engine"], "sidecar_text")
            self.assertIn("旁路OCR项目", result["extracted_text"])

    def test_easyocr_provider_is_optional_and_reports_missing_module(self):
        from ocr.providers.easyocr_provider import EasyOcrProvider

        provider = EasyOcrProvider(dependency_probe=lambda name: False)

        result = provider.extract("scan.png")

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["engine"], "easyocr")
        self.assertEqual(result["blocked_reason"], "ocr_provider_unavailable")
        self.assertIn("easyocr", result["error"])

    def test_easyocr_provider_extracts_text_when_module_is_available(self):
        from ocr.providers.easyocr_provider import EasyOcrProvider

        fake_easyocr = types.SimpleNamespace(
            Reader=lambda languages, gpu=False, verbose=False, model_storage_directory=None: types.SimpleNamespace(
                readtext=lambda path, detail=1, paragraph=False: [
                    ([[0, 0], [1, 0], [1, 1], [0, 1]], "项目名称：EasyOCR封装项目", 0.91),
                    ([[0, 2], [1, 2], [1, 3], [0, 3]], "采购人：测试客户", 0.83),
                ]
            )
        )

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "scan.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"easyocr": fake_easyocr}):
                provider = EasyOcrProvider(dependency_probe=lambda name: True, languages=("ch_sim", "en"))
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["engine"], "easyocr")
        self.assertIn("EasyOCR封装项目", result["text"])
        self.assertEqual(result["pages"][0]["confidence"], 0.87)

    def test_easyocr_provider_copies_unicode_image_path_before_ocr(self):
        from ocr.providers.easyocr_provider import EasyOcrProvider

        seen_paths = []

        def readtext(path, detail=1, paragraph=False):
            seen_paths.append(path)
            self.assertTrue(os.path.exists(path))
            self.assertNotIn("付款凭证", path)
            return [([[0, 0], [1, 0], [1, 1], [0, 1]], "项目名称：中文路径图片项目", 0.92)]

        fake_easyocr = types.SimpleNamespace(
            Reader=lambda languages, gpu=False, verbose=False, model_storage_directory=None: types.SimpleNamespace(
                readtext=readtext
            )
        )

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "付款凭证.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"easyocr": fake_easyocr}):
                provider = EasyOcrProvider(dependency_probe=lambda name: True)
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["engine"], "easyocr")
        self.assertEqual(len(seen_paths), 1)
        self.assertIn("中文路径图片项目", result["text"])

    def test_easyocr_low_confidence_keeps_text_for_human_review(self):
        from ocr.providers.easyocr_provider import EasyOcrProvider

        fake_easyocr = types.SimpleNamespace(
            Reader=lambda languages, gpu=False, verbose=False, model_storage_directory=None: types.SimpleNamespace(
                readtext=lambda path, detail=1, paragraph=False: [
                    ([[0, 0], [1, 0], [1, 1], [0, 1]], "收款人：中经国际招标集团有限公司", 0.33),
                ]
            )
        )

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "receipt.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"easyocr": fake_easyocr}):
                provider = EasyOcrProvider(dependency_probe=lambda name: True, min_confidence=0.55)
                result = provider.extract(image_path)

        self.assertEqual(result["status"], "success")
        self.assertIn("收款人", result["text"])
        self.assertEqual(result["quality"]["quality"], "poor")
        self.assertTrue(result["quality"]["needs_human_review"])

    def test_easyocr_reader_is_cached_across_provider_instances(self):
        from ocr.providers.easyocr_provider import EasyOcrProvider
        import ocr.providers.easyocr_provider as easyocr_provider

        easyocr_provider._READER_CACHE.clear()
        reader_count = {"count": 0}

        def make_reader(languages, gpu=False, verbose=False, model_storage_directory=None):
            reader_count["count"] += 1
            return types.SimpleNamespace(
                readtext=lambda path, detail=1, paragraph=False: [
                    ([[0, 0], [1, 0], [1, 1], [0, 1]], "项目名称：缓存验证项目", 0.9),
                ]
            )

        fake_easyocr = types.SimpleNamespace(Reader=make_reader)

        with tempfile.TemporaryDirectory() as td:
            first = os.path.join(td, "first.png")
            second = os.path.join(td, "second.png")
            for path in (first, second):
                with open(path, "wb") as f:
                    f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"easyocr": fake_easyocr}):
                EasyOcrProvider(dependency_probe=lambda name: True).extract(first)
                EasyOcrProvider(dependency_probe=lambda name: True).extract(second)

        self.assertEqual(reader_count["count"], 1)

    def test_easyocr_provider_renders_pdf_pages_before_ocr(self):
        from ocr.providers.easyocr_provider import EasyOcrProvider

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

        fake_easyocr = types.SimpleNamespace(
            Reader=lambda languages, gpu=False, verbose=False, model_storage_directory=None: types.SimpleNamespace(
                readtext=lambda path, detail=1, paragraph=False: [
                    ([[0, 0], [1, 0], [1, 1], [0, 1]], f"项目名称：PDF扫描项目-{os.path.basename(path)}", 0.9),
                ]
            )
        )
        fake_fitz = types.SimpleNamespace(open=lambda path: FakeDoc())

        with patch.dict(sys.modules, {"easyocr": fake_easyocr, "fitz": fake_fitz}):
            provider = EasyOcrProvider(dependency_probe=lambda name: name in {"easyocr", "fitz"})
            result = provider.extract("scan.pdf")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["pages"]), 2)
        self.assertEqual(len(seen_paths), 2)
        self.assertTrue(all(path.endswith(".png") for path in seen_paths))
        self.assertIn("PDF扫描项目", result["text"])

    def test_registry_uses_easyocr_as_pluggable_fallback_for_images(self):
        from ocr.provider_registry import extract_pdf_or_image

        fake_easyocr = types.SimpleNamespace(
            Reader=lambda languages, gpu=False, verbose=False, model_storage_directory=None: types.SimpleNamespace(
                readtext=lambda path, detail=1, paragraph=False: [
                    ([[0, 0], [1, 0], [1, 1], [0, 1]], "项目名称：图片识别项目", 0.88),
                ]
            )
        )

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "scan.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            with patch.dict(sys.modules, {"easyocr": fake_easyocr}):
                result = extract_pdf_or_image(
                    image_path,
                    dependency_probe=lambda name: name == "easyocr",
                    binary_probe=lambda name: None,
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["ocr"]["engine"], "easyocr")
        self.assertIn("图片识别项目", result["extracted_text"])

    def test_ofd_uses_same_stem_xml_sidecar_without_converter_dependency(self):
        from ocr.provider_registry import extract_pdf_or_image

        with tempfile.TemporaryDirectory() as td:
            ofd_path = os.path.join(td, "invoice.ofd")
            xml_path = os.path.join(td, "invoice.xml")
            with open(ofd_path, "wb") as f:
                f.write(b"fake ofd bytes")
            with open(xml_path, "w", encoding="utf-8") as f:
                f.write("<root><buyer>测试客户</buyer><amount>123.45</amount></root>")

            result = extract_pdf_or_image(
                ofd_path,
                dependency_probe=lambda name: False,
                binary_probe=lambda name: None,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["extract_method"], "ofd_sidecar")
        self.assertIn("测试客户", result["extracted_text"])
        self.assertIn("123.45", result["extracted_text"])

    def test_ofd_without_sidecar_returns_converter_unavailable_blocked(self):
        from ocr.provider_registry import extract_pdf_or_image

        with tempfile.TemporaryDirectory() as td:
            ofd_path = os.path.join(td, "invoice.ofd")
            with open(ofd_path, "wb") as f:
                f.write(b"fake ofd bytes")

            result = extract_pdf_or_image(
                ofd_path,
                dependency_probe=lambda name: False,
                binary_probe=lambda name: None,
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_reason"], "ofd_converter_unavailable")
        self.assertEqual(result["next_action"], "provide_ofd_sidecar_or_configure_ofd_converter")


if __name__ == "__main__":
    unittest.main()
