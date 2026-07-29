import importlib
import os
import shutil
import tempfile
from typing import Callable, Dict, Iterable, Optional, Sequence

from ocr.result_schema import normalize_ocr_result


DependencyProbe = Callable[[str], bool]
_READER_CACHE: Dict[tuple, object] = {}


def _default_dependency_probe(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


class EasyOcrProvider:
    name = "easyocr"

    """Lazy EasyOCR adapter.

    EasyOCR is intentionally optional: this provider reports structured blocked
    results when the module or model is unavailable instead of making OCR a
    hard project dependency.
    """

    def __init__(
        self,
        dependency_probe: DependencyProbe = _default_dependency_probe,
        languages: Sequence[str] = ("ch_sim", "en"),
        model_dir: Optional[str] = None,
        gpu: bool = False,
        min_confidence: float = 0.55,
    ) -> None:
        self.dependency_probe = dependency_probe
        self.languages = tuple(languages)
        self.model_dir = model_dir
        self.gpu = gpu
        self.min_confidence = min_confidence

    def extract(self, file_path: str) -> Dict:
        if not self.dependency_probe("easyocr"):
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "easyocr",
                "blocked_reason": "ocr_provider_unavailable",
                "error": "easyocr is not installed",
            })

        try:
            easyocr = importlib.import_module("easyocr")
            reader = self._get_reader(easyocr)
        except Exception as exc:
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "easyocr",
                "blocked_reason": _classify_easyocr_startup_error(str(exc)),
                "error": str(exc),
            })

        if os.path.splitext(file_path)[1].lower() == ".pdf":
            return self._extract_pdf(file_path, reader)

        return self._extract_image(file_path, reader)

    def _get_reader(self, easyocr_module: object) -> object:
        cache_key = (id(easyocr_module), id(getattr(easyocr_module, "Reader")), self.languages, self.gpu, self.model_dir or "")
        if cache_key not in _READER_CACHE:
            reader_kwargs = {
                "gpu": self.gpu,
                "verbose": False,
                "download_enabled": False,
            }
            if self.model_dir:
                reader_kwargs["model_storage_directory"] = self.model_dir
            _READER_CACHE[cache_key] = easyocr_module.Reader(list(self.languages), **reader_kwargs)
        return _READER_CACHE[cache_key]

    def _extract_image(self, file_path: str, reader: object) -> Dict:
        temp_path = None
        try:
            suffix = os.path.splitext(file_path)[1] or ".png"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_path = temp_file.name
            shutil.copyfile(file_path, temp_path)
            raw_lines = reader.readtext(temp_path, detail=1, paragraph=False)
        except Exception as exc:
            return normalize_ocr_result({
                "status": "failed",
                "engine": "easyocr",
                "error": str(exc),
            })
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        text, confidence = _parse_easyocr_lines(raw_lines)
        if not text.strip():
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "easyocr",
                "blocked_reason": "ocr_empty_text",
                "error": "easyocr returned empty text",
            })

        return normalize_ocr_result({
            "status": "success",
            "engine": "easyocr",
            "text": text,
            "pages": [{"page": 1, "text": text, "confidence": confidence}],
        })

    def _extract_pdf(self, file_path: str, reader: object) -> Dict:
        if not self.dependency_probe("fitz"):
            return normalize_ocr_result({
                "status": "blocked",
                "engine": "easyocr",
                "blocked_reason": "pdf_renderer_unavailable",
                "error": "PyMuPDF/fitz is required to render PDF pages before OCR",
            })

        try:
            fitz = importlib.import_module("fitz")
            doc = fitz.open(file_path)
        except Exception as exc:
            return normalize_ocr_result({
                "status": "failed",
                "engine": "easyocr",
                "error": str(exc),
            })

        pages = []
        try:
            for index, page in enumerate(doc, start=1):
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                    temp_path = temp_file.name
                try:
                    pix = page.get_pixmap(dpi=200)
                    pix.save(temp_path)
                    page_result = self._extract_image(temp_path, reader)
                finally:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

                if page_result.get("status") == "success":
                    page_text = page_result.get("text", "")
                    confidence = page_result.get("quality", {}).get("mean_confidence")
                    pages.append({"page": index, "text": page_text, "confidence": confidence})
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
                "engine": "easyocr",
                "blocked_reason": "ocr_empty_text",
                "error": "easyocr returned empty text for rendered PDF pages",
            })
        return normalize_ocr_result({
            "status": "success",
            "engine": "easyocr",
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


def _parse_easyocr_lines(raw_lines: Iterable) -> tuple[str, Optional[float]]:
    texts = []
    confidences = []
    for item in raw_lines or []:
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
    mean_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    return "\n".join(texts), mean_confidence


def _classify_easyocr_startup_error(error: str) -> str:
    lowered = error.lower()
    if "model" in lowered or "download" in lowered or "network" in lowered:
        return "ocr_model_unavailable"
    return "ocr_provider_unavailable"
