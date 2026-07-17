"""RapidOCR (ONNX-based PaddleOCR) provider for project-level OCR.

Mirrors the EasyOcrProvider contract:
- Returns ocr.result.v1 via normalize_ocr_result.
- Treats rapidocr_onnxruntime as an optional dependency.
- Renders PDF pages with PyMuPDF before OCR.
"""

import importlib
import os
import tempfile
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


from ocr.result_schema import normalize_ocr_result


DependencyProbe = Callable[[str], bool]
_ENGINE_CACHE: Dict[Tuple, object] = {}


def _default_dependency_probe(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None  # type: ignore[attr-defined]


class RapidOcrProvider:
    """Lazy RapidOCR adapter (ONNX runtime, PaddleOCR-derived).

    RapidOCR is intentionally optional: this provider reports structured
    blocked results when the module or model is unavailable instead of
    making OCR a hard project dependency.
    """

    def __init__(
        self,
        dependency_probe: DependencyProbe = _default_dependency_probe,
        languages: Sequence[str] = ("ch", "en"),
        min_confidence: float = 0.55,
    ) -> None:
        self.dependency_probe = dependency_probe
        self.languages = tuple(languages)
        self.min_confidence = min_confidence

    def extract(self, file_path: str) -> Dict[str, Any]:
        if not self.dependency_probe("rapidocr_onnxruntime"):
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "rapidocr",
                "blocked_reason": "ocr_provider_unavailable",
                "error": "rapidocr_onnxruntime is not installed",
            })

        try:
            rapidocr_module = importlib.import_module("rapidocr_onnxruntime")
            engine = self._get_engine(rapidocr_module)
        except Exception as exc:
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "rapidocr",
                "blocked_reason": _classify_rapidocr_startup_error(str(exc)),
                "error": str(exc),
            })

        if os.path.splitext(file_path)[1].lower() == ".pdf":
            return self._extract_pdf(file_path, engine)

        return self._extract_image(file_path, engine)

    def _get_engine(self, rapidocr_module: object) -> object:
        cache_key = (
            id(rapidocr_module),
            id(getattr(rapidocr_module, "RapidOCR", None)),
            self.languages,
        )
        if cache_key not in _ENGINE_CACHE:
            engine_cls = getattr(rapidocr_module, "RapidOCR", None)
            if engine_cls is None:
                raise RuntimeError("rapidocr_onnxruntime missing RapidOCR class")
            _ENGINE_CACHE[cache_key] = engine_cls()
        return _ENGINE_CACHE[cache_key]

    def _extract_image(self, file_path: str, engine: object) -> Dict[str, Any]:
        try:
            # RapidOCR engine instances are callable: engine(image_path) -> (result, elapse)
            result, _elapse = engine(file_path)  # type: ignore[operator]
        except Exception as exc:
            return normalize_ocr_result({
                "status": "failed",
                "engine": "rapidocr",
                "error": str(exc),
            })

        text, confidences = _parse_rapidocr_lines(result)
        if not text.strip():
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "rapidocr",
                "blocked_reason": "ocr_empty_text",
                "error": "rapidocr returned empty text",
            })

        return normalize_ocr_result({
            "status": "success",
            "engine": "rapidocr",
            "text": text,
            "pages": [{"page": 1, "text": text, "confidence": _mean(confidences)}],
        })

    def _extract_pdf(self, file_path: str, engine: object) -> Dict[str, Any]:
        if not self.dependency_probe("fitz"):
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "rapidocr",
                "blocked_reason": "pdf_renderer_unavailable",
                "error": "PyMuPDF/fitz is required to render PDF pages before OCR",
            })

        try:
            fitz = importlib.import_module("fitz")
            doc = fitz.open(file_path)
        except Exception as exc:
            return normalize_ocr_result({
                "status": "failed",
                "engine": "rapidocr",
                "error": str(exc),
            })

        pages: List[Dict[str, Any]] = []
        try:
            for index, page in enumerate(doc, start=1):
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                    temp_path = temp_file.name
                try:
                    pix = page.get_pixmap(dpi=200)
                    pix.save(temp_path)
                    page_result = self._extract_image(temp_path, engine)
                finally:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

                if page_result.get("status") == "success":
                    page_text = page_result.get("text", "")
                    confidence = page_result.get("quality", {}).get("mean_confidence")
                    pages.append({
                        "page": index,
                        "text": page_text,
                        "confidence": confidence,
                    })
        finally:
            close = getattr(doc, "close", None)
            if callable(close):
                close()

        text = "\n".join(page.get("text", "") for page in pages).strip()
        confidences = [
            float(page["confidence"])
            for page in pages
            if isinstance(page.get("confidence"), (int, float))
        ]
        mean_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
        if not text:
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "rapidocr",
                "blocked_reason": "ocr_empty_text",
                "error": "rapidocr returned empty text for rendered PDF pages",
            })
        return normalize_ocr_result({
            "status": "success",
            "engine": "rapidocr",
            "text": text,
            "pages": [
                {
                    "page": page["page"],
                    "text": page["text"],
                    "confidence": page.get("confidence", mean_confidence),
                }
                for page in pages
            ],
        })


def _parse_rapidocr_lines(result: Any) -> Tuple[str, List[float]]:
    """Parse RapidOCR result into (text, confidences).

    RapidOCR result format: [[box, text, confidence], ...]
    where box is [[x1,y1], [x2,y2], [x3,y3], [x4,y4]] (4 corner points).
    """
    texts: List[str] = []
    confidences: List[float] = []
    for item in result or []:
        if isinstance(item, str):
            if item.strip():
                texts.append(item.strip())
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            line_text = str(item[1]).strip()
            if line_text:
                texts.append(line_text)
            if len(item) >= 3 and isinstance(item[2], (int, float)):
                confidences.append(float(item[2]))
    return "\n".join(texts), confidences


def _mean(values: Iterable[float]) -> Optional[float]:
    values = list(values)
    return round(sum(values) / len(values), 4) if values else None


def _classify_rapidocr_startup_error(error: str) -> str:
    lowered = error.lower()
    if "model" in lowered or "download" in lowered or "network" in lowered:
        return "ocr_model_unavailable"
    return "ocr_provider_unavailable"
