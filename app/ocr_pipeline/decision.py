"""OCR Decision Engine — determines whether a PDF page has selectable text
or requires OCR.

Strategy:
1. Render page to image for OCR pipeline (always needed for fallback).
2. Use PyMuPDF to extract text directly — if sufficient content exists,
   skip OCR.
3. Decision criteria: character count > threshold AND average confidence
   (from font info) is acceptable.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.ocr_pipeline.models import OCRResult

logger = logging.getLogger(__name__)

# Minimum number of characters to consider a page "has text"
_MIN_TEXT_CHARS = 20

# Minimum ratio of extractable characters to page area (chars per sq inch)
# to consider text extraction successful
_MIN_CHAR_DENSITY = 0.5


class OCRDecisionEngine:
    """Decides whether a PDF page needs OCR based on its selectable text content.

    Usage::

        decision = OCRDecisionEngine()
        needs_ocr, text = decision.evaluate(pdf_path, page_number=1)
        if needs_ocr:
            # run OCR pipeline
            ...
        else:
            # use extracted text directly
            ...
    """

    @staticmethod
    def evaluate(pdf_path: str | Path, page_number: int) -> tuple[bool, str, OCRResult]:
        """Evaluate whether a PDF page has extractable text.

        Args:
            pdf_path: Path to the PDF file.
            page_number: 1-based page number.

        Returns:
            A tuple of ``(needs_ocr, extracted_text_or_empty, partial_result)``.
            If ``needs_ocr`` is False, ``extracted_text`` contains the page text.
            ``partial_result`` contains whatever metadata was gathered.

        """
        try:
            import fitz
        except ImportError:
            logger.error("PyMuPDF (fitz) not available — cannot check for selectable text")
            return True, "", OCRResult(page=page_number, ocr_used=True, error="PyMuPDF not available")

        try:
            doc = fitz.open(str(pdf_path))
            if page_number < 1 or page_number > len(doc):
                doc.close()
                return True, "", OCRResult(page=page_number, ocr_used=True, error=f"Page {page_number} out of range")

            page = doc[page_number - 1]

            # ---- Method 1: Full text extraction ----
            raw_text = page.get_text("text") or ""

            # ---- Method 2: Check for text blocks (more reliable) ----
            text_blocks = page.get_text("blocks")
            has_text_blocks = (
                any(block[6] == 0 for block in text_blocks) if text_blocks else False  # block[6] == 0 means text block
            )

            # ---- Method 3: Check for characters directly ----
            text_dict = page.get_text("dict")
            char_count = 0
            for block in text_dict.get("blocks", []):
                if block.get("type") == 0:  # text block
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            char_count += len(span.get("text", ""))

            # ---- Decide ----
            text = raw_text.strip()
            # Clean the text for direct use
            if text:
                text = OCRDecisionEngine._clean_direct_text(text)

            # Decision: has enough characters AND has text blocks detected
            if char_count >= _MIN_TEXT_CHARS and has_text_blocks:
                # Page has sufficient selectable text — use directly
                result = OCRResult(
                    page=page_number,
                    ocr_used=False,
                    confidence=1.0,
                    language="english",  # Will be refined later if needed
                    text=text,
                    ocr_engine=None,
                )
                doc.close()
                return False, text, result

            # Also check: if char_count is very high, treat as text page
            # even if text_blocks detection is fuzzy
            if char_count >= _MIN_TEXT_CHARS * 5:
                result = OCRResult(
                    page=page_number,
                    ocr_used=False,
                    confidence=1.0,
                    language="english",
                    text=text,
                    ocr_engine=None,
                )
                doc.close()
                return False, text, result

            # Page needs OCR
            doc.close()
            return (
                True,
                "",
                OCRResult(
                    page=page_number,
                    ocr_used=True,
                    confidence=0.0,
                    text="",
                ),
            )

        except Exception as exc:
            logger.error("Failed to evaluate page %d of %s: %s", page_number, pdf_path, exc)
            return (
                True,
                "",
                OCRResult(
                    page=page_number,
                    ocr_used=True,
                    error=f"Decision engine failed: {exc}",
                ),
            )

    @staticmethod
    def _clean_direct_text(raw: str) -> str:
        """Clean text that was extracted directly (non-OCR path)."""
        import re
        import unicodedata

        # Normalize Unicode
        text = unicodedata.normalize("NFKC", raw)
        # Replace non-breaking spaces
        text = text.replace("\u00a0", " ")
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
