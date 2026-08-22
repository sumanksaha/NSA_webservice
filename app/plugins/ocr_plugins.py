"""Plugin implementations for OCR providers.

Wraps the existing ``app.ocr_pipeline.OCRPipeline`` (EasyOCR primary,
PaddleOCR + Tesseract fallbacks) behind the ``OCRProvider`` interface.

Uses lazy imports — ``OCRPipeline`` is only imported when
:meth:`extract_text` is called, so ``import app.plugins.ocr_plugins`` works
even when EasyOCR / torch are not installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.plugins.base import OCRProvider, OCRResult

logger = logging.getLogger(__name__)


class EasyOCRPlugin(OCRProvider):
    """OCR provider wrapping ``app.ocr_pipeline.OCRPipeline``.

    The underlying ``OCRPipeline`` uses EasyOCR first, with PaddleOCR and
    Tesseract as fallbacks (see ``app/ocr_pipeline/ocr_engine.py``).
    """

    def extract_text(self, file_path: str | Path) -> OCRResult:
        """Extract text from a document via the OCR pipeline.

        Lazy-imports ``OCRPipeline`` so the plugin module is import-safe
        even without EasyOCR/torch installed.
        """
        from app.ocr_pipeline.pipeline import OCRPipeline  # lazy

        pipeline = OCRPipeline(languages=["english", "hindi"])
        results = pipeline.process_document(self._safe_path(file_path))

        if not results:
            return OCRResult(text="", confidence=0.0, page_count=0)

        texts = []
        confidences: list[float] = []
        page_results: list[dict] = []

        for page_result in results:
            texts.append(page_result.text or "")
            confidences.append(page_result.confidence or 0.0)
            page_results.append({
                "page": page_result.page,
                "text": page_result.text or "",
                "confidence": page_result.confidence or 0.0,
                "ocr_used": getattr(page_result, "ocr_used", False),
            })

        combined_text = "\n\n".join(texts)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        engine = getattr(results[0], "ocr_engine_used", None) or "easyocr"

        return OCRResult(
            text=combined_text,
            confidence=avg_confidence,
            ocr_engine_used=engine,
            page_count=len(results),
            page_results=page_results,
        )


__all__ = ["EasyOCRPlugin"]
