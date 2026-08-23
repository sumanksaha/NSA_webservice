"""Plugin implementations for OCR providers.

Wraps the existing ``app.ocr_pipeline`` stack (``OCRPipeline`` for PDFs,
``OCREngine`` for images — EasyOCR primary, PaddleOCR + Tesseract fallbacks)
behind the ``OCRProvider`` interface. This adapter is the single owner of:

- the document-vs-image dispatch (by file extension),
- the translation from pipeline/engine results into the canonical
  ``plugins.base.OCRResult`` / ``PageText`` shapes,
- config resolution via ``cfg`` (``OCR_LANGUAGES``, ``OCR_USE_GPU``).

Uses lazy imports — the OCR stack is only imported when extraction or an
availability probe actually runs, so importing this module works even when
EasyOCR / torch are not installed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.plugins.base import OCRProvider, OCRResult, PageText
from app.shared.config import cfg

logger = logging.getLogger(__name__)

#: Suffixes routed to the image path of the implementation (everything else,
#: notably PDFs, goes through the page-deciding document pipeline).
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"})


def _configured_languages() -> list[str]:
    """Split the declared ``OCR_LANGUAGES`` value into a language list."""
    langs = [part.strip() for part in cfg.ocr_languages.split(",") if part.strip()]
    return langs or ["english"]


class EasyOCRPlugin(OCRProvider):
    """OCR provider wrapping ``app.ocr_pipeline`` (PDF pipeline + image engine)."""

    def available(self) -> bool:
        """True when the OCR backend can be constructed on this host."""
        try:
            import easyocr  # noqa: F401  (lazy probe — the primary backend)
        except Exception:
            logger.debug("EasyOCRPlugin.available: easyocr not importable")
            return False
        return True

    def extract_text(self, file_path: str | Path) -> OCRResult:
        """Extract text from a PDF document or an image file.

        The dispatch is by suffix and is invisible to callers — both paths
        return the same canonical :class:`OCRResult`.
        """
        suffix = Path(file_path).suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            return self._extract_image(Path(file_path))
        return self._extract_document(Path(file_path))

    # ------------------------------------------------------------------ #
    # Implementation: documents (PDF)
    # ------------------------------------------------------------------ #

    def _extract_document(self, path: Path) -> OCRResult:
        from app.ocr_pipeline.pipeline import OCRPipeline  # lazy

        pipeline = OCRPipeline(languages=_configured_languages(), use_gpu=cfg.ocr_use_gpu)
        results = pipeline.process_document(str(path))

        if not results:
            return OCRResult(text="", confidence=0.0, page_count=0)

        pages = [
            PageText(
                page=int(getattr(r, "page", i + 1)),
                text=getattr(r, "text", "") or "",
                confidence=float(getattr(r, "confidence", 0.0) or 0.0),
                engine=(getattr(r, "ocr_engine", "") or ""),
            )
            for i, r in enumerate(results)
        ]
        # The pipeline's page results carry ``ocr_engine`` (the engine that
        # actually ran); partial/error pages may leave it empty — fall back.
        engine = next((p.engine for p in pages if p.engine), "easyocr")
        combined = "\n\n".join(p.text for p in pages if p.text)
        avg_confidence = sum(p.confidence for p in pages) / len(pages) if pages else 0.0

        return OCRResult(
            text=combined,
            confidence=avg_confidence,
            ocr_engine_used=engine,
            page_count=len(pages),
            page_results=pages,
        )

    # ------------------------------------------------------------------ #
    # Implementation: images
    # ------------------------------------------------------------------ #

    def _extract_image(self, path: Path) -> OCRResult:
        import numpy as np
        from PIL import Image

        from app.ocr_pipeline.ocr_engine import OCREngine  # lazy

        with Image.open(path) as image:
            array = np.array(image.convert("RGB"))

        engine = OCREngine(languages=_configured_languages(), use_gpu=cfg.ocr_use_gpu)
        text, confidence, engine_name, _language = engine.recognize(array)

        page = PageText(page=1, text=text or "", confidence=float(confidence or 0.0), engine=engine_name or "")
        return OCRResult(
            text=page.text,
            confidence=page.confidence,
            ocr_engine_used=page.engine or "easyocr",
            page_count=1 if page.text else 0,
            page_results=[page],
        )


__all__ = ["EasyOCRPlugin"]
