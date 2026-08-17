"""PDF generation utilities — backward-compatible shims.

All logic has been consolidated into :mod:`app.pdf_assembly.engine`.
These functions delegate to a module-level :class:`PDFAssemblyEngine`
instance so that existing import sites (``adjudication.routes``,
``document_viewer.renderer``, ``case_file_generator.tasks``, etc.) need
no changes.
"""

from __future__ import annotations

import logging

from app.pdf_assembly.engine import PDFAssemblyEngine

logger = logging.getLogger(__name__)

_ENGINE: PDFAssemblyEngine | None = None


def _engine() -> PDFAssemblyEngine:
    """Return the shared ``PDFAssemblyEngine`` instance (lazy singleton)."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = PDFAssemblyEngine()
    return _ENGINE


def import_weasyprint():
    """Import WeasyPrint with graceful error handling.

    Delegates to :meth:`PDFAssemblyEngine`'s module-level guard.
    """
    from app.pdf_assembly.engine import import_weasyprint as _import_weasyprint

    return _import_weasyprint()


def generate_pdf_from_html(html_content: str) -> tuple:
    """Generate PDF from HTML string using WeasyPrint.

    Delegates to :meth:`PDFAssemblyEngine.generate_from_html`.
    """
    return _engine().generate_from_html(html_content)


def post_process_pdf_html(
    html_content: str, case_id: int | None = None, adjudication_id: int | None = None
) -> str:
    """Phase 6 + Phase 7 post-processing pass over rendered HTML.

    Delegates to :meth:`PDFAssemblyEngine.post_process`.
    """
    return _engine().post_process(html_content, case_id=case_id, adjudication_id=adjudication_id)


def embed_photos_as_base64(photo_urls: list) -> list[dict]:
    """Fetch photo images and return base64 data URIs.

    Delegates to :meth:`PDFAssemblyEngine.embed_photos`.
    """
    return _engine().embed_photos(photo_urls)


def renumber_html_lists(html_content: str) -> str:
    """Renumbering pass for ``<ol start="N">`` continuation lists (Phase 6).

    Delegates to :meth:`PDFAssemblyEngine.renumber_html_lists`.
    """
    return _engine().renumber_html_lists(html_content)


def _inject_bookmark_css(html_content: str) -> str:
    """Inject WeasyPrint bookmark CSS for h1-h6 headings.

    Delegates to :meth:`PDFAssemblyEngine._inject_bookmark_css`.
    """
    return _engine()._inject_bookmark_css(html_content)
