"""PDF document loader.

Uses **pdfplumber** as the primary extraction engine (best text layout
preservation for legal documents). Falls back to **PyMuPDF (fitz)** for
encrypted, damaged, or OCR-oriented PDFs.

Strategy:
1. Try pdfplumber — fast, good for text-based PDFs.
2. If pdfplumber fails (encrypted, image-only, or structural error) ->
   try PyMuPDF which handles a broader range of PDFs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.document_loader.base import BaseLoader
from app.document_loader.models import FileMetadata, PageResult

logger = logging.getLogger(__name__)

__all__ = [
    "PDFLoader",
]


class PDFLoader(BaseLoader):
    """Load and extract text from PDF files on a per-page basis."""

    FILE_TYPE = "pdf"

    def __init__(self, file_path: str | Path, max_page_chars: int | None = None) -> None:
        super().__init__(file_path)
        self._max_page_chars = max_page_chars

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_pages(self) -> list[PageResult]:
        # Strategy 1: pdfplumber (preferred)
        pages = self._try_pdfplumber()
        if pages is not None:
            return pages

        # Strategy 2: PyMuPDF fallback
        pages = self._try_pymupdf()
        if pages is not None:
            return pages

        # Strategy 3: absolute fallback -- report extraction failure as a single page
        logger.error("All PDF backends failed for %s", self._path)
        return [
            PageResult(
                page=1,
                text=f"[ERROR: Could not extract text from {self._path.name}. "
                f"The file may be encrypted, damaged, or image-only without OCR.]",
            ),
        ]

    def _try_pdfplumber(self) -> list[PageResult] | None:
        """Extract pages using pdfplumber."""
        try:
            import pdfplumber
        except ImportError:
            logger.debug("pdfplumber not available -- skipping")
            return None

        try:
            pages: list[PageResult] = []
            with pdfplumber.open(str(self._path)) as pdf:
                for i, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    text = self._clean_text(text)
                    text = self._truncate_page(text, self._max_page_chars)
                    pages.append(PageResult(page=i, text=text))
            if pages:
                return pages
            logger.debug("pdfplumber produced zero pages for %s -- trying PyMuPDF", self._path)
            return None
        except Exception as exc:
            logger.warning("pdfplumber failed for %s: %s -- trying PyMuPDF", self._path, exc)
            return None

    def _try_pymupdf(self) -> list[PageResult] | None:
        """Extract pages using PyMuPDF (fitz)."""
        try:
            import fitz
        except ImportError:
            logger.debug("PyMuPDF (fitz) not available -- skipping")
            return None

        try:
            pages: list[PageResult] = []
            with fitz.open(str(self._path)) as doc:
                for i in range(len(doc)):
                    page = doc[i]
                    text = page.get_text() or ""
                    text = self._clean_text(text)
                    text = self._truncate_page(text, self._max_page_chars)
                    pages.append(PageResult(page=i + 1, text=text))
            if pages:
                return pages
            logger.debug("PyMuPDF produced zero pages for %s", self._path)
            return None
        except Exception as exc:
            logger.error("PyMuPDF failed for %s: %s", self._path, exc)
            return None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def _extract_metadata(self) -> FileMetadata:
        stat = self._path.stat()
        meta_kwargs: dict[str, Any] = {
            "file_size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_ctime) if hasattr(stat, "st_ctime") else None,
            "modified_at": datetime.fromtimestamp(stat.st_mtime) if hasattr(stat, "st_mtime") else None,
        }
        # Try to get PDF page count
        page_count = self._try_pdf_page_count()
        if page_count is not None:
            meta_kwargs["page_count"] = page_count
        return FileMetadata(**meta_kwargs)

    def _try_pdf_page_count(self) -> int | None:
        """Extract the page count from the PDF without full text extraction."""
        # Try pdfplumber first (faster for page count)
        try:
            import pdfplumber

            with pdfplumber.open(str(self._path)) as pdf:
                return len(pdf.pages)
        except Exception:
            pass
        # Fall back to PyMuPDF
        try:
            import fitz

            with fitz.open(str(self._path)) as doc:
                return len(doc)
        except Exception:
            pass
        return None
