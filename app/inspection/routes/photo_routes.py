"""Photo evidence routes — thin HTTP adapters for the inspection photo module.

All business logic lives in ``InspectionPhotoService``
(``app/inspection/photo_service.py``): validation, EXIF + coordinates,
Evidence persistence, stamping, and OCR dispatch. These handlers parse
the request, delegate to the module, and return JSON.
"""

import json

from flask import jsonify, request

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.photo_scoring import (
    PROMPT_VERSION,
    PhotoEvidenceScorer,
    form_value_for_violation,
)
from app.inspection.photo_service import InspectionPhotoService
from app.shared.config import cfg

_photo_service = InspectionPhotoService()


@inspection_bp.route("/<int:inspection_id>/photo-evidence", methods=["GET"])
def get_inspection_photo_evidence(inspection_id):
    """Get all photo evidence records for an inspection (JSON)."""
    try:
        photos = _photo_service.list_for_inspection(inspection_id)
    except FileNotFoundError:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    return jsonify([
        {
            "image_id": p.id,
            "filepath": p.file_url,
            "raw_lat": p.raw_lat,
            "raw_lng": p.raw_lng,
            "verification_status": p.verification_status,
            "uploaded_at": p.uploaded_at,
        }
        for p in photos
    ])


@inspection_bp.route("/photo-upload", methods=["POST"])
def upload_photo_evidence():
    """Upload photo evidence for an inspection."""
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    inspection_id_str = request.form.get("inspection_id")
    case_id = request.form.get("case_id")

    if not inspection_id_str and not case_id:
        return jsonify({"error": "Either inspection_id or case_id is required"}), 400

    try:
        if inspection_id_str:
            inspection_id = int(inspection_id_str)
        else:
            from app.models import Inspection

            inspection = db.session.get(Inspection, case_id)
            if not inspection:
                return jsonify({"error": "Inspection not found"}), 404
            inspection_id = inspection.id
    except (ValueError, TypeError):
        return jsonify({"error": "inspection_id must be an integer"}), 400

    required_fields = ["lat", "lng", "accuracy", "captured_at"]
    for field in required_fields:
        if field not in request.form:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    file.seek(0)

    try:
        result = _photo_service.upload_evidence(
            inspection_id,
            file,
        )
    except FileNotFoundError:
        return jsonify({"error": "Inspection not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500

    return (
        jsonify({
            "image_id": result.photo_id,
            "verification_status": result.verification.get("verification_status"),
            "ocr_task_id": result.ocr_task_id,
            "ocr_result": result.ocr_result,
            "scoring": _result_to_dict(result.scoring, float(cfg.photo_scoring_threshold)) if result.scoring is not None else None,
        }),
        201,
    )


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["POST"])
def upload_adjudication_photo(adjudication_id):
    """Upload a photo for an adjudication.

    Persists to R2/B2 (Cloudinary when configured), then — when
    ``PHOTO_SCORING_ENABLED`` — scores the stored image and writes the photo and
    its assessment in one transaction. See ``assess_photo_evidence`` for scoring
    an already-stored photo.
    """
    if "photo" not in request.files:
        return jsonify({"error": 'No photo file provided. Use field name "photo".'}), 400

    file = request.files["photo"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    try:
        result = _photo_service.upload_adjudication_photo(
            adjudication_id, file, caption=request.form.get("caption", ""),
        )
    except FileNotFoundError:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        msg = str(exc)
        if "Storage" in msg:
            return jsonify({"error": "Storage service unavailable."}), 502
        return jsonify({"error": "Database error."}), 500

    from app.shared.config import cfg

    body = {"id": result.photo_id, "file_url": result.filepath, "uploaded_at": None}
    if result.scoring is not None:
        body["scoring"] = _result_to_dict(result.scoring, float(cfg.photo_scoring_threshold))
    return (jsonify(body), 201)


@inspection_bp.route("/photos/<photo_id>", methods=["DELETE"])
def delete_adjudication_photo(photo_id):
    """Delete a photo from storage and remove its DB record."""
    try:
        _photo_service.delete(photo_id)
    except FileNotFoundError:
        return jsonify({"error": f"Photo with id {photo_id} not found"}), 404
    except RuntimeError:
        return jsonify({"error": "Database error."}), 500

    return "", 204


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["GET"])
def list_adjudication_photos(adjudication_id):
    """List all photos for an adjudication, ordered by upload time. Supports pagination."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    try:
        paginated = _photo_service.list_adjudication(adjudication_id, page=page, per_page=per_page)
    except FileNotFoundError:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    return jsonify(paginated)


# --------------------------------------------------------------------------- #
# Probabilistic violation assessment (ADR-0011)
#
# --------------------------------------------------------------------------- #
# Probabilistic violation assessment (ADR-0011)
#
# Two entry points, because scoring is wanted both at capture and later:
#
#   * at capture   — ``POST /<adjudication_id>/photos`` scores between the storage
#     upload and the Evidence insert (see InspectionPhotoService), so a fresh
#     photo is never stored without its assessment.
#   * later        — ``POST /photos/<evidence_id>/assess`` re-scores a photo that
#     is already stored: the evidence file is read back from Cloudinary/R2 by URL
#     and scored in a new append-only row. Re-running after a prompt or model
#     change adds evidence rather than erasing it.
#
# ADVISORY ONLY. Nothing on these paths writes a checklist value — the scores are
# recorded and returned so the FSO can accept or override them on the
# adjudication form. A violation is never created here.
# --------------------------------------------------------------------------- #
def _assessment_to_dict(row) -> dict:
    return {
        "id": row.id,
        "evidence_id": row.evidence_id,
        "adjudication_id": row.adjudication_id,
        "overall_observable": row.overall_observable,
        "payload": json.loads(row.payload) if row.payload else None,
        "suggested_fields": json.loads(row.suggested_fields) if row.suggested_fields else [],
        "provider_model": row.provider_model,
        "resolved_model": row.resolved_model,
        "prompt_version": row.prompt_version,
        "token_usage": row.token_usage,
        "latency_ms": row.latency_ms,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _result_to_dict(result, threshold: float) -> dict:
    """Serialise a :class:`ScoringResult`, including the polarity-correct hint."""
    return {
        "ok": result.ok,
        "gate_passed": result.gate_passed,
        "overall_observable": result.overall_observable,
        "suggested_fields": result.suggested_fields(threshold),
        "items": [
            {
                "field": i.field,
                "observable": i.observable,
                "p_violation": i.p_violation,
                # Advisory hint for the FSO's checklist; the inverted pair
                # (artificial_colour / Expired_item) reads "yes" here.
                "suggested_form_value": (
                    form_value_for_violation(i.field, violation_present=True)
                    if i.observable and i.p_violation is not None and i.p_violation >= threshold
                    else None
                ),
            }
            for i in result.items
        ],
        "provider_model": result.provider_model,
        "resolved_model": result.resolved_model,
        "token_usage": result.token_usage,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }


@inspection_bp.route("/photos/<evidence_id>/assess", methods=["POST"])
def assess_photo_evidence(evidence_id):
    """Re-score a stored photo by reading it back from Cloudinary/R2.

    Appends an ``EvidenceAssessment`` row and returns the per-field
    probabilities. Advisory: it never sets a checklist value.

    A gated-off image (not a premises photograph) is a *successful* run that
    scores nothing. A provider failure is recorded and reported as ``502`` —
    never as a clean premises.
    """
    from app.models import Evidence
    from app.services.audit_context import audit_logger
    from app.shared.config import cfg

    if not bool(cfg.photo_scoring_enabled):
        return jsonify({"error": "Photo scoring is disabled (PHOTO_SCORING_ENABLED=false)."}), 503

    evidence = db.session.get(Evidence, evidence_id)
    if evidence is None:
        return jsonify({"error": f"Evidence with id {evidence_id} not found"}), 404
    if evidence.evidence_type != "photo":
        return jsonify({"error": "Only photo evidence can be assessed."}), 400

    threshold = float(cfg.photo_scoring_threshold)
    scorer = PhotoEvidenceScorer()
    try:
        assessment, result = scorer.assess(evidence)
    except ValueError as exc:
        # Caller error, or scoring switched off mid-request.
        return jsonify({"error": str(exc)}), 400

    audit_logger("photo").log(
        evidence_id,
        "ASSESSMENT_SCORED",
        ok=result.ok,
        gate_passed=result.gate_passed,
        provider_model=result.provider_model,
        resolved_model=result.resolved_model,
        prompt_version=PROMPT_VERSION,
    )

    body = _result_to_dict(result, threshold)
    body["assessment"] = _assessment_to_dict(assessment)
    return jsonify(body), (200 if result.ok else 502)


@inspection_bp.route("/<int:adjudication_id>/photos/assess", methods=["POST"])
def assess_adjudication_photos(adjudication_id):
    """Re-score every stored photo for an adjudication, newest run per photo.

    The batch counterpart to the per-photo endpoint, for backfilling a case whose
    photos were captured before scoring existed. Each photo gets its own
    append-only row; a photo that fails does not stop the rest.

    Returns a per-photo summary; ``200`` even when some photos failed, since the
    failures are reported in ``items``.
    """
    from app.models import Adjudication, Evidence
    from app.services.audit_context import audit_logger
    from app.shared.config import cfg

    if not bool(cfg.photo_scoring_enabled):
        return jsonify({"error": "Photo scoring is disabled (PHOTO_SCORING_ENABLED=false)."}), 503
    if db.session.get(Adjudication, adjudication_id) is None:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    threshold = float(cfg.photo_scoring_threshold)
    photos = (
        Evidence.query.filter_by(adjudication_id=adjudication_id, evidence_type="photo")
        .order_by(Evidence.uploaded_at.asc())
        .all()
    )
    scorer = PhotoEvidenceScorer()
    items = []
    for photo in photos:
        try:
            assessment, result = scorer.assess(photo)
            summary = _result_to_dict(result, threshold)
            items.append({
                "evidence_id": photo.id,
                "assessment_id": assessment.id,
                "ok": summary["ok"],
                "gate_passed": summary["gate_passed"],
                "suggested_fields": summary["suggested_fields"],
                "error": summary["error"],
            })
        except Exception as exc:  # one bad photo must not abort the batch
            items.append({"evidence_id": photo.id, "ok": False, "error": str(exc)})

    audit_logger("adjudication").log(
        str(adjudication_id),
        "ASSESSMENTS_SCORED_BULK",
        count=len(items),
        failed=sum(1 for i in items if not i.get("ok")),
    )
    return jsonify({"adjudication_id": adjudication_id, "count": len(items), "items": items})


@inspection_bp.route("/photos/<evidence_id>/assessments", methods=["GET"])
def list_photo_assessments(evidence_id):
    """Every assessment recorded for a photo, newest first (append-only history)."""
    from app.models import Evidence, EvidenceAssessment

    if db.session.get(Evidence, evidence_id) is None:
        return jsonify({"error": f"Evidence with id {evidence_id} not found"}), 404

    rows = (
        EvidenceAssessment.query.filter_by(evidence_id=evidence_id)
        .order_by(EvidenceAssessment.created_at.desc(), EvidenceAssessment.id.desc())
        .all()
    )
    return jsonify([_assessment_to_dict(r) for r in rows])


@inspection_bp.route("/<int:inspection_id>/checklist/apply-suggestion", methods=["POST"])
def apply_scoring_suggestion(inspection_id):
    """Apply photo-scoring suggestions to the inspection checklist.

    Body: JSON {"fields": ["clean_premise", "date_tag", ...]} — field names
    cleared by the model. Values are set to the correct form polarity:
    checklist fields -> "no" (violation present), special fields
    (artificial_colour, Expired_item) -> "yes".
    Advisory only — the FSO confirms; this just pre-fills the checklist.
    """
    from app.models import Inspection

    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    if inspection.notice_issued_at is not None:
        return jsonify({"error": "Inspection is frozen: an Improvement Notice has already been issued."}), 409

    body = request.get_json(silent=True) or {}
    fields = body.get("fields", [])
    if not isinstance(fields, list):
        return jsonify({"error": "Body must be {\"fields\": [\"field1\", \"field2\"]}"}), 400

    from app.shared.context_derivers import CHECKLIST_RULES, SPECIAL_VIOLATION_RULES

    checklist = {}
    if inspection.checklist_json:
        try:
            checklist = json.loads(inspection.checklist_json) or {}
        except (ValueError, TypeError):
            checklist = {}

    applied = []
    for field in fields:
        if field in SPECIAL_VIOLATION_RULES:
            checklist[field] = "yes"  # violation present = "yes" for special fields
            applied.append(field)
        elif field in CHECKLIST_RULES:
            checklist[field] = "no"   # violation present = "no" for checklist fields
            applied.append(field)
        # unknown fields are silently skipped

    inspection.checklist_json = json.dumps(checklist) if checklist else None

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": f"Failed to save checklist: {exc!s}"}), 500

    return jsonify({"message": "Checklist updated", "applied": applied, "checklist": checklist}), 200
