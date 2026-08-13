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
from app.food_cell.renderer import DODocumentRenderer

logger = logging.getLogger(__name__)

#: Module-level renderer instance (shared by generate_and_forward_do_intimation
#: and the backward-compatible module-level wrapper functions).
_renderer = DODocumentRenderer()

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


# --- Thin wrappers around DODocumentRenderer (keep module-level functions
# for backward compatibility with existing tests that import them directly).


def _next_do_reference_no() -> str:
    """Backward-compatible wrapper around DODocumentRenderer.generate_reference."""
    return _renderer.generate_reference()


def _render_html(sample: "Sample") -> str:
    """Backward-compatible wrapper around DODocumentRenderer.render_html."""
    return _renderer.render_html(sample)


def _render_pdf(html: str, sample: "Sample") -> str:
    """Backward-compatible wrapper around DODocumentRenderer.render_pdf."""
    return _renderer.render_pdf(html, sample)


def _store_intimation(intimation: "DoIntimation", sample: "Sample", html: str, pdf_path: str) -> None:
    """Backward-compatible wrapper around DODocumentRenderer.store."""
    _renderer.store(intimation, sample, html, pdf_path)


def _build_sync_row(sample: "Sample", intimation: "DoIntimation") -> dict[str, Any]:
    """Backward-compatible wrapper around DODocumentRenderer.build_sync_row."""
    return _renderer.build_sync_row(sample, intimation)


def _sync_intimation(sample: "Sample", intimation: "DoIntimation") -> dict[str, bool]:
    """Best-effort sync to all parallel targets (Sheets, Airtable, Excel).

    Returns ``{"sheets": bool, "airtable": bool, "excel": bool}``.

    Uses module-level sync-function caches (set by :func:`_load_sync_fns`)
    rather than :func:`~app.services.sync_orchestrator.sync_row` because the
    food_cell tests mock these globals directly.
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

    # --- Reference + intimation record ---
    renderer = DODocumentRenderer()
    do_ref = renderer.generate_reference()
    intimation = DoIntimation(
        sample_id=sample.id,
        do_reference_no=do_ref,
        status="pending",
    )
    db.session.add(intimation)
    db.session.flush()

    # --- Render HTML + PDF (via DODocumentRenderer) ---
    html = renderer.render_html(sample)
    pdf_path = renderer.render_pdf(html, sample)
    renderer.store(intimation, sample, html, pdf_path)

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
