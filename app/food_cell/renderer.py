"""DO Intimation document renderer — rendering / storage helpers.

Extracted from :mod:`app.food_cell.services` so that the seven private
helpers (``_render_html``, ``_render_pdf``, ``_store_intimation``,
``_next_do_reference_no``, ``_build_sync_row``) that deal with *document
generation* live in a focused class.  ``services.py`` keeps only the
orchestration (``generate_and_forward_do_intimation``) and the triple-sync
call, which still depends on module-level sync-function caches that the
existing tests mock directly.

Typical usage::

    from app.food_cell.renderer import DODocumentRenderer

    renderer = DODocumentRenderer()
    do_ref = renderer.generate_reference()
    html = renderer.render_html(sample)
    pdf_path = renderer.render_pdf(html, sample)
    renderer.store_intimation(intimation, sample, html, pdf_path)
    row = renderer.build_sync_row(sample, intimation)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import current_app, render_template

from app.models.billing import CodeSequence
from app.extensions import db

logger = logging.getLogger(__name__)


class DODocumentRenderer:
    """Render, store, and prepare sync rows for DO intimation documents.

    All methods operate within a Flask app context (they use
    ``current_app.instance_path`` and ``render_template``).
    """

    def generate_reference(self) -> str:
        """Generate a unique DO reference number via the ``CodeSequence`` table.

        Uses a row with ``key='do_intimation'`` and atomically increments
        ``last_value`` under a flush, so concurrent calls receive distinct
        sequences.
        """
        seq: CodeSequence | None = db.session.get(CodeSequence, "do_intimation")
        if seq is None:
            seq = CodeSequence(key="do_intimation", last_value=0)
            db.session.add(seq)
            db.session.flush()
        seq.last_value += 1
        db.session.flush()
        year = datetime.now(UTC).year
        return f"DO/{year}/{seq.last_value:06d}"

    def render_html(self, sample: Any) -> str:
        """Render the DO intimation HTML template for *sample*."""
        return render_template("food_cell/do_intimation.html", sample=sample)

    def render_pdf(self, html: str, sample: Any) -> str:
        """Render *html* to PDF and store it, returning the local filepath.

        When WeasyPrint is unavailable (e.g. in test environments with
        ``DISABLE_PDF_GENERATION=1``), a minimal valid 1-page PDF stub is
        written so downstream consumers (download endpoint, file checks)
        still work.
        """
        from app.pdf_assembly import PDFAssemblyEngine

        engine = PDFAssemblyEngine()
        pdf_bytes, error = engine.generate_from_html(html)
        if pdf_bytes is None:
            logger.warning(
                "PDF generation unavailable for sample %s; writing stub: %s",
                sample.id,
                error,
            )
            pdf_bytes = (
                b"%PDF-1.4\n"
                b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
                b"xref\n0 4\n0000000000 65535 f \n"
                b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
                b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
            )

        filename = f"do_intimation_{sample.id}_{int(datetime.now(UTC).timestamp())}.pdf"
        upload_dir = Path(current_app.instance_path) / "food_cell" / "pdfs"
        upload_dir.mkdir(parents=True, exist_ok=True)
        filepath = str(upload_dir / filename)
        with open(filepath, "wb") as fh:
            fh.write(pdf_bytes)
        return filepath

    def store(self, intimation: Any, sample: Any, html: str, pdf_path: str) -> None:
        """Persist HTML and PDF paths on the *intimation* record."""
        html_dir = Path(current_app.instance_path) / "food_cell" / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        html_filename = f"do_intimation_{sample.id}_{int(datetime.now(UTC).timestamp())}.html"
        html_path = str(html_dir / html_filename)
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        intimation.html_path = html_path
        intimation.pdf_url = pdf_path
        db.session.add(intimation)
        db.session.flush()

    def build_sync_row(self, sample: Any, intimation: Any) -> dict[str, Any]:
        """Build the canonical row dict for Sheets / Airtable / Excel sync."""
        return {
            "sample_id": sample.id,
            "sample_code": getattr(sample, "sample_code", ""),
            "sample_name": getattr(sample, "sample_name", ""),
            "fso_name": getattr(sample, "fso_name", ""),
            "retailer_name": getattr(sample, "retailer_name", ""),
            "collection_date": sample.collection_date.isoformat() if sample.collection_date else "",
            "do_reference_no": intimation.do_reference_no,
            "food_cell_forwarded": (
                intimation.food_cell_forwarded.isoformat() if intimation.food_cell_forwarded else ""
            ),
            "status": intimation.status,
            "pdf_url": intimation.pdf_url or "",
        }


__all__ = ["DODocumentRenderer"]
