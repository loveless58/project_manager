from .result_schema import normalize_ocr_result
from .provider_registry import describe_ocr_capabilities, extract_pdf_or_image

__all__ = ["describe_ocr_capabilities", "extract_pdf_or_image", "normalize_ocr_result"]
