"""Autopopulation services (Phase C).

- :func:`build_verified_record` — merge the Sample row + the latest reviewed
  OCR extraction (corrections already applied in ``extracted_json``) into one
  addressable record.
- :func:`prefill` — project the verified record through :data:`MAPPINGS` into
  per-consumer prefill bundles.
- :func:`non_conforming_params` — lab parameters whose observed value differs
  from the declared standard.
- :func:`draft_fbo_issue_for_sample` — auto-draft an ``FboIssue`` (state ``open``, ``source_type=sample``) when a sample has non-conforming
  lab parameters, per the Phase C spec.
"""

from __future__ import annotations

import json
import logging

from app.autopopulation.mappings import MAPPINGS, resolve_path

logger = logging.getLogger(__name__)


def build_verified_record(sample_id: int) -> dict | None:
    """Merge Sample + latest completed OCRDocument into one record.

    Returns ``None`` when the sample does not exist. The OCR section is omitted
    when the sample has no completed extraction yet.
    """
    from app.extensions import db
    from app.models import OCRDocument, Sample

    sample = db.session.get(Sample, sample_id)
    if sample is None:
        return None

    record: dict = {
        "sample": {
            "sample_code": sample.sample_code or "",
            "sample_name": sample.sample_name or "",
            "retailer_name": sample.retailer_name or "",
            "retailer_fssai": sample.retailer_fssai or "",
            "nature_of_food": sample.nature_of_food or "",
            "batch_no": sample.batch_no or "",
            "mfd": sample.mfd or "",
            "exp": sample.exp or "",
            "manufacturer_details": sample.manufacturer_details or "",
            "fso_name": sample.fso_name or "",
        },
        "lab": {},
    }

    latest = (
        db.session.query(OCRDocument)
        .filter(OCRDocument.sample_id == sample_id, OCRDocument.status == "completed")
        .order_by(OCRDocument.created_at.desc())
        .first()
    )
    if latest is not None:
        import json

        try:
            payload = json.loads(latest.extracted_json or "{}")
        except (TypeError, ValueError):
            payload = {}
        record["ocr"] = {"fields": payload.get("fields", {}), "document_id": latest.id}

    for param in _lab_parameters(sample_id):
        entry = record["lab"].setdefault(param.parameter_name.lower(), {})
        entry["observed"] = param.observed_value
        entry["standard"] = param.standard_value

    return record


def prefill(sample_id: int) -> dict | None:
    """Per-consumer prefill bundles; keys with no resolvable value are omitted."""
    record = build_verified_record(sample_id)
    if record is None:
        return None

    record = _augment_counts(record, sample_id)
    bundles: dict[str, dict] = {}
    for consumer, field_map in MAPPINGS.items():
        bundle = {}
        for form_field, source_path in field_map.items():
            value = resolve_path(record, source_path)
            if value not in (None, ""):
                bundle[form_field] = value
        if bundle:
            bundles[consumer] = bundle
    return {"sample_id": sample_id, "prefill": bundles}


def non_conforming_params(sample_id: int) -> list:
    """Lab parameters whose observed value differs from the declared standard."""
    return [
        p
        for p in _lab_parameters(sample_id)
        if p.standard_value and p.observed_value and p.standard_value.strip() != p.observed_value.strip()
    ]


def draft_fbo_issue_for_sample(sample_id: int):
    """Auto-draft an FboIssue for a sample with non-conforming lab parameters.

    Idempotent: returns the existing open lab_report issue for this sample if
    one is already drafted. Returns ``None`` when the sample is conforming
    (all observed values match standards) or the sample/FBO identity is unknown.
    """
    from app.extensions import db
    from app.models import FboIssue, Sample

    sample = db.session.get(Sample, sample_id)
    if sample is None:
        return None

    failures = non_conforming_params(sample_id)
    if not failures:
        return None

    existing = None
    candidates = (
        db.session.query(FboIssue)
        .filter(FboIssue.source_type == "sample", FboIssue.state == "open")
        .all()
    )
    for candidate in candidates:
        try:
            detail = json.loads(candidate.detail_json or "{}")
        except (TypeError, ValueError):
            continue
        if detail.get("sample_id") == sample_id:
            existing = candidate
            break
    if existing is not None:
        return existing

    issue = FboIssue(
        fbo_id=sample.retailer_fssai or f"sample-{sample.sample_code}",
        fbo_name=sample.retailer_name or sample.sample_name or f"Sample {sample.sample_code}",
        source_type="sample",
        state="open",
        fso_name=sample.fso_name or "unknown",
        detail_json=json.dumps({
            "sample_id": sample_id,
            "sample_code": sample.sample_code,
            "non_conforming": [
                {
                    "parameter": p.parameter_name,
                    "standard": p.standard_value,
                    "observed": p.observed_value,
                    "unit": p.unit or "",
                }
                for p in failures
            ],
        }),
    )
    db.session.add(issue)
    db.session.commit()
    logger.info(
        "draft_fbo_issue_for_sample: drafted FboIssue for sample %s (%d non-conforming)",
        sample_id,
        len(failures),
    )
    return issue


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _lab_parameters(sample_id: int) -> list:
    from app.models import LabTestParameter

    return (
        _db()
        .session.query(LabTestParameter)
        .filter(LabTestParameter.sample_id == sample_id)
        .order_by(LabTestParameter.parameter_name.asc())
        .all()
    )


def _db():
    from app.extensions import db

    return db


def _augment_counts(record: dict, sample_id: int) -> dict:
    """Fill the synthetic lab.__enf_count__/__surv_count__ paths used by the
    bill mapping (enforcement/surveillance parameter counts)."""
    params = _lab_parameters(sample_id)
    counts: dict[str, int] = {}
    for p in params:
        stype = (p.standard_value or "").lower()
        if "enf" in stype:
            counts["enf"] = counts.get("enf", 0) + 1
        elif "surv" in stype:
            counts["surv"] = counts.get("surv", 0) + 1
    record.setdefault("lab", {})
    record["lab"]["__enf_count__"] = {"observed": str(counts.get("enf", 0))}
    record["lab"]["__surv_count__"] = {"observed": str(counts.get("surv", 0))}
    return record
