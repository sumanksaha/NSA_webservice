"""Review-workflow services (Phase B).

- :func:`apply_field_corrections` — diff submitted values against the stored
  extraction, write one :class:`OCRCorrection` per changed field, update
  ``extracted_json`` in place.
- :func:`correct_lab_parameter` — same bookkeeping for a lab-test parameter's
  observed value (source flips to ``manual``).
- Conflict rule: a field correction that disagrees with a lab-report value for
  the same field name opens an unresolved :class:`ConflictLog` entry so the
  conflict-resolution queue can surface it (spec: "writing field discrepancies
  to ConflictLog").
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Outcome of one correction submission."""

    applied: list[dict] = field(default_factory=list)  # {field_name, old, new}
    skipped: int = 0  # submitted values identical to stored ones
    conflicts_opened: int = 0

    @property
    def applied_count(self) -> int:
        return len(self.applied)


def _db():
    from app.extensions import db

    return db


def load_payload(ocr_doc) -> dict:
    """Parse ``extracted_json`` into a dict (empty dict on corrupt JSON)."""
    try:
        return json.loads(ocr_doc.extracted_json or "{}")
    except (TypeError, ValueError):
        return {}


def apply_field_corrections(doc_id: str, corrections: dict[str, str], user_id: int | None = None) -> ApplyResult:
    """Apply ``{field_name: new_value}`` corrections to an OCRDocument.

    Only fields whose value actually changes produce an OCRCorrection row;
    unchanged submissions are counted as skipped. The stored
    ``extracted_json.fields`` is updated so downstream consumers (autopopulation,
    Phase C) always see reviewed values.
    """
    from app.extensions import db
    from app.models import OCRCorrection, OCRDocument

    ocr_doc = db.session.get(OCRDocument, doc_id)
    if ocr_doc is None:
        raise LookupError(f"OCRDocument {doc_id} not found")

    payload = load_payload(ocr_doc)
    stored_fields: dict = payload.setdefault("fields", {})
    result = ApplyResult()

    for field_name, new_value in corrections.items():
        old_value = stored_fields.get(field_name)
        if old_value == new_value:
            result.skipped += 1
            continue

        db.session.add(
            OCRCorrection(
                ocr_document_id=ocr_doc.id,
                user_id=user_id,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
            )
        )
        stored_fields[field_name] = new_value
        result.applied.append({"field_name": field_name, "old": old_value, "new": new_value})

        # Conflict rule: manual value disagrees with a lab-report observation
        # for the same field → open an unresolved ConflictLog entry.
        lab_value = _lab_report_value(ocr_doc.id, field_name)
        if lab_value is not None and lab_value != "" and lab_value != new_value:
            _record_conflict(
                ocr_doc,
                field_name=field_name,
                values=[{"source": "manual", "value": new_value}, {"source": "lab_report", "value": lab_value}],
            )
            result.conflicts_opened += 1

    payload["reviewed_at"] = datetime.now(UTC).isoformat()
    ocr_doc.extracted_json = json.dumps(payload)
    db.session.commit()
    logger.info(
        "apply_field_corrections: doc=%s applied=%d skipped=%d conflicts=%d",
        doc_id,
        result.applied_count,
        result.skipped,
        result.conflicts_opened,
    )
    return result


def correct_lab_parameter(param_id: int, new_value: str, user_id: int | None = None):
    """Correct one lab-test parameter's observed value (logs + flags manual)."""
    from app.extensions import db
    from app.models import LabTestParameter, OCRCorrection

    param = db.session.get(LabTestParameter, param_id)
    if param is None:
        raise LookupError(f"LabTestParameter {param_id} not found")

    old_value = param.observed_value
    if old_value == new_value:
        return param

    db.session.add(
        OCRCorrection(
            ocr_document_id=param.ocr_document_id,
            user_id=user_id,
            field_name=f"lab:{param.parameter_name}",
            old_value=old_value,
            new_value=new_value,
        )
    )
    param.observed_value = new_value
    param.source_authority = "manual"
    db.session.commit()
    return param


def record_conflict(doc_id: str, field_name: str, values: list[dict], sample_id: int | None = None):
    """Insert an unresolved ConflictLog entry (values = [{source, value}, ...])."""
    from app.extensions import db
    from app.models import OCRDocument

    ocr_doc = db.session.get(OCRDocument, doc_id)
    if ocr_doc is None:
        raise LookupError(f"OCRDocument {doc_id} not found")
    return _record_conflict(ocr_doc, field_name=field_name, values=values, sample_id=sample_id)


def resolve_conflict(conflict_id: int, resolved_value: str, user_id: int | None = None) -> ApplyResult:
    """Resolve a conflict: mark it resolved AND apply the chosen value as a
    field correction so autopopulation sees the authoritative value."""
    from app.extensions import db
    from app.models import ConflictLog

    conflict = db.session.get(ConflictLog, conflict_id)
    if conflict is None:
        raise LookupError(f"ConflictLog {conflict_id} not found")

    conflict.resolved = True
    conflict.resolved_value = resolved_value
    conflict.resolved_by = user_id
    conflict.resolved_at = datetime.now(UTC)
    db.session.commit()

    return apply_field_corrections(conflict.ocr_document_id, {conflict.field_name: resolved_value}, user_id=user_id)


def open_conflicts() -> list:
    """All unresolved conflicts, oldest first (queue order)."""
    from app.models import ConflictLog

    return (
        _db()
        .session.query(ConflictLog)
        .filter(ConflictLog.resolved.is_(False))
        .order_by(ConflictLog.created_at.asc())
        .all()
    )


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _record_conflict(ocr_doc, *, field_name: str, values: list[dict], sample_id: int | None = None):
    from app.extensions import db
    from app.models import ConflictLog

    entry = ConflictLog(
        ocr_document_id=ocr_doc.id,
        sample_id=sample_id or ocr_doc.sample_id,
        field_name=field_name,
        values_json=json.dumps(values),
    )
    db.session.add(entry)
    db.session.commit()
    return entry


def _lab_report_value(doc_id: str, field_name: str) -> str | None:
    from sqlalchemy import func

    from app.models import LabTestParameter

    row = (
        _db()
        .session.query(LabTestParameter)
        .filter(
            LabTestParameter.ocr_document_id == doc_id,
            func.lower(LabTestParameter.parameter_name) == field_name.lower(),
        )
        .first()
    )
    return row.observed_value if row else None
