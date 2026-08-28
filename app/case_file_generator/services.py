"""Case-level backup / export / import services (Phase 16).

Builds on the Phase 3 full-DB backup infrastructure (``app.utils.backup``)
by adding per-case JSON and ZIP export, plus case-cloning import.

All three functions accept ``case_type`` — either ``"case_file"`` or
``"adjudication"`` — and dispatch to the appropriate model, serializer, and
form processor from the respective route modules.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from datetime import UTC, datetime
from typing import Any

from flask import current_app

from app.extensions import db
from app.models import Adjudication, Annexure, CaseFile, Evidence, Version

logger = logging.getLogger(__name__)

_CASE_TYPE_MODELS = {
    "case_file": CaseFile,
    "adjudication": Adjudication,
}


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.isoformat()
    return value.replace(tzinfo=UTC).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _relpath(path: str | None) -> str | None:
    """Convert an absolute filepath to a path relative to instance_path."""
    if not path:
        return None
    try:
        return os.path.relpath(path, current_app.instance_path)
    except (TypeError, ValueError):
        return path


def _abspath(rel_path: str | None) -> str | None:
    """Convert a relative filepath (from export) back to absolute on disk."""
    if not rel_path:
        return rel_path
    if os.path.isabs(rel_path):
        return rel_path
    try:
        return os.path.join(current_app.instance_path, rel_path)
    except Exception:
        return rel_path


def _get_case(case_id: int, case_type: str):
    model = _CASE_TYPE_MODELS.get(case_type)
    if model is None:
        raise ValueError(f"Unknown case_type: {case_type!r}")
    return db.session.get(model, case_id)


def _fk_field(case_type: str) -> str:
    """Return the FK column name used by Annexure/Evidence/Version."""
    return "case_id" if case_type == "case_file" else "adjudication_id"


def _fk_kwargs(case_type: str, new_id: int) -> dict:
    """Return the FK kwargs for creating a new related record."""
    if case_type == "case_file":
        return {"case_id": new_id, "adjudication_id": None}
    return {"case_id": None, "adjudication_id": new_id}


def _serialize_annexure(a: Annexure) -> dict:
    return {
        "id": a.id,
        "case_id": a.case_id,
        "adjudication_id": a.adjudication_id,
        "caption": a.caption,
        "date": _serialize_dt(a.date),
        "file_hash": a.file_hash,
        "page_count": a.page_count,
        "ocr_text": a.ocr_text,
        "tags": a.tags,
        "filepath": _relpath(a.filepath),
        "filename": a.filename,
        "file_size": a.file_size,
        "mime_type": a.mime_type,
        "annexure_letter": a.annexure_letter,
        "uploaded_at": _serialize_dt(a.uploaded_at),
    }


def _serialize_evidence(e: Evidence) -> dict:
    return {
        "id": e.id,
        "case_id": e.case_id,
        "adjudication_id": e.adjudication_id,
        "inspection_id": e.inspection_id,
        "evidence_type": e.evidence_type,
        "filepath": _relpath(e.filepath),
        "filename": e.filename,
        "file_size": e.file_size,
        "mime_type": e.mime_type,
        "file_hash": e.file_hash,
        "raw_lat": e.raw_lat,
        "raw_lng": e.raw_lng,
        "accuracy": e.accuracy,
        "captured_at": _serialize_dt(e.captured_at),
        "locality": e.locality,
        "ip_region": e.ip_region,
        "ip_match": e.ip_match,
        "distance_to_fbo_m": e.distance_to_fbo_m,
        "verification_status": e.verification_status,
        "stamped": e.stamped,
        "caption": e.caption,
        "ocr_text": e.ocr_text,
        "tags": e.tags,
        "uploaded_at": _serialize_dt(e.uploaded_at),
    }


def _serialize_version(v: Version) -> dict:
    return {
        "id": v.id,
        "case_id": v.case_id,
        "adjudication_id": v.adjudication_id,
        "doc_type": v.doc_type,
        "version_number": v.version_number,
        "content_hash": v.content_hash,
        "html_snapshot": v.html_snapshot,
        "delta": v.delta,
        "created_at": _serialize_dt(v.created_at),
        "user_id": v.user_id,
        "change_summary": v.change_summary,
        "branch_name": v.branch_name,
        "branch_of": v.branch_of,
    }


def _case_to_dict(case, case_type: str) -> dict:
    """Reuse existing serializers from the route modules."""
    if case_type == "case_file":
        from app.case_file_generator.routes import case_file_to_dict

        return case_file_to_dict(case)
    from app.adjudication.routes import adjudication_to_dict

    return adjudication_to_dict(case)


def _get_manager(case_type: str):
    """Return the DocumentCaseManager instance for *case_type*."""
    if case_type == "case_file":
        from app.case_file_generator.routes import _manager

        return _manager
    from app.adjudication.routes import _manager

    return _manager


def _validate_form(case_dict: dict, case_type: str) -> None:
    """Validate the form dict using existing validators. Raises ValueError on failure."""
    if case_type == "case_file":
        from app.case_file_generator.routes import validate_case_file_form

        # Exported dates are ISO datetime strings; the validator expects YYYY-MM-DD.
        normalized = _normalize_dates_for_form(case_dict, case_type)
        errors = validate_case_file_form(normalized)
        if errors:
            raise ValueError(f"Validation errors: {errors}")
    else:
        required = ["case_number", "food_safety_officer_name"]
        missing = [k for k in required if not case_dict.get(k)]
        if missing:
            raise ValueError(f"Adjudication missing required fields: {missing}")


def _normalize_dates_for_form(data: dict, case_type: str) -> dict:
    """Convert ISO datetime strings from export back to ``YYYY-MM-DD`` for form validators.

    The existing form validators (``validate_case_file_form``) and processors
    (``_process_case_file_form`` via ``parse_date``) expect dates in
    ``%Y-%m-%d`` format, but ``case_file_to_dict`` / ``adjudication_to_dict``
    produce full ISO datetime strings.  This helper truncates them.
    """
    if case_type == "case_file":
        date_fields = [
            "authorization_date",
            "inspection_date",
            "mfg_date",
            "expiry_date",
            "do_receipt_date",
            "analyst_report_date",
            "directive_letter_date",
            "retailer_report_receive_date",
            "manufacturer_report_receive_date",
        ]
    else:
        date_fields = [
            "first_inspection_date",
            "compliance_deadline",
            "complaint_date",
            "followup_inspection_date",
            "authorization_date",
        ]

    result = data.copy()
    for field in date_fields:
        if field in result and isinstance(result[field], str) and result[field]:
            try:
                dt = datetime.fromisoformat(result[field])
                result[field] = dt.strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                pass
    return result


def _process_form(case_dict: dict, case_type: str):
    """Create a model instance from a form dict using existing processors."""
    if case_type == "case_file":
        from app.case_file_generator.routes import _process_case_file_form

        return _process_case_file_form(case_dict)
    from app.adjudication.routes import _process_adjudication_form

    return _process_adjudication_form(case_dict)


def _try_generate_pdfs(case_id: int, case_type: str) -> bytes | None:
    """Attempt to produce compiled PDF bytes for the case.

    Uses the ``DocumentCaseManager.regenerate()`` method which renders
    templates and returns a Flask ``send_file`` response (ZIP in memory)
    containing the compiled PDFs.  Returns ``None`` on any failure
    (caller should handle gracefully).
    """
    try:
        manager = _get_manager(case_type)
        result = manager.regenerate(case_id)
        # ``regenerate`` returns either a Flask send_file response (success)
        # or a (jsonify, status) tuple (error).
        if isinstance(result, tuple):
            # Error case — don't raise, just log.
            logger.warning(
                "PDF regeneration for %s %s returned error status %s",
                case_type,
                case_id,
                result[1] if len(result) > 1 else "unknown",
            )
            return None
        if hasattr(result, "get_data"):
            return result.get_data()
        if isinstance(result, (bytes, bytearray)):
            return bytes(result)
    except Exception as exc:
        logger.warning("PDF generation failed for %s %s: %s", case_type, case_id, exc)
    return None


# --------------------------------------------------------------------------- #
#  Export — JSON (metadata + annexures + evidence + versions)
# --------------------------------------------------------------------------- #


def export_case_as_json(case_id: int, case_type: str) -> dict[str, Any]:
    """Build a full JSON-serialisable dict for *case_id* of *case_type*.

    Returns a dict with keys: ``case_type``, ``case``, ``annexures``,
    ``evidence``, ``versions``.

    Reuses the existing ``case_file_to_dict`` / ``adjudication_to_dict``
    serializers to keep field-name mapping consistent with the editor and API.
    """
    case = _get_case(case_id, case_type)
    if case is None:
        raise ValueError(f"{case_type} with id {case_id} not found")

    case_data = _case_to_dict(case, case_type)
    fk = _fk_field(case_type)

    annexures = [_serialize_annexure(a) for a in Annexure.query.filter(getattr(Annexure, fk) == case_id).all()]
    evidence = [_serialize_evidence(e) for e in Evidence.query.filter(getattr(Evidence, fk) == case_id).all()]
    versions = [_serialize_version(v) for v in Version.query.filter(getattr(Version, fk) == case_id).all()]

    return {
        "case_type": case_type,
        "exported_at": _serialize_dt(datetime.now(UTC)),
        "case": case_data,
        "annexures": annexures,
        "evidence": evidence,
        "versions": versions,
    }


# --------------------------------------------------------------------------- #
#  Export — ZIP (JSON + compiled PDFs + annexure/evidence files)
# --------------------------------------------------------------------------- #


def export_case_as_zip(case_id: int, case_type: str) -> bytes:
    """Build an in-memory ZIP for *case_id* / *case_type*.

    Contains:
    - ``case_export.json`` — full metadata (see :func:`export_case_as_json`).
    - ``compiled_pdfs.zip`` — Petition + Permission Letter PDFs (if generation succeeds).
    - ``annexures/<filename>`` — raw uploaded annexure files.
    - ``evidence/<filename>`` — raw uploaded evidence files.

    PDF generation or file-bundling failures are non-fatal: a ``warnings``
    list is appended to the JSON metadata instead of crashing the export.
    """
    data = export_case_as_json(case_id, case_type)
    warnings: list[str] = []

    # --- Compiled PDFs via DocumentCaseManager ---
    pdf_bytes = _try_generate_pdfs(case_id, case_type)
    if pdf_bytes:
        # The manager returns a ZIP of PDFs; embed it inside our export ZIP.
        pass
    else:
        warnings.append("Compiled PDFs could not be generated for this case.")

    fk = _fk_field(case_type)

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        # JSON manifest (with warnings baked in).
        data["warnings"] = warnings
        zf.writestr("case_export.json", json.dumps(data, default=str, indent=2).encode("utf-8"))

        # Compiled PDFs (if available).
        if pdf_bytes:
            zf.writestr("compiled_pdfs.zip", pdf_bytes)

        # Annexure files (resolve relative paths back to absolute).
        for a in Annexure.query.filter(getattr(Annexure, fk) == case_id).all():
            if a.filepath and os.path.isfile(a.filepath):
                arcname = f"annexures/{a.id}_{a.filename}"
                try:
                    zf.write(a.filepath, arcname)
                except OSError as exc:
                    warnings.append(f"Could not bundle annexure {a.id}: {exc}")

        # Evidence files.
        for e in Evidence.query.filter(getattr(Evidence, fk) == case_id).all():
            if e.filepath and os.path.isfile(e.filepath):
                arcname = f"evidence/{e.id}_{e.filename}"
                try:
                    zf.write(e.filepath, arcname)
                except OSError as exc:
                    warnings.append(f"Could not bundle evidence {e.id}: {exc}")

    output.seek(0)
    return output.getvalue()


# --------------------------------------------------------------------------- #
#  Import — clone an exported case JSON into a new case record
# --------------------------------------------------------------------------- #


def import_case_from_json(json_data: dict) -> int:
    """Create a new case (and related annexure/evidence/version clones) from *json_data*.

    The JSON must be the output of :func:`export_case_as_json`.

    Returns the new case ``id``.

    Related records that reference the original case_id/adjudication_id
    (Annexure, Evidence, Version) are cloned with new UUIDs / autoincrement
    PKs, pointing to the new case.  Raw files are NOT duplicated — only
    DB records are created; file paths are preserved as-is.
    """
    case_type = json_data.get("case_type")
    if case_type not in _CASE_TYPE_MODELS:
        raise ValueError(f"Unknown case_type: {case_type!r}")

    case_dict = json_data.get("case")
    if not isinstance(case_dict, dict):
        raise ValueError("JSON must contain a 'case' dict")

    # Validate form data using the existing validators.
    _validate_form(case_dict, case_type)

    # Normalize date formats for the form processors (they expect YYYY-MM-DD).
    form_data = _normalize_dates_for_form(case_dict, case_type)

    # Create the case record via the existing form processor.
    new_case = _process_form(form_data, case_type)
    db.session.add(new_case)
    db.session.flush()  # assigns new_case.id

    new_id = new_case.id
    fk_kwargs = _fk_kwargs(case_type, new_id)

    # Clone annexures.
    for a_dict in json_data.get("annexures", []):
        new_annexure = Annexure(
            **fk_kwargs,
            caption=a_dict.get("caption", ""),
            date=_parse_dt(a_dict.get("date")),
            file_hash=a_dict.get("file_hash", ""),
            page_count=a_dict.get("page_count"),
            ocr_text=a_dict.get("ocr_text"),
            tags=a_dict.get("tags"),
            filepath=_abspath(a_dict.get("filepath", "")),
            filename=a_dict.get("filename", ""),
            file_size=a_dict.get("file_size"),
            mime_type=a_dict.get("mime_type"),
            annexure_letter=a_dict.get("annexure_letter"),
            uploaded_at=_parse_dt(a_dict.get("uploaded_at")),
        )
        db.session.add(new_annexure)

    # Clone evidence.
    for e_dict in json_data.get("evidence", []):
        new_evidence = Evidence(
            **fk_kwargs,
            inspection_id=e_dict.get("inspection_id"),
            evidence_type=e_dict.get("evidence_type", "photo"),
            filepath=_abspath(e_dict.get("filepath", "")),
            filename=e_dict.get("filename", ""),
            file_size=e_dict.get("file_size"),
            mime_type=e_dict.get("mime_type"),
            file_hash=e_dict.get("file_hash"),
            raw_lat=e_dict.get("raw_lat"),
            raw_lng=e_dict.get("raw_lng"),
            accuracy=e_dict.get("accuracy"),
            captured_at=_parse_dt(e_dict.get("captured_at")),
            locality=e_dict.get("locality"),
            ip_region=e_dict.get("ip_region"),
            ip_match=e_dict.get("ip_match"),
            distance_to_fbo_m=e_dict.get("distance_to_fbo_m"),
            verification_status=e_dict.get("verification_status", "PENDING"),
            stamped=e_dict.get("stamped", False),
            caption=e_dict.get("caption"),
            ocr_text=e_dict.get("ocr_text"),
            tags=e_dict.get("tags"),
            uploaded_at=_parse_dt(e_dict.get("uploaded_at")),
        )
        db.session.add(new_evidence)

    # Clone versions (HTML/delta snapshots, not files).
    for v_dict in json_data.get("versions", []):
        new_version = Version(
            **fk_kwargs,
            doc_type=v_dict.get("doc_type", "petition"),
            version_number=v_dict.get("version_number", 1),
            content_hash=v_dict.get("content_hash", ""),
            html_snapshot=v_dict.get("html_snapshot", ""),
            delta=v_dict.get("delta"),
            created_at=_parse_dt(v_dict.get("created_at")),
            user_id=v_dict.get("user_id"),
            change_summary=v_dict.get("change_summary"),
            branch_name=v_dict.get("branch_name"),
            branch_of=v_dict.get("branch_of"),
        )
        db.session.add(new_version)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    logger.info("Imported case JSON for %s -> new id %s", case_type, new_id)
    return new_id
