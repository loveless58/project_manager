import importlib.util
import os
import re
import shutil
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

from .providers.easyocr_provider import EasyOcrProvider
from .result_schema import normalize_ocr_result


DependencyProbe = Callable[[str], bool]
BinaryProbe = Callable[[str], Optional[str]]


def default_dependency_probe(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def default_binary_probe(binary_name: str) -> Optional[str]:
    return shutil.which(binary_name)


def extract_pdf_or_image(
    file_path: str,
    ocr_adapter: Optional[Callable[[str], Any]] = None,
    dependency_probe: DependencyProbe = default_dependency_probe,
    binary_probe: BinaryProbe = default_binary_probe,
) -> Dict:
    """Extract PDF/image content through stable project-level OCR providers."""
    ext = os.path.splitext(file_path)[1].lower()
    if _is_disabled_ocr_adapter(ocr_adapter):
        if ext == ".pdf":
            extracted = _extract_pdf_text_with_pymupdf(file_path)
            if extracted.get("status") == "success":
                return _document_success(
                    file_path,
                    ext,
                    extracted.get("text", ""),
                    "pdf_text",
                    is_scanned=extracted.get("is_scanned", False),
                    engine_candidates=[],
                )
        ocr = _run_custom_ocr_adapter(file_path, ocr_adapter)
        if ocr.get("status") == "success":
            text = ocr.get("text", "") or ""
            return _document_success(file_path, ext, text, "ocr", ocr=ocr, engine_candidates=[])
        return _blocked_document(
            file_path,
            ext,
            [],
            error=ocr.get("error", "OCR adapter failed"),
            ocr=ocr,
            blocked_reason=ocr.get("blocked_reason"),
        )

    candidates = _engine_candidates(file_path, dependency_probe, binary_probe)

    if ext == ".ofd":
        sidecar = _find_ocr_sidecar(file_path, extra_extensions=(".xml", ".txt", ".md"))
        if sidecar:
            text = _read_sidecar_text(sidecar)
            if text:
                return _document_success(file_path, ext, text, "ofd_sidecar", is_scanned=False, engine_candidates=candidates)
        return _blocked_document(
            file_path,
            ext,
            candidates,
            error="OFD converter is unavailable and no readable sidecar was found",
            blocked_reason="ofd_converter_unavailable",
            next_action="provide_ofd_sidecar_or_configure_ofd_converter",
        )

    sidecar = _find_ocr_sidecar(file_path)
    if sidecar:
        text = _read_text_file(sidecar)
        if text:
            ocr = normalize_ocr_result({
                "status": "success",
                "engine": "sidecar_text",
                "text": text,
                "pages": [{"page": 1, "text": text, "confidence": 1.0, "source_ref": sidecar}],
            })
            return _document_success(file_path, ext, text, "ocr", ocr=ocr, engine_candidates=candidates)

    if ext == ".pdf" and dependency_probe("fitz"):
        extracted = _extract_pdf_text_with_pymupdf(file_path)
        if extracted.get("status") == "success":
            return _document_success(
                file_path,
                ext,
                extracted.get("text", ""),
                "pdf_text",
                is_scanned=extracted.get("is_scanned", False),
                engine_candidates=candidates,
            )


    if ocr_adapter is not None:
        ocr = _run_custom_ocr_adapter(file_path, ocr_adapter)
        if ocr.get("status") == "success":
            text = ocr.get("text", "") or ""
            return _document_success(file_path, ext, text, "ocr", ocr=ocr, engine_candidates=candidates)
        return _blocked_document(
            file_path,
            ext,
            candidates,
            error=ocr.get("error", "OCR adapter failed"),
            ocr=ocr,
            blocked_reason=ocr.get("blocked_reason"),
        )
    if ext in {".png", ".jpg", ".jpeg"} and dependency_probe("easyocr"):
        ocr = EasyOcrProvider(dependency_probe=dependency_probe).extract(file_path)
        if ocr.get("status") == "success":
            text = ocr.get("text", "") or ""
            return _document_success(file_path, ext, text, "ocr", ocr=ocr, engine_candidates=candidates)
        return _blocked_document(
            file_path,
            ext,
            candidates,
            error=ocr.get("error", "EasyOCR provider failed"),
            ocr=ocr,
        )

    return _blocked_document(file_path, ext, candidates, error="No OCR/PDF engine provider available for this file")

def _is_disabled_ocr_adapter(ocr_adapter: Optional[Callable[[str], Any]]) -> bool:
    """Identify the explicit disabled capability without fragile name matching."""
    provider = getattr(ocr_adapter, "__self__", None)
    return bool(getattr(provider, "disables_ocr_capability", False))

def describe_ocr_capabilities(
    file_path: str = "",
    dependency_probe: DependencyProbe = default_dependency_probe,
    binary_probe: BinaryProbe = default_binary_probe,
) -> Dict:
    candidates = _engine_candidates(file_path, dependency_probe, binary_probe)
    return {
        "schema_version": "ocr.capabilities.v1",
        "status": "ready" if any(item["available"] for item in candidates) else "blocked",
        "engine_candidates": candidates,
    }


def _engine_candidates(
    file_path: str,
    dependency_probe: DependencyProbe,
    binary_probe: BinaryProbe,
) -> List[Dict]:
    sidecar = _find_ocr_sidecar(file_path) if file_path else None
    return [
        {
            "engine": "sidecar_text",
            "available": bool(sidecar),
            "reason": "ready" if sidecar else "sidecar_missing",
            "path": sidecar or "",
        },
        {
            "engine": "pymupdf_text",
            "available": dependency_probe("fitz"),
            "reason": "ready" if dependency_probe("fitz") else "module_not_installed",
            "module": "fitz",
        },
        {
            "engine": "tesseract",
            "available": bool(binary_probe("tesseract")),
            "reason": "ready" if binary_probe("tesseract") else "binary_not_found",
            "binary": "tesseract",
        },
        {
            "engine": "easyocr",
            "available": dependency_probe("easyocr"),
            "reason": "ready" if dependency_probe("easyocr") else "module_not_installed",
            "module": "easyocr",
        },
    ]


def _extract_pdf_text_with_pymupdf(file_path: str) -> Dict:
    try:
        import fitz
    except ImportError:
        return {"status": "blocked", "blocked_reason": "pdf_parser_unavailable", "error": "PyMuPDF not installed"}

    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        if len(text.strip()) < 100:
            return {
                "status": "blocked",
                "blocked_reason": "ocr_adapter_unavailable",
                "error": "PDF appears scanned and no OCR provider is available",
                "text": text,
                "is_scanned": True,
            }
        return {"status": "success", "text": text, "is_scanned": False}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def _run_custom_ocr_adapter(file_path: str, ocr_adapter: Callable[[str], Any]) -> Dict:
    try:
        result = ocr_adapter(file_path)
    except Exception as exc:
        return normalize_ocr_result({
            "status": "failed",
            "engine": "custom_adapter",
            "error": str(exc),
        })
    if isinstance(result, str):
        result = {"text": result}
    if not isinstance(result, dict):
        result = {"status": "failed", "engine": "custom_adapter", "error": "OCR adapter returned invalid result"}
    if result.get("status") == "blocked":
        return normalize_ocr_result({
            "status": "blocked", "engine": result.get("engine", "custom_adapter"),
            "blocked_reason": result.get("blocked_reason", "OCR.CAPABILITY_DISABLED"),
            "error": result.get("error", "OCR capability is disabled"),
        })

    if result.get("status") == "failed":
        return normalize_ocr_result({
            "status": "failed",
            "engine": result.get("engine", "custom_adapter"),
            "error": result.get("error", "OCR failed"),
        })
    if not result.get("text"):
        return normalize_ocr_result({
            "status": "failed",
            "engine": result.get("engine", "custom_adapter"),
            "error": "OCR adapter returned empty text",
        })
    return normalize_ocr_result({
        "status": "success",
        "engine": result.get("engine", "custom_adapter"),
        "text": result.get("text", ""),
        "pages": result.get("pages", []),
    })


def _document_success(
    file_path: str,
    ext: str,
    text: str,
    extract_method: str,
    ocr: Optional[Dict] = None,
    is_scanned: bool = True,
    engine_candidates: Optional[List[Dict]] = None,
) -> Dict:
    result = {
        "schema_version": "document.extract.v1",
        "status": "success",
        "file": file_path,
        "filename": os.path.basename(file_path),
        "file_type": ext,
        "extract_method": extract_method,
        "text_length": len(text),
        "extracted_text": text[:4000] + ("..." if len(text) > 4000 else ""),
        "is_scanned": is_scanned,
        "fields": {},
        "engine_candidates": engine_candidates or [],
    }
    if ocr:
        result["ocr"] = ocr
        result["needs_human_review"] = bool(ocr.get("quality", {}).get("needs_human_review"))
    return result


def _blocked_document(
    file_path: str,
    ext: str,
    candidates: List[Dict],
    error: str = "No OCR/PDF provider available for this file",
    ocr: Optional[Dict] = None,
    blocked_reason: Optional[str] = None,
    next_action: str = "install_pymupdf_or_provide_ocr_sidecar",
) -> Dict:
    if ocr is None:
        ocr = normalize_ocr_result({
            "status": "blocked",
            "engine": "unavailable",
            "blocked_reason": blocked_reason or "ocr_adapter_unavailable",
            "error": error,
        })
    if blocked_reason is None:
        blocked_reason = "ocr_engine_failed" if ext in {".png", ".jpg", ".jpeg"} else "ocr_adapter_unavailable"
    return {
        "schema_version": "document.extract.v1",
        "status": "blocked",
        "blocked_reason": blocked_reason,
        "error": error,
        "file": file_path,
        "filename": os.path.basename(file_path),
        "file_type": ext,
        "extract_method": "ocr",
        "text_length": 0,
        "extracted_text": "",
        "is_scanned": True,
        "fields": {},
        "ocr": ocr,
        "needs_human_review": True,
        "engine_candidates": candidates,
        "next_action": next_action,
    }


def _find_ocr_sidecar(file_path: str, extra_extensions: tuple[str, ...] = ()) -> Optional[str]:
    base, _ = os.path.splitext(file_path)
    candidates = [f"{file_path}.ocr.txt", f"{base}.ocr.txt"]
    candidates.extend(f"{base}{extension}" for extension in extra_extensions)
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def _read_sidecar_text(file_path: str) -> str:
    if os.path.splitext(file_path)[1].lower() == ".xml":
        return _read_xml_text(file_path)
    return _read_text_file(file_path)


def _read_xml_text(file_path: str) -> str:
    raw = _read_text_file(file_path)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return re.sub(r"<[^>]+>", " ", raw)
    values = []
    for node in root.iter():
        if node.text and node.text.strip():
            values.append(node.text.strip())
        if node.tail and node.tail.strip():
            values.append(node.tail.strip())
    return "\n".join(values)


def _read_text_file(file_path: str) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()
