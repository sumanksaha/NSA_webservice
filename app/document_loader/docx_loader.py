"""Microsoft Word (.docx) document loader.

Uses **python-docx** to extract paragraphs. Since DOCX files don't have
inherent page boundaries, we approximate them by:
1. Using section breaks and page breaks embedded in the XML.
2. Grouping paragraphs into pages based on explicit page breaks.
3. Falling back to a single "page" if no breaks are found.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.document_loader.base import BaseLoader
from app.document_loader.models import FileMetadata, PageResult

logger = logging.getLogger(__name__)

__all__ = [
    "DOCXLoader",
]


# Regex patterns for detecting page/section breaks in run-level XML
_PAGE_BREAK_PATTERN = re.compile(r"<w:br\s+[^>]*w:type=\"page\"[^>]*/>", re.IGNORECASE)
_SECTION_BREAK_PATTERN = re.compile(r"<w:sectPr[^>]*/>|<w:lastRenderedPageBreak\s*/?>", re.IGNORECASE)


class DOCXLoader(BaseLoader):
    """Load and extract text from .docx files, approximating page boundaries."""

    FILE_TYPE = "docx"

    def __init__(self, file_path: str | Path, max_page_chars: int | None = None) -> None:
        super().__init__(file_path)
        self._max_page_chars = max_page_chars

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_pages(self) -> list[PageResult]:
        try:
            from docx import Document
        except ImportError:
            logger.error("python-docx is not installed — cannot load .docx files")
            return [
                PageResult(
                    page=1,
                    text="[ERROR: python-docx library is not available. Install with: pip install python-docx]",
                ),
            ]

        try:
            doc = Document(str(self._path))
            return self._build_pages(doc)
        except Exception as exc:
            logger.error("Failed to load .docx file %s: %s", self._path, exc)
            return [
                PageResult(
                    page=1,
                    text=f"[ERROR: Could not read {self._path.name}. The file may be corrupted. Details: {exc}]",
                ),
            ]

    def _build_pages(self, doc) -> list[PageResult]:
        """Group paragraphs into pages by detecting page/section breaks."""
        page_texts: list[list[str]] = [[]]  # list of paragraphs per page
        current_page = 0

        for para in doc.paragraphs:
            raw = para.text
            # Check if the paragraph's XML contains a page break
            if para._element is not None:
                xml = para._element.xml
                if _PAGE_BREAK_PATTERN.search(xml) or _SECTION_BREAK_PATTERN.search(xml):
                    # Start a new page
                    page_texts.append([])
                    current_page += 1
                    # If the break is inline, split the text content at the break
                    # (text after the break goes to the new page)
                    page_texts[current_page].append(raw)
                    continue

            if raw.strip():
                page_texts[current_page].append(raw)

        # Build PageResult list
        pages: list[PageResult] = []
        for i, paragraphs in enumerate(page_texts, start=1):
            text = "\n".join(paragraphs)
            text = self._clean_text(text)
            text = self._truncate_page(text, self._max_page_chars)
            pages.append(PageResult(page=i, text=text))

        # If no explicit page breaks were found, collapse into a single page
        if len(pages) == 1 and not pages[0].text.strip():
            # Truly empty document
            pass

        return pages

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

        # Try to get page count from the actual extraction
        try:
            pages = self._extract_pages()
            meta_kwargs["page_count"] = len(pages)
        except Exception:
            pass

        return FileMetadata(**meta_kwargs)
