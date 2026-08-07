"""Food Cell DO Intimation — service layer.

Public API:
    generate_and_forward_do_intimation(sample_id, sample=None, force=False)
        -> DoIntimation | None

Safe to call from both request scope (sync) and Celery background workers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from app.extensions import db
from app.models.billing import Sample
from app.models.food_cell import DoIntimation

logger = logging.getLogger(__name__)

#: Sync function cache (resolved lazily so optional deps don't break import)
_sync_to_sheets: Any = None
_sync_to_airtable: Any = None
_sync_to_excel: Any = None
_sync_lock: bool = False


def _load_sync_fns() -> None:
    """Lazily import sync helpers so the food_cell module can bootstrap
    even when optional sync deps (gspread, pyairtable, msal) are absent.
    """
    global _sync_to_sheets, _sync_to_airtable, _sync_to_excel, _sync_lock
    if _sync_lock:
        return

    try:
        from app.services.sheets_sync import sync_to_sheets

        _sync_to_sheets = sync_to_sheets
    except Exception:  # noqa: BLE001
        _sync_to_sheets = None
        logger.warning("sync_to_sheets unavailable; DO intimation won't sync to Sheets")

    try:
        from app.services.airtable_sync import sync_to_airtable

        _sync_to_airtable = sync_to_airtable
    except Exception:  # noqa: BLE001
        _sync_to_airtable = None
        logger.warning("sync_to_airtable unavailable")

    try:
        from app.services.excel_sync import sync_to_excel

        _sync_to_excel = sync_to_excel
    except Exception:  # noqa: BLE001
        _sync_to_excel = None
        logger.warning("sync_to_excel unavailable")

    _sync_lock = True


def _resolve_sample(sample_id: int, sample: "Sample | None") -> "Sample | None":
    """Return the Sample record, optionally using a pre-fetched object."""
    if sample is None:
        sample = db.session.get(Sample, sample_id)
    return sample


def _next_do_reference_no() -> str:
    """Generate a unique DO reference number via the CodeSequence table."""
    from app.models.billing import CodeSequence

    seq: CodeSequence | None = db.session.get(CodeSequence, "do_intimation")
    if seq is None:
        seq = CodeSequence(key="do_intimation", last_value=0)
        db.session.add(seq)
        db.session.flush()
    seq.last_value += 1
    db.session.flush()
    year = datetime.now(UTC).year
    return f"DO/{year}/{seq.last_value:06d}"


def _render_html(sample: "Sample") -> str:
    """Render the DO intimation HTML template for *sample*."""
    from flask import render_template

    return render_template("food_cell/do_intimation.html", sample=sample)


def _render_pdf(html: str, sample: "Sample") -> str:
    """Render *html* to PDF and store it, returning the local filepath.

    When WeasyPrint is unavailable (e.g. in test environments with
    ``DISABLE_PDF_GENERATION=1``), a minimal valid 1-page PDF stub is
    written so downstream consumers (download endpoint, file checks) still
    work.
    """
    from app.pdf_assembly import PDFAssemblyEngine
    from pathlib import Path

    engine = PDFAssemblyEngine()
    pdf_bytes, error = engine.generate_from_html(html)
    if pdf_bytes is None:
        # Fallback: write a minimal valid PDF stub so downstream
        # consumers (download endpoint, file checks) still function.
        logger.warning("PDF generation unavailable for sample %s; writing stub: %s", sample.id, error)
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


def _store_intimation(intimation: "DoIntimation", sample: "Sample", html: str, pdf_path: str) -> None:
    """Persist HTML and PDF paths on the *intimation* record."""
    from pathlib import Path

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


def _build_sync_row(sample: "Sample", intimation: "DoIntimation") -> dict[str, Any]:
    """Build the canonical row dict for Sheets / Airtable / Excel sync."""
    return {
        "sample_id": sample.id,
        "sample_code": getattr(sample, "sample_code", ""),
        "sample_name": getattr(sample, "sample_name", ""),
        "fso_name": getattr(sample, "fso_name", ""),
        "retailer_name": getattr(sample, "retailer_name", ""),
        "collection_date": sample.collection_date.isoformat() if sample.collection_date else "",
        "do_reference_no": intimation.do_reference_no,
        "food_cell_forwarded": (intimation.food_cell_forwarded.isoformat() if intimation.food_cell_forwarded else ""),
        "status": intimation.status,
        "pdf_url": intimation.pdf_url or "",
    }


def _sync_intimation(sample: "Sample", intimation: "DoIntimation") -> dict[str, bool]:
    """Best-effort sync to all parallel targets (Sheets, Airtable, Excel).

    Returns ``{"sheets": bool, "airtable": bool, "excel": bool}``.
    """
    _load_sync_fns()
    results: dict[str, bool] = {}
    row = _build_sync_row(sample, intimation)

    # Sync to Sheets (module key → worksheet name via WORKSHEET_MAP)
    if _sync_to_sheets is not None:
        try:
            _sync_to_sheets("food_cell_do_intimations", row)
            results["sheets"] = True
        except Exception:  # noqa: BLE001
            logger.exception("sync_to_sheets failed for sample %s", sample.id)
            results["sheets"] = False
    else:
        results["sheets"] = False

    # Sync to Airtable (module key → table name via AIRTABLE_TABLE_MAP)
    if _sync_to_airtable is not None:
        try:
            _sync_to_airtable("food_cell_do_intimations", row, intimation.id)
            results["airtable"] = True
        except Exception:  # noqa: BLE001
            logger.exception("sync_to_airtable failed for sample %s", sample.id)
            results["airtable"] = False
    else:
        results["airtable"] = False

    # Sync to Excel Online (worksheet name via WORKSHEET_MAP)
    if _sync_to_excel is not None:
        try:
            _sync_to_excel("FoodCellDOIntimations", row)
            results["excel"] = True
        except Exception:  # noqa: BLE001
            logger.exception("sync_to_excel failed for sample %s", sample.id)
            results["excel"] = False
    else:
        results["excel"] = False

    return results


def generate_and_forward_do_intimation(
    sample_id: int,
    sample: "Sample | None" = None,
    force: bool = False,
) -> "DoIntimation | None":
    """Generate a DO intimation for *sample_id* and forward to Food Cell.

    Called from:
        - ``app/sample/routes.py`` ``create_sample()`` (via Celery task)
        - ``app/food_cell/routes.py`` ``regenerate_do_intimation()`` (manual re-render)

    Parameters
    ----------
    sample_id : int
        Primary key of the :class:`~app.models.billing.Sample`.
    sample : Sample | None
        Optional pre-fetched Sample (avoids a redundant query).
    force : bool
        If True, regenerate even if a DoIntimation already exists.

    Returns
    -------
    DoIntimation | None
        The persisted intimation record, or None if the sample was not found.
    """
    sample = _resolve_sample(sample_id, sample)
    if sample is None:
        logger.warning("generate_and_forward_do_intimation: sample %s not found", sample_id)
        return None

    existing: DoIntimation | None = DoIntimation.query.filter_by(sample_id=sample.id).first()
    if existing is not None and not force:
        return existing

    if existing is not None and force:
        db.session.delete(existing)
        db.session.flush()

    do_ref = _next_do_reference_no()
    intimation = DoIntimation(
        sample_id=sample.id,
        do_reference_no=do_ref,
        status="pending",
    )
    db.session.add(intimation)
    db.session.flush()

    # --- Render HTML + PDF ---
    html = _render_html(sample)
    pdf_path = _render_pdf(html, sample)
    _store_intimation(intimation, sample, html, pdf_path)

    # --- Update Sample forward timestamp ---
    sample.food_cell_forwarded = datetime.now(UTC)

    # --- Sync to parallel targets (best-effort) ---
    sync_results = _sync_intimation(sample, intimation)

    intimation.sync_status = json.dumps(sync_results)
    intimation.status = "forwarded"
    db.session.add(intimation)
    db.session.commit()
    db.session.refresh(intimation)

    logger.info(
        "DO intimation generated for sample %s (ref=%s), sync=%s",
        sample.id,
        do_ref,
        sync_results,
    )
    return intimation
