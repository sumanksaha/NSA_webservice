"""Bill issuance — the deep module behind ``POST /generate_bill``.

Owns the whole **Bill issuance** transaction (see CONTEXT.md):

    validate range → recompute totals from Samples → persist Bill *atomically*
    together with marking those Samples billed and linking them → best-effort
    parallel sync → dispatch PDF generation (QStash with synchronous fallback).

Load-bearing invariant (ADR-0001): **no Bill exists unless its Samples are
marked billed.** Persistence never depends on sync or PDF success; a Bill whose
PDF failed is recoverable, a duplicated Bill is not — so ``IssuanceResult``
carries ``bill_id`` even on ``status="error"``.

The interface is deliberately transport-free: the Flask route maps an
``IssuanceResult`` onto HTTP codes, and any future caller (e.g. an
``/api/v2`` mirror) reuses the same seam.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm.exc import StaleDataError

from app.bill_generator.utils import get_billable_samples, mark_samples_as_billed
from app.extensions import db
from app.models import Bill
from app.utils.filters import parse_date

logger = logging.getLogger(__name__)

# Fields forwarded verbatim into the PDF template; everything else in
# ``form_data`` is ignored (server-computed values always win).
TEMPLATE_VARS_ALLOWLIST = frozenset({
    "Name",
    "EMP_ID",
    "Designation",
    "Enf_samp_No",
    "Surv_samp_No",
    "Total_bill",
    "No_of_enfbills",
    "No_of_survbills",
    "TR_Value",
    "TR_date",
    "Submission_date",
    "start_date",
    "end_date",
    "enforcement_price",
    "surveillance_price",
})


@dataclass
class IssuanceResult:
    """Outcome of one bill issuance attempt.

    ``status``:
        - ``"invalid"``   — date range missing/reversed; nothing persisted.
        - ``"conflict"``  — concurrent modification lost (optimistic locking);
                            nothing persisted (atomic rollback).
        - ``"queued"``    — Bill persisted; PDF dispatched asynchronously
                            (``task_id`` holds the QStash message id).
        - ``"generated"`` — Bill persisted; PDF generated inline (fallback).
        - ``"error"``     — Bill persisted but the PDF step failed;
                            ``bill_id`` is still populated so the caller can
                            offer regeneration instead of a duplicate submit.
    """

    status: str
    bill_id: int | None = None
    detail: str | None = None
    task_id: str | None = None
    pdf_result: dict | None = None
    sync: dict | None = None


def validate_range(start_date: str | None, end_date: str | None) -> str | None:
    """Return the range-validation error message, or ``None`` when valid."""
    if not start_date or not end_date:
        return "Both start and end dates are required"
    if end_date < start_date:
        return "End date must be >= start date"
    return None


def issue(start_date: str | None, end_date: str | None, form_data: dict) -> IssuanceResult:
    """Issue a Bill for unbilled Samples between *start_date* and *end_date*.

    See the module docstring for the transaction shape and invariants.
    """
    validation_error = validate_range(start_date, end_date)
    if validation_error:
        return IssuanceResult(status="invalid", detail=validation_error)

    # TR / Submission dates are NOT NULL columns — reject early with a clear
    # "invalid" result instead of an unhandled IntegrityError at flush time.
    tr_date = parse_date(form_data.get("TR_date", ""))
    submission_date = parse_date(form_data.get("Submission_date", ""))
    if tr_date is None or submission_date is None:
        return IssuanceResult(
            status="invalid",
            detail="A valid TR date and Submission date are required",
        )

    # Recompute everything server-side from the Samples.
    sample_data = get_billable_samples(start_date, end_date)
    total_amount = sample_data["enforcement_price"] + sample_data["surveillance_price"]

    bill_record = Bill(
        Name=form_data.get("Name", ""),
        EMP_ID=form_data.get("EMP_ID", ""),
        Designation=form_data.get("Designation", "Food Safety Officer"),
        Enf_samp_No=sample_data["enforcement_no"],
        Surv_samp_No=sample_data["surveillance_no"],
        enforcement_price=sample_data["enforcement_price"],
        surveillance_price=sample_data["surveillance_price"],
        Total_bill=str(total_amount),
        No_of_enfbills=form_data.get("No_of_enfbills", ""),
        No_of_survbills=form_data.get("No_of_survbills", ""),
        TR_Value=form_data.get("TR_Value", ""),
        TR_date=tr_date,
        Submission_date=submission_date,
        start_date=parse_date(start_date),
        end_date=parse_date(end_date),
    )

    # ---- Atomic persist (ADR-0001): Bill + billed flags + links, one commit ----
    db.session.add(bill_record)
    db.session.flush()  # assign bill_record.id without committing
    mark_samples_as_billed(
        [s["sample_id"] for s in sample_data["samples"]],
        bill_record.id,
    )  # stages only — no internal commit
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        return IssuanceResult(
            status="conflict",
            detail="This bill was modified by another user. Please reload and try again.",
        )

    # ---- Best-effort parallel sync (never blocks, never rolls back) ----
    sync_result = None
    try:
        from app.services.sync_orchestrator import sync_row

        row_dict = {k: v for k, v in form_data.items() if k in bill_record.__dict__}
        row_dict["created_at"] = bill_record.created_at.isoformat() if bill_record.created_at else ""
        sync_result = sync_row("billing", row_dict, entity_id=bill_record.id)
        if not sync_result["sheets"]:
            logger.warning("Bill Generator: Sheets sync failed - not blocking")
    except Exception as e:
        logger.warning("Bill Generator sync failed: %s", e)

    # ---- Dispatch PDF generation ----
    template_vars = {k: form_data.get(k, "") for k in TEMPLATE_VARS_ALLOWLIST}
    payload = {"bill_id": bill_record.id, "template_vars": template_vars}
    try:
        from app.utils.qstash_client import make_dedup_key, publish_task

        dispatched = publish_task(
            "generate_bill_pdf",
            payload=payload,
            dedup_key=make_dedup_key("generate_bill_pdf", bill_record.id, payload),
        )
    except Exception as exc:
        logger.error("Bill PDF dispatch failed: %s", exc)
        return IssuanceResult(
            status="error",
            bill_id=bill_record.id,
            detail=f"Bill PDF generation failed: {exc}",
            sync=sync_result,
        )

    if dispatched["mode"] == "async":
        return IssuanceResult(
            status="queued",
            bill_id=bill_record.id,
            task_id=dispatched["message_id"],
            sync=sync_result,
        )

    result = dispatched["result"]

    # Task error metadata → consistent failure reporting (the Bill stands).
    if isinstance(result, Exception):
        logger.error("Bill PDF generation returned exception: %s", result)
        return IssuanceResult(
            status="error",
            bill_id=bill_record.id,
            detail=f"Bill PDF generation failed: {result}",
            sync=sync_result,
        )

    if isinstance(result, dict) and result.get("status") == "error":
        error_msg = result.get("error", "PDF generation failed")
        logger.error("Bill PDF generation returned error: %s", error_msg)
        return IssuanceResult(
            status="error",
            bill_id=bill_record.id,
            detail=error_msg,
            pdf_result=result,
            sync=sync_result,
        )

    return IssuanceResult(
        status="generated",
        bill_id=bill_record.id,
        pdf_result=result,
        sync=sync_result,
    )


__all__ = ["IssuanceResult", "issue", "validate_range"]
