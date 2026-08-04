"""PDF Assembly Engine package.

Re-exports :class:`PDFAssemblyEngine` and :func:`assemble_complete_case_pdf`
from :mod:`app.pdf_assembly.engine`, the single source of truth for all
PDF-generation logic.

All PDF operations that were previously scattered across
``app/utils/pdf_utils.py`` (WeasyPrint guard, bookmark CSS, post-processing
orchestration, photo embedding) now live behind the engine's canonical
interface methods:

    - ``generate_from_html(html)  -> (bytes|None, error|None)``
    - ``post_process(html, **kw)   -> str``
    - ``embed_photos(urls)         -> list[dict]``
    - ``assemble(html, **kw)       -> (bytes|None, error|None)``

For backward compatibility, ``app/utils/pdf_utils.py`` provides thin shims
that delegate to a module-level ``PDFAssemblyEngine`` instance.
"""

from app.pdf_assembly.engine import PDFAssemblyEngine, assemble_complete_case_pdf, import_weasyprint

__all__ = ["PDFAssemblyEngine", "assemble_complete_case_pdf", "import_weasyprint"]
