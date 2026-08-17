"""Legal document OCR adapter for the RAG corpus pipeline (Agent A §3.3).

Reuses the existing ``app/ocr_pipeline`` (decision engine + preprocessing +
``OCREngine`` — EasyOCR primary since 2026-08-09) and adapts it to the RAG
ingestion path: image-only PDF pages (0 selectable chars) are OCR'd so they
produce chunks instead of being dropped as empty documents.

Design notes:
- **Graceful degradation** — if ``easyocr``/``torch``/``cv2`` are missing the
  adapter reports ``available() == False`` and returns text unchanged (the
  corpus evaluation §2.4 flagged 2 image-only scans; OCR is now the fix).
- **Per-page decision** — the underlying ``OCRPipeline.process_document``
  already decides per page (selectable text → direct, else OCR), so only
  genuinely scanned pages pay the OCR cost.
- **Injectable** — the ``OCRPipeline`` is constructor-injectable (mock
  injection pattern) so tests never need the real model stack.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: A loaded document whose total text is below this many chars is treated as
#: image-only / scan and routed through OCR (when available).
MIN_TEXT_CHARS_FOR_OCR = 20


class LegalDocumentOCR:
    """OCR adapter: fills text gaps in scanned legal PDFs for ingestion.

    Args:
        pipeline: Optional pre-built ``OCRPipeline`` (injected for tests;
            the real one is built lazily from ``app.ocr_pipeline``).
        min_text_chars: Below this total char count a loaded PDF is routed
            through OCR. Defaults to :data:`MIN_TEXT_CHARS_FOR_OCR`.
    """

    def __init__(self, pipeline: Any | None = None, min_text_chars: int = MIN_TEXT_CHARS_FOR_OCR) -> None:
        self._pipeline = pipeline
        self._min_text_chars = min_text_chars

    # ------------------------------------------------------------------ #
    # Lazy accessors
    # ------------------------------------------------------------------ #

    def _get_pipeline(self) -> Any | None:
        """Return the (cached) ``OCRPipeline`` or ``None`` when unavailable."""
        if self._pipeline is None:
            try:
                from app.ocr_pipeline.pipeline import OCRPipeline

                self._pipeline = OCRPipeline(use_gpu=False)
            except Exception as exc:
                logger.warning("LegalDocumentOCR: OCR pipeline unavailable (%s)", exc)
                self._pipeline = False
        return self._pipeline or None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def available(self) -> bool:
        """Whether an OCR pipeline can actually be constructed."""
        return self._get_pipeline() is not None

    def extract_document_text(self, pdf_path: str | Path) -> str:
        """OCR a PDF document, returning the concatenated page text.

        Selectable-text pages are returned as-is (no OCR cost); scanned pages
        are OCR'd. Returns ``""`` on any failure so ingestion can continue
        with the empty-document guard rather than raising.
        """
        pipeline = self._get_pipeline()
        if pipeline is None:
            return ""
        try:
            results = pipeline.process_document(str(pdf_path))
        except Exception as exc:
            logger.warning("LegalDocumentOCR.extract_document_text failed: %s", exc)
            return ""
        if not results:
            return ""
        return "\n\n".join(getattr(r, "text", "") or "" for r in results if getattr(r, "text", ""))

    def should_ocr(self, loaded_text: str) -> bool:
        """True when a loaded document's text is below the OCR threshold."""
        return not (loaded_text or "").strip() or len((loaded_text or "").strip()) < self._min_text_chars

    def fill_scanned_pdf(self, pdf_path: str | Path, loaded_text: str) -> tuple[str, bool]:
        """Return ``(text, ocr_applied)`` for a possibly-scanned PDF.

        Args:
            pdf_path: Path to the PDF.
            loaded_text: Text extracted by the document loader (may be empty).

        Returns:
            ``(text, ocr_applied)`` — when the loaded text is already
            sufficient, ``(loaded_text, False)`` unchanged.
        """
        if not self.should_ocr(loaded_text):
            return loaded_text, False
        if not self.available():
            logger.info(
                "LegalDocumentOCR: %s looks scanned but OCR is unavailable — leaving text as-is",
                Path(pdf_path).name,
            )
            return loaded_text, False
        ocr_text = self.extract_document_text(pdf_path)
        if not ocr_text or not ocr_text.strip():
            logger.warning("LegalDocumentOCR: OCR produced no text for %s", Path(pdf_path).name)
            return loaded_text, False
        logger.info(
            "LegalDocumentOCR: OCR applied to %s (%d chars)", Path(pdf_path).name, len(ocr_text)
        )
        return ocr_text, True


# End of legal_ocr.py
