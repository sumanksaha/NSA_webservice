"""Inspection Sample Service — single deep module behind sample collection.

Extracts sample orchestration from the inspection UI (conditional "Sample
Collection" block) and the standalone sample UI into one testable module.

Previously the sample validation, creation, and sync logic was spread across:
  - app/inspection/routes.py  (create_inspection, update_inspection)
  - app/sample/routes.py      (create_sample, update_sample)
  - app/sample/sample_utils.py (validate_sample_code, validate_sample_status)

This module owns the public interface:
  - create_sample(inspection_id, form_data) -> (sample, error)
  - update_sample(sample_id, form_data) -> (sample, error)
  - validate_sample_code(code, sample_type) -> error | None
  - validate_sample_status(status) -> error | None
  - sample_to_sync_row(sample) -> dict

The service is deliberately thin — it delegates to the existing model
methods and validation functions, but centralises the orchestration
flow so callers have one seam to target.
"""
from __future__ import annotations

from typing import Any

from app.extensions import db
from app.models import FSO, Sample
from app.sample.sample_utils import validate_sample_code, validate_sample_status
from app.services.sync_orchestrator import sync_row
from app.utils.filters import parse_date
from app.utils.fso_data import get_all_fso_names
from app.utils.lookup import lookup_fssai

__all__ = [
    "create_sample",
    "update_sample",
    "validate_sample_code",
    "validate_sample_status",
    "sample_to_sync_row",
]


def validate_sample_code(code: str | None, sample_type: str | None = None) -> str | None:
    """Validate a sample code against the format for its sample_type.

    Same contract as ``app.sample.sample_utils.validate_sample_code`` but
    imported through this service seam so callers don't need to know about
    the lower-level module.
    """
    return validate_sample_code(code, sample_type)


def validate_sample_status(status: str | None) -> str | None:
    """Validate a sample status value.

    Returns an error message on failure, ``None`` on success.
    """
    return validate_sample_status(status)


def create_sample(inspection_id: int, form_data: dict[str, Any]) -> tuple[Sample | None, str | None]:
    """Create a Sample linked to an Inspection.

    Returns ``(sample, error)`` where ``error`` is ``None`` on success or an
    error string to surface to the caller.

    This centralises the create-flow logic that previously lived in
    ``inspection_routes.create_inspection`` and ``sample_routes.create_sample``.
    """
    from flask import current_app

    from app.models import Inspection

    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return None, f"Inspection with id {inspection_id} not found"

    # --- Required fields ---
    sample_name = form_data.get("sample_name", "").strip()
    food_safety_officer_name = form_data.get("food_safety_officer_name", "").strip()
    sample_draw_date = form_data.get("sample_draw_date", "").strip()

    if not sample_name:
        return None, "sample_name is required"
    if not food_safety_officer_name:
        return None, "food_safety_officer_name is required"
    if not sample_draw_date:
        return None, "sample_draw_date is required"

    # --- Sample type ---
    sample_type_val = form_data.get("sample_type", "").strip().lower()
    if not sample_type_val or sample_type_val not in ("enforcement", "surveillance"):
        return None, "sample_type must be 'enforcement' or 'surveillance'"

    # --- Sample code: FSO-entered, validated against format pattern ---
    sample_code = form_data.get("sample_code", "").strip()
    code_error = validate_sample_code(sample_code, sample_type_val)
    if code_error:
        return None, code_error

    # Uniqueness check (DB also has UNIQUE constraint, but give a friendly error)
    existing = Sample.query.filter_by(sample_code=sample_code).first()
    if existing and existing.inspection_id:
        return None, f"Sample code '{sample_code}' already linked to another inspection"
    if existing:
        # Sample exists but unlinked; we'll update it below (fall through to create logic)
        pass

    # --- FSO validation ---
    fso = db.session.get(FSO, food_safety_officer_name)
    if not fso:
        return None, f'FSO "{food_safety_officer_name}" not found in database'

    # --- Retailer autofill ---
    retailer_fssai_license = form_data.get("retailer_fssai_license", "").strip()
    retailer_person_name = form_data.get("retailer_person_name", "").strip()

    if retailer_fssai_license and not retailer_person_name:
        result = lookup_fssai(retailer_fssai_license)
        if result.found and not result.error:
            retailer_person_name = result.data.get("companyName", retailer_fssai_license)

    # --- Collection and submission dates ---
    collection_date = parse_date(sample_draw_date)
    submission_date = parse_date(
        form_data.get("sample_submission_date", "").strip() or None
    )

    # --- Price (canonical: total_cost) ---
    price = form_data.get("total_cost", "").strip() or None

    # --- Build the Sample record ---
    sample = Sample(
        sample_code=sample_code,
        sample_name=sample_name,
        sample_type=sample_type_val,
        fso_name=food_safety_officer_name,
        collection_date=collection_date,
        submission_date=submission_date,
        retailer_fssai=retailer_fssai_license or None,
        retailer_name=retailer_person_name or None,
        price=price,
        created_at=datetime.now(UTC),
    )

    # --- Persist ---
    try:
        db.session.add(sample)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return None, f"Failed to create sample: {e!s}"

    # --- Sync to Sheets (best-effort) ---
    try:
        row_dict = sample_to_sync_row(sample, synced_at=datetime.now(UTC).isoformat())
        sync_row("sample_repo", row_dict, entity_id=sample.id)
        sample.synced_at = datetime.now(UTC)
        db.session.commit()
    except Exception as e:
        current_app.logger.warning(f"Sample sync failed (non-fatal): {e}")

    # --- Post-save: trigger Food Cell DO intimation (best-effort, via QStash
    # with synchronous inline fallback) ---
    try:
        from app.utils.qstash_client import publish_task

        publish_task("send_do_intimation", {"sample_id": sample.id})
    except Exception as e:
        current_app.logger.warning(f"DO intimation trigger failed: {e}")

    return sample, None


