import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestDataCleaningOcrProvider(unittest.TestCase):
    def setUp(self):
        # 清空 OCR provider 模块级缓存, 避免 mock 替换模块时跨测试污染 (commit bb579fe 后浮出)
        from ocr.providers.rapidocr_provider import _ENGINE_CACHE
        from ocr.providers.easyocr_provider import _READER_CACHE
        _ENGINE_CACHE.clear()
        _READER_CACHE.clear()

    def test_extract_document_routes_pdf_through_project_ocr_provider(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "notice.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n% placeholder\n")

            provider_result = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": pdf_path,
                "filename": "notice.pdf",
                "file_type": ".pdf",
                "extract_method": "ocr",
                "text_length": 28,
                "extracted_text": "项目名称：封装OCR项目\n采购人：测试客户",
                "is_scanned": True,
                "fields": {},
                "ocr": {
                    "schema_version": "ocr.result.v1",
                    "status": "success",
                    "engine": "fake-provider",
                    "text": "项目名称：封装OCR项目\n采购人：测试客户",
                    "pages": [],
                    "quality": {"needs_human_review": False},
                },
                "engine_candidates": [{"engine": "fake-provider", "available": True, "reason": "ready"}],
            }

            with patch("ocr.provider_registry.extract_pdf_or_image", return_value=provider_result) as provider:
                extracted = DataCleaningTools(workspace_dir=td).extract_document(pdf_path)

            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[0], pdf_path)
            self.assertEqual(provider.call_args.kwargs["ocr_adapter"].__name__, "_default_ocr_adapter")
            self.assertEqual(extracted["status"], "success")
            self.assertEqual(extracted["ocr"]["engine"], "fake-provider")
            self.assertEqual(extracted["fields"]["project_name"], "封装OCR项目")
            self.assertEqual(extracted["engine_candidates"][0]["engine"], "fake-provider")

    def test_explicit_ocr_adapter_remains_higher_priority_than_default_provider(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "scan.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            def explicit_adapter(path):
                return {"status": "success", "engine": "explicit", "text": "项目名称：显式OCR项目"}

            provider_result = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": image_path,
                "filename": "scan.png",
                "file_type": ".png",
                "extract_method": "ocr",
                "text_length": 14,
                "extracted_text": "项目名称：显式OCR项目",
                "is_scanned": True,
                "fields": {},
                "ocr": {
                    "schema_version": "ocr.result.v1",
                    "status": "success",
                    "engine": "explicit",
                    "text": "项目名称：显式OCR项目",
                    "pages": [],
                    "quality": {"needs_human_review": False},
                },
                "engine_candidates": [],
            }

            with patch("ocr.provider_registry.extract_pdf_or_image", return_value=provider_result) as provider:
                extracted = DataCleaningTools(workspace_dir=td, ocr_adapter=explicit_adapter).extract_document(image_path)

            provider.assert_called_once_with(image_path, ocr_adapter=explicit_adapter)
            self.assertEqual(extracted["status"], "success")
            self.assertEqual(extracted["ocr"]["engine"], "explicit")

    def test_blocked_pdf_keeps_provider_diagnostics(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            pdf_path = os.path.join(td, "blocked.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n% placeholder\n")

            provider_result = {
                "schema_version": "document.extract.v1",
                "status": "blocked",
                "blocked_reason": "ocr_adapter_unavailable",
                "error": "No OCR/PDF provider available for this file",
                "file": pdf_path,
                "filename": "blocked.pdf",
                "file_type": ".pdf",
                "extract_method": "ocr",
                "text_length": 0,
                "extracted_text": "",
                "is_scanned": True,
                "fields": {},
                "ocr": {"schema_version": "ocr.result.v1", "status": "blocked", "blocked_reason": "ocr_adapter_unavailable"},
                "engine_candidates": [
                    {"engine": "pymupdf_text", "available": False, "reason": "module_not_installed"},
                ],
                "next_action": "install_pymupdf_or_provide_ocr_sidecar",
            }

            with patch("ocr.provider_registry.extract_pdf_or_image", return_value=provider_result):
                extracted = DataCleaningTools(workspace_dir=td).extract_document(pdf_path)

            self.assertEqual(extracted["status"], "blocked")
            self.assertEqual(extracted["blocked_reason"], "ocr_adapter_unavailable")
            self.assertEqual(extracted["engine_candidates"][0]["reason"], "module_not_installed")
            self.assertEqual(extracted["next_action"], "install_pymupdf_or_provide_ocr_sidecar")

    def test_extract_document_routes_ofd_through_project_provider(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            ofd_path = os.path.join(td, "invoice.ofd")
            with open(ofd_path, "wb") as f:
                f.write(b"fake ofd bytes")

            provider_result = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": ofd_path,
                "filename": "invoice.ofd",
                "file_type": ".ofd",
                "extract_method": "ofd_sidecar",
                "text_length": 20,
                "extracted_text": "项目名称：OFD发票项目\n采购人：测试客户",
                "is_scanned": False,
                "fields": {},
                "engine_candidates": [],
            }

            with patch("ocr.provider_registry.extract_pdf_or_image", return_value=provider_result) as provider:
                extracted = DataCleaningTools(workspace_dir=td).extract_document(ofd_path)

            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[0], ofd_path)
            self.assertEqual(extracted["status"], "success")
            self.assertEqual(extracted["extract_method"], "ofd_sidecar")
            self.assertEqual(extracted["fields"]["project_name"], "OFD发票项目")

    def test_payment_receipt_ocr_text_extracts_structured_payment_fields(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "付款凭证.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            provider_result = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": image_path,
                "filename": "付款凭证.png",
                "file_type": ".png",
                "extract_method": "ocr",
                "text_length": 120,
                "extracted_text": "\n".join([
                    "交易日期: 2026年06月26日",
                    "付款人: 北京华胜天成科技股份有限公司",
                    "收款人: 中经国际招标集团有限公司",
                    "交易金额 (小写): 800.00",
                    "交易摘要: CEITCL-BJ04-2606013-01标书费",
                ]),
                "is_scanned": True,
                "fields": {},
                "ocr": {
                    "schema_version": "ocr.result.v1",
                    "status": "success",
                    "engine": "easyocr",
                    "text": "",
                    "pages": [],
                    "quality": {"needs_human_review": False},
                },
                "engine_candidates": [],
            }

            with patch("ocr.provider_registry.extract_pdf_or_image", return_value=provider_result):
                extracted = DataCleaningTools(workspace_dir=td).extract_document(image_path)

            self.assertEqual(extracted["fields"]["payer"], "北京华胜天成科技股份有限公司")
            self.assertEqual(extracted["fields"]["payee"], "中经国际招标集团有限公司")
            self.assertEqual(extracted["fields"]["payment_amount"], "800.00")
            self.assertEqual(extracted["fields"]["payment_date"], "2026年06月26日")
            self.assertEqual(extracted["fields"]["payment_summary"], "CEITCL-BJ04-2606013-01标书费")

    def test_payment_amount_tolerates_common_ocr_confusions(self):
        from tools.data_cleaning_tools import DataCleaningTools

        fields = DataCleaningTools(workspace_dir=tempfile.mkdtemp())._extract_fields(
            "交易金额 (小写) : CI8O0. 叩\n"
        )

        self.assertEqual(fields["payment_amount"], "800.00")

    def test_project_text_fields_are_enriched_by_contract_parsers(self):
        from tools.data_cleaning_tools import DataCleaningTools

        fields = DataCleaningTools(workspace_dir=tempfile.mkdtemp())._extract_fields(
            "\n".join([
                "项目金额：2,000,000 元（预算）",
                "报名截止：2026-05-20",
                "投标截止/开标：2026-05-26 09:30",
            ])
        )

        self.assertEqual(fields["amount"], 2_000_000.0)
        self.assertEqual(fields["amount_label"], "预算")
        self.assertEqual(fields["registration_deadline"], "2026-05-20")
        self.assertEqual(fields["bid_deadline"], "2026-05-26")
        self.assertEqual(fields["bid_open_time"], "2026-05-26")
        self.assertEqual(fields["deadline"], "2026-05-26")

    def test_ocr_provider_fields_are_mapped_to_contract_ledger_fields(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            image_path = os.path.join(td, "招标公告.png")
            with open(image_path, "wb") as f:
                f.write(b"fake image bytes")

            provider_result = {
                "schema_version": "document.extract.v1",
                "status": "success",
                "file": image_path,
                "filename": "招标公告.png",
                "file_type": ".png",
                "document_type": "招标公告",
                "extract_method": "ocr",
                "text_length": 18,
                "extracted_text": "招标公告\n项目名称：OCR字段映射项目",
                "is_scanned": True,
                "fields": {
                    "buyer": "测试客户",
                    "date": "2026-05-26",
                    "project_no": "ABC12345",
                    "amount": "800 元",
                },
                "ocr": {
                    "schema_version": "ocr.result.v1",
                    "status": "success",
                    "engine": "fake-provider",
                    "text": "",
                    "pages": [],
                    "quality": {"needs_human_review": False},
                },
                "engine_candidates": [],
            }

            with patch("ocr.provider_registry.extract_pdf_or_image", return_value=provider_result):
                extracted = DataCleaningTools(workspace_dir=td).extract_document(image_path)

            self.assertEqual(extracted["document_type"], "招标公告")
            self.assertEqual(extracted["fields"]["customer"], "测试客户")
            self.assertEqual(extracted["fields"]["bid_deadline"], "2026-05-26")
            self.assertEqual(extracted["fields"]["deadline"], "2026-05-26")
            self.assertEqual(extracted["fields"]["bid_code"], "ABC12345")
            self.assertEqual(extracted["fields"]["amount"], 800.0)

    def test_internal_project_markdown_builds_ready_archive_action(self):
        from tools.data_cleaning_tools import DataCleaningTools

        with tempfile.TemporaryDirectory() as td:
            source_path = os.path.join(td, "PRD-project-manager-ocr.md")
            with open(source_path, "w", encoding="utf-8") as f:
                f.write("# PRD\nproject_manager OCR provider design")

            result = DataCleaningTools(workspace_dir=td).prepare_file_organization_run([source_path])

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["archive_actions"][0]["status"], "needs_review")
            self.assertEqual(result["archive_actions"][0]["project_name"], "project_manager")
            self.assertEqual(result["archive_actions"][0]["document_type"], "项目治理文档")
            self.assertIn("pm_internal_archive_pending_redesign", result["archive_actions"][0]["blockers"])
            self.assertIsNone(result["archive_actions"][0]["target_dir"])
            self.assertIsNone(result["archive_actions"][0]["target_path"])


if __name__ == "__main__":
    unittest.main()
