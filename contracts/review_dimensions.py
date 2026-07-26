"""Contracts for read-only extraction and archive review."""

REVIEW_DIMENSIONS = [
    "ocr_quality",
    "field_completeness",
    "field_accuracy",
    "document_classification",
    "archive_plan",
    "cross_doc_consistency",
]

OCR_CONFIDENCE_THRESHOLD = 0.60

DOCUMENT_REQUIRED_FIELDS = {
    "发票": [],
    "invoice": [],
    "招标公告": ["project_name", "customer", "deadline"],
    "采购公告": ["project_name", "customer", "deadline"],
    "招标文件": ["project_name", "customer", "deadline"],
    "投标文件": ["project_name", "customer", "deadline"],
    "合同": ["project_name", "customer", "contract_status"],
    "未分类": ["project_name"],
}