def update_sample(sample_id: int, form_data: dict[str, Any]) -> tuple[Sample | None, str | None]:
    """Update a Sample record.

    Centralises the update-flow logic that previously lived in
    ``sample_routes.update_sample``.

    Returns ``(sample, error)`` where ``error`` is ``None`` on success or an
    error string to surface to the caller.
    """
    sample = db.session.get(Sample, sample_id)
    if not sample:
        return None, f"Sample with id {sample_id} not found"

    # Update fields if present in form data
    if "sample_name" in form_data:
        sample.sample_name = form_data["sample_name"].strip()
    if "sample_type" in form_data:
        sample_type_val = form_data["sample_type"].strip()
        if not sample_type_val:
            return None, "sample_type cannot be empty"
        if sample_type_val not in ("enforcement", "surveillance"):
            return None, f"sample_type must be 'enforcement' or 'surveillance', got '{sample_type_val}'"
        sample.sample_type = sample_type_val

    # Validate sample_code if provided (FSO-entered, format-checked)
    if "sample_code" in form_data:
        new_code = form_data["sample_code"].strip()
        effective_type = sample.sample_type  # current type (may have been updated above)
        code_error = validate_sample_code(new_code, effective_type)
        if code_error:
            return None, code_error
        # Uniqueness check (skip if code unchanged)
        if new_code != sample.sample_code:
            existing = Sample.query.filter_by(sample_code=new_code).first()
            if existing:
                return None, f"Sample code '{new_code}' already exists"
        sample.sample_code = new_code

    if "fso_name" in form_data:
        fso_name = form_data["fso_name"].strip()
        from app.models import FSO as _FSO
        fso = db.session.get(_FSO, fso_name)
        if not fso:
            return None, f'FSO "{fso_name}" not found'
        sample.fso_name = fso_name

    if "collection_date" in form_data:
        sample.collection_date = parse_date(form_data["collection_date"].strip())

    if "submission_date" in form_data:
        sample.submission_date = parse_date(form_data["submission_date"].strip() or None)

    if "retailer_fssai" in form_data:
        sample.retailer_fssai = form_data["retailer_fssai"].strip() or None

    if "retailer_name" in form_data:
        sample.retailer_name = form_data["retailer_name"].strip() or None

    if "price" in form_data:
        sample.price = form_data["price"].strip() or None

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return None, f"Failed to update sample: {e!s}"

    # Sync to Sheets
    try:
        row_dict = sample_to_sync_row(sample, synced_at=datetime.now(UTC).isoformat())
        sync_row("sample_repo", row_dict, entity_id=sample.id)
    except Exception as e:
        current_app.logger.warning(f"Sample update sync failed: {e}")

    return sample, None


def sample_to_sync_row(sample: Sample, synced_at: str = "") -> dict:
    """Build the canonical sync row for a Sample (shared by create and update).

    One source of truth for the ``sample_repo`` sync payload, shared by the
    create and update handlers (which previously copy-pasted the same dict
    literal and diverged only on ``synced_at``).

    Args:
        sample: A persisted :class:`~app.models.billing.Sample`.
        synced_at: ISO timestamp to mark the row as synced (empty string on a
            freshly-created sample that has not yet synced).

    Returns:
        Dict keyed to ``app.services.sheets_sync.SHEET_COLUMNS`` column order
        for the ``sample_repo`` module.
    """
    from app.sample.sample_utils import sample_to_sync_row as _original

    return _original(sample, synced_at=synced_at)