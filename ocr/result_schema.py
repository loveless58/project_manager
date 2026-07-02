from typing import Any, Dict, List


def normalize_ocr_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize any OCR adapter output to ocr.result.v1."""
    status = result.get("status", "success" if result.get("text") else "blocked")
    pages = _normalize_pages(result.get("pages", []), result.get("text", ""))
    text = result.get("text", "") or "\n".join(page.get("text", "") for page in pages).strip()
    normalized = {
        "schema_version": "ocr.result.v1",
        "status": status,
        "engine": result.get("engine", "custom_adapter"),
        "text": text,
        "pages": pages,
        "quality": evaluate_ocr_quality(status, text, pages),
    }
    for key in ("blocked_reason", "error"):
        if result.get(key):
            normalized[key] = result[key]
    return normalized


def evaluate_ocr_quality(status: str, text: str, pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    reasons: List[str] = []
    if status != "success":
        reasons.append("ocr_not_successful")
    if not text.strip():
        reasons.append("empty_text")

    confidences = [
        float(page.get("confidence"))
        for page in pages
        if isinstance(page.get("confidence"), (int, float))
    ]
    mean_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    low_confidence_pages = [
        page.get("page", index + 1)
        for index, page in enumerate(pages)
        if isinstance(page.get("confidence"), (int, float)) and float(page.get("confidence")) < 0.7
    ]
    if mean_confidence is not None and mean_confidence < 0.7:
        reasons.append("low_mean_confidence")
    if low_confidence_pages:
        reasons.append("low_confidence_pages")

    if status != "success" or reasons:
        quality = "poor" if status != "success" or "empty_text" in reasons or "low_mean_confidence" in reasons else "partial"
    else:
        quality = "good"

    return {
        "quality": quality,
        "mean_confidence": mean_confidence,
        "low_confidence_pages": low_confidence_pages,
        "needs_human_review": quality != "good",
        "reasons": reasons,
    }


def _normalize_pages(pages: Any, fallback_text: str) -> List[Dict[str, Any]]:
    if isinstance(pages, list) and pages:
        normalized = []
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            normalized.append({
                "page": page.get("page", index + 1),
                "text": page.get("text", ""),
                "confidence": page.get("confidence"),
                "source_ref": page.get("source_ref", ""),
            })
        return normalized
    if fallback_text:
        return [{"page": 1, "text": fallback_text, "confidence": None, "source_ref": ""}]
    return []
