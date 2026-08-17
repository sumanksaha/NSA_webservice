"""Page splitter for multi-sample lab-report PDFs.

Split a multi-page PDF into individual page-PDFs so each can be processed
independently by the OCR extraction pipeline (plan.md Phase A).

Uses PyMuPDF (``fitz``) — already a project dependency — for reliable
page-level PDF splitting.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PAGE_PREFIX = "page_"


def split_pdf_bundle(pdf_path: str | Path) -> list[Path]:
    """Split a multi-page PDF into individual single-page PDFs.

    Args:
        pdf_path: Path to the source PDF file.

    Returns:
        List of paths to the per-page PDFs, sorted by page number (0-indexed
        → filenames ``page_1.pdf``, ``page_2.pdf``, ...).  Returns an empty
        list if the PDF has zero pages or cannot be opened.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        logger.warning("split_pdf_bundle: file not found — %s", pdf_path)
        return []

    try:
        import fitz
    except ImportError:
        logger.error("split_pdf_bundle: PyMuPDF (fitz) is not installed")
        return []

    output_paths: list[Path] = []
    output_dir = pdf_path.parent / f"{pdf_path.stem}_pages"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        if total_pages <= 1:
            logger.info("split_pdf_bundle: single-page PDF — no split needed")
            return [pdf_path]

        for page_num in range(total_pages):
            doc[page_num]
            new_doc = fitz.open()  # empty document
            new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            out_path = output_dir / f"{_PAGE_PREFIX}{page_num + 1}.pdf"
            new_doc.save(str(out_path))
            new_doc.close()
            output_paths.append(out_path)

        doc.close()
        logger.info("split_pdf_bundle: split %s into %d pages", pdf_path.name, total_pages)
    except Exception as exc:
        logger.error("split_pdf_bundle: failed to split %s — %s", pdf_path, exc)

    return output_paths
