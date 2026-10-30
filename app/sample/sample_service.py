"""Sample collection service — deep module for the inspection sample block.

Owns validation, upsert, unlink and sync of a ``Sample`` tied to an
``Inspection``. The inspection routes stay thin transport: they parse the
form, call the service, commit the transaction, then sync.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from app.extensions import db
from app.models import Inspection, Sample
from app.sample.sample_utils import (
    validate_sample_code,
    validate_sample_status,
    sample_to_sync_row,
)
from app.services.sync_orchestrator import sync_row

logger = logging.getLogger(__name__)

VALID_SAMPLE_TYPES = ("enforcement", "surveillance")


@dataclass
class SampleCollectionResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    sample: Sample | None = None
    synced: bool = True


def _form_str(form_data: dict, key: str) -> str:
    val = form_data.get(key)
    if val is None:
        return ""
    return str(val).strip()


def validate_collection(form_data: dict) -> list[str]:
    errors: list[str] = []
    sample_type = _form_str(form_data, "sample_type")
    if not sample_type:
        errors.append("Sample type is required.")
    elif sample_type not in VALID_SAMPLE_TYPES:
        errors.append("Sample type must be enforcement or surveillance.")

    sample_code = _form_str(form_data, "sample_code")
    code_err = validate_sample_code(sample_code, sample_type if sample_type else None)
    if code_err:
        errors.append(code_err)

    if not _form_str(form_data, "collection_date"):
        errors.append("Collection date is required.")
    if not _form_str(form_data, "sample_name"):
        errors.append("Sample name is required.")
    price = _form_str(form_data, "price")
    if not price:
        errors.append("Price is required.")

    status = _form_str(form_data, "sample_status")
    status_err = validate_sample_status(status if status else None)
    if status_err:
        errors.append(status_err)

    return errors


from app.utils.filters import parse_date


def _apply_fields(sample: Sample, inspection: Inspection, form_data: dict) -> None:
    sample.sample_type = _form_str(form_data, "sample_type")
    sample.sample_code = _form_str(form_data, "sample_code") or None
    sample.sample_name = _form_str(form_data, "sample_name")
    sample.collection_date = _form_str(form_data, "collection_date")
    price = _form_str(form_data, "price")
    try:
        sample.price = float(price) if price else None
    except (TypeError, ValueError):
        sample.price = None
    sample.sample_status = _form_str(form_data, "sample_status") or None
    sample.fso_name = inspection.food_safety_officer
    sub = _form_str(form_data, "submission_date")
    sample.submission_date = parse_date(sub) if sub else None
    sample.batch_no = _form_str(form_data, "batch_no") or None
    sample.mfd = _form_str(form_data, "mfd") or None
    sample.exp = _form_str(form_data, "exp") or None
    sample.manufacturer_details = _form_str(form_data, "manufacturer_details") or None
    if getattr(sample, "id", None) is not None:
        sample.version_id = (sample.version_id or 1) + 1


def create_or_update_sample(inspection: Inspection, form_data: dict) -> SampleCollectionResult:
    result = SampleCollectionResult()
    errors = validate_collection(form_data)
    if errors:
        result.ok = False
        result.errors = errors
        return result

    sample_code = _form_str(form_data, "sample_code")
    existing = None
    if sample_code:
        existing = Sample.query.filter_by(sample_code=sample_code).first()

    if existing is not None:
        if existing.inspection_id is not None and existing.inspection_id != inspection.id:
            result.ok = False
            result.errors = ["Sample code already linked to another inspection."]
            return result
        sample = existing
        sample.inspection_id = inspection.id
    else:
        sample = Sample(inspection_id=inspection.id)
        db.session.add(sample)

    _apply_fields(sample, inspection, form_data)
    db.session.flush()
    result.sample = sample
    return result


def unlink_sample(inspection: Inspection) -> None:
    if inspection.sample is not None:
        inspection.sample.inspection_id = None
    inspection.sample_collected = False
    inspection.sample_code = None


def sync_sample(sample: Sample) -> bool:
    try:
        now = datetime.now(UTC)
        row = sample_to_sync_row(sample, synced_at=now)
        sync_row("sample_repo", row)
        sample.synced_at = now
        db.session.flush()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("sample sync failed: %s", exc)
        return False


__all__ = [
    "SampleCollectionResult",
    "create_or_update_sample",
    "sync_sample",
    "unlink_sample",
    "validate_collection",
]
