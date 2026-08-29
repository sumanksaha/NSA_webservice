"""Food Cell DO Intimation — service layer.

Public API:
    generate_and_forward_do_intimation(sample_id, sample=None, force=False)
        -> DoIntimation | None

Safe to call from both request scope (sync) and Celery background workers.

The triple-target sync (Google Sheets / Airtable / Excel Online) is delegated
to the shared :func:`app.services.sync_orchestrator.sync_row` seam — one
adapter for the sync concern across the whole app. Tests stub that seam
directly (patch ``food_cell.services.sync_row``); there is no longer a
food_cell-local duplicate of the triple try/except.

Document rendering / storage lives in :class:`DODocumentRenderer`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.extensions import db
from app.food_cell.renderer import DODocumentRenderer
from app.models.billing import Sample
from app.models.food_cell import DoIntimation
from app.services.sync_orchestrator import sync_row

logger = logging.getLogger(__name__)


def _resolve_sample(sample_id: int, sample: Sample | None) -> Sample | None:
    """Return the Sample record, optionally using a pre-fetched object."""
    if sample is None:
        sample = db.session.get(Sample, sample_id)
    return sample


def generate_and_forward_do_intimation(
    sample_id: int,
    sample: Sample | None = None,
    force: bool = False,
) -> DoIntimation | None:
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

    # --- Sync to parallel targets (synchronous, mandatory) ---
    # sync_row fans out to Sheets (primary), Airtable, and Excel Online
    # synchronously — any failure raises and is caught by the caller. The
    # module key "food_cell_do_intimations" resolves the worksheet/table
    # for every target.
    sync_row(
        "food_cell_do_intimations",
        renderer.build_sync_row(sample, intimation),
        entity_id=intimation.id,
    )

    intimation.sync_status = json.dumps({"status": "synced"})
    intimation.status = "forwarded"
    db.session.add(intimation)
    db.session.commit()
    db.session.refresh(intimation)

    logger.info(
        "DO intimation generated for sample %s (ref=%s), sync=ok",
        sample.id,
        do_ref,
    )
    return intimation
