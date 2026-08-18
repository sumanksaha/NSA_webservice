"""Plugin implementations for PDF rendering providers.

Wraps :class:`app.pdf_assembly.engine.PDFAssemblyEngine` behind the
``PDFProvider`` interface.

The engine already serves as the central PDF generation entry point
(``app/utils/pdf_utils.py`` delegates to it), so this plugin is a thin
adapter — callers that use :class:`PluginRegistry` or ``pdf_utils`` both
end up at the same underlying engine.  Uses lazy imports.
"""

from __future__ import annotations

import logging
from typing import Any

from app.plugins.base import PDFProvider

logger = logging.getLogger(__name__)


class WeasyPrintPDFPlugin(PDFProvider):
    """PDF provider wrapping :class:`PDFAssemblyEngine`.

    Delegates to ``PDFAssemblyEngine.generate_from_html()`` (which handles
    the WeasyPrint import guard via ``import_weasyprint()``) and
    ``post_process()`` for header/footer/bookmark injection.

    When ``DISABLE_PDF_GENERATION=1`` is set (CI/testing), ``render_pdf``
    raises ``RuntimeError`` and ``render_pdf_safe`` returns
    ``(None, "PDF generation disabled")``.
    """

    def _engine(self) -> Any:
        """Lazily build and return the shared PDFAssemblyEngine instance."""
        from app.pdf_assembly.engine import PDFAssemblyEngine

        # The engine module already exposes a lazy singleton pattern
        return PDFAssemblyEngine()

    def get_engine(self) -> Any:
        """Return the underlying PDFAssemblyEngine (for advanced callers).

        ``render_pdf_safe`` covers the common ``generate_from_html`` use case.
        Callers that need engine-specific methods (e.g.
        ``assemble_complete_case_pdf``) can use this escape hatch.
        """
        return self._engine()

    def render_pdf(self, html_content: str, **kwargs: Any) -> bytes:
        """Render HTML to PDF bytes.

        Raises:
            RuntimeError: When WeasyPrint is unavailable or PDF generation
                is disabled.
        """
        engine = self._engine()
        pdf_bytes, error = engine.generate_from_html(html_content)
        if pdf_bytes is None:
            raise RuntimeError(error or "PDF generation failed")
        return pdf_bytes

    def render_pdf_safe(self, html_content: str, **kwargs: Any) -> tuple[bytes | None, str | None]:
        """Render HTML to PDF, returning (pdf_bytes, error) instead of raising.

        Applies post-processing (header/footer/bookmark injection) when
        any of ``case_id``, ``adjudication_id``, ``post_process`` are passed.
        """
        engine = self._engine()

        # Optional post-processing step (only if requested)
        processed_html = html_content
        if (
            kwargs.get("post_process", False)
            or kwargs.get("case_id") is not None
            or kwargs.get("adjudication_id") is not None
        ):
            processed_html = engine.post_process(
                html_content,
                case_id=kwargs.get("case_id"),
                adjudication_id=kwargs.get("adjudication_id"),
            )

        pdf_bytes, error = engine.generate_from_html(processed_html)
        return pdf_bytes, error


__all__ = ["WeasyPrintPDFPlugin"]
