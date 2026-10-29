"""Derived-state views: open issues, pending action, history, and dismissal."""

from datetime import UTC, date, datetime

from flask import jsonify, redirect, render_template, request, url_for

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.routes.inspection_routes import _apply_inspection_sorting
from app.models import Adjudication, Inspection, Sample
from app.utils.fso_data import get_all_fso_names


@inspection_bp.route("/open")
def open_issues():
    """Open Issues view: every inspection that is neither corrective-implemented
    (internal flag: is_dismissed) nor adjudication-linked — no deadline filter.
    Stays listed until the FSO asserts corrective measures or an adjudication closes it.
    """
    from app.models import FSO

    sort_by = request.args.get("sort_by", "compliance_deadline")
    sort_order = request.args.get("sort_order", "asc")
    filter_fso = request.args.get("fso_name")

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        ~Inspection.is_dismissed,
        Inspection.adjudication_id.is_(None),
    )

    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)

    inspections = _apply_inspection_sorting(query, sort_by, sort_order).all()

    return render_template(
        "inspection/open_issues.html",
        inspections=inspections,
        fso_names=get_all_fso_names(),
        sort_by=sort_by,
        sort_order=sort_order,
        filter_fso=filter_fso,
        view_type="open",
    )


@inspection_bp.route("/pending")
def pending_action():
    """Pending Action view: inspections where compliance_deadline < today AND is_dismissed = false AND adjudication_id IS NULL."""
    from app.models import FSO

    today = date.today().isoformat()
    sort_by = request.args.get("sort_by", "compliance_deadline")
    sort_order = request.args.get("sort_order", "asc")
    filter_fso = request.args.get("fso_name")

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        Inspection.compliance_deadline < today,
        ~Inspection.is_dismissed,
        Inspection.adjudication_id.is_(None),
    )

    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)

    inspections = _apply_inspection_sorting(query, sort_by, sort_order).all()

    today_date = datetime.now(UTC)
    for inspection in inspections:
        if inspection.compliance_deadline:
            inspection.days_overdue = (today_date - inspection.compliance_deadline).days
        else:
            inspection.days_overdue = 0

    return render_template(
        "inspection/pending_action.html",
        inspections=inspections,
        fso_names=get_all_fso_names(),
        sort_by=sort_by,
        sort_order=sort_order,
        filter_fso=filter_fso,
        view_type="pending",
    )


@inspection_bp.route("/history")
def history():
    """History view: inspections that are dismissed or have adjudication_id set."""
    from app.models import FSO

    sort_by = request.args.get("sort_by", "dismissed_at")
    sort_order = request.args.get("sort_order", "desc")
    filter_fso = request.args.get("fso_name")
    filter_type = request.args.get("type", "all")

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        (Inspection.is_dismissed) | (Inspection.adjudication_id.isnot(None)),
    )

    if filter_type == "dismissed":
        query = query.filter(Inspection.is_dismissed)
    elif filter_type == "adjudicated":
        query = query.filter(Inspection.adjudication_id.isnot(None))

    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)

    inspections = _apply_inspection_sorting(query, sort_by, sort_order).all()

    return render_template(
        "inspection/history.html",
        inspections=inspections,
        fso_names=get_all_fso_names(),
        sort_by=sort_by,
        sort_order=sort_order,
        filter_fso=filter_fso,
        filter_type=filter_type,
        view_type="history",
    )


@inspection_bp.route("/<int:inspection_id>/implement_corrective_measures", methods=["POST"])
def implement_corrective_measures(inspection_id):
    """FSO asserts corrective measures implemented (replaces dismissal).

    No deadline precondition — any inspection can be closed out at any time;
    audited with who/when. Internal storage reuses the legacy dismissed_*
    columns to avoid a Postgres column rename migration.
    """
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    if inspection.is_dismissed:
        return jsonify({"error": "Corrective measures already implemented for this inspection"}), 400

    if inspection.adjudication_id:
        return jsonify({"error": "Inspection already linked to adjudication"}), 400

    implemented_by = request.form.get("implemented_by", inspection.fso_name)

    inspection.is_dismissed = True
    inspection.dismissed_by = implemented_by
    inspection.dismissed_at = datetime.now(UTC)

    try:
        db.session.commit()
        return (
            jsonify({
                "message": "Corrective measures implemented successfully",
                "inspection_id": inspection.id,
                "inspection_code": inspection.inspection_code,
            }),
            200,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to record corrective measures: {e!s}"}), 500


@inspection_bp.route("/<int:inspection_id>/create_adjudication", methods=["GET"])
def create_adjudication_from_inspection(inspection_id):
    """Redirect to Adjudication form with prefill data from inspection."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    today = date.today().isoformat()
    if inspection.compliance_deadline >= today:
        return jsonify({"error": "Only Pending Action inspections (past deadline) can create adjudication"}), 400

    if inspection.is_dismissed:
        return jsonify({"error": "Dismissed inspections cannot create adjudication"}), 400

    if inspection.adjudication_id:
        return jsonify({"error": "Inspection already linked to adjudication"}), 400

    prefill = {
        "from_inspection": inspection_id,
        "food_safety_officer_name": inspection.fso_name,
        "fbo_name": inspection.fbo_name or "",
        "fbo_address": inspection.fbo_address or "",
        "fssai_license": inspection.fssai_license or "",
        "ce_license_no": inspection.ce_license_no or "",
        "first_inspection_date": inspection.inspection_date,
        "compliance_deadline": inspection.compliance_deadline,
        "concerned_food": inspection.concerned_food or "",
        "problem": inspection.problem or "",
        "notes": inspection.notes or "",
        "is_reinspection": inspection.is_reinspection,
        "days_allowed_for_errors": inspection.days_allowed_for_errors,
        "errors_in_second_inspection": inspection.errors_in_second_inspection,
    }

    return redirect(url_for("adjudication.index", **prefill))


@inspection_bp.route("/<int:inspection_id>/link_adjudication/<int:adjudication_id>", methods=["POST"])
def link_adjudication(inspection_id, adjudication_id):
    """Link an inspection to an adjudication after successful save."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    adjudication = db.session.get(Adjudication, adjudication_id)
    if not adjudication:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    if inspection.is_dismissed:
        return jsonify({"error": "Dismissed inspections cannot be linked to adjudication"}), 400

    if inspection.adjudication_id:
        return jsonify({"error": "Inspection already linked to adjudication"}), 400

    inspection.adjudication_id = adjudication_id

    try:
        db.session.commit()
        return (
            jsonify({
                "message": "Inspection linked to adjudication successfully",
                "inspection_id": inspection.id,
                "adjudication_id": adjudication.id,
            }),
            200,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to link inspection to adjudication: {e!s}"}), 500


@inspection_bp.route("/<int:inspection_id>/detail")
def inspection_detail(inspection_id):
    """Render the inspection detail page with photo upload UI."""
    import json

    from app.models import Evidence, EvidenceAssessment

    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    photos = (
        Evidence.query
        .filter_by(inspection_id=inspection_id, evidence_type="photo")
        .order_by(Evidence.uploaded_at.desc())
        .all()
    )
    fso_names = get_all_fso_names()

    # Parse checklist JSON for template rendering
    checklist = None
    if inspection.checklist_json:
        try:
            checklist = json.loads(inspection.checklist_json)
        except (ValueError, TypeError):
            checklist = None

    # Load the latest assessment per photo (append-only history -> newest first)
    assessments = {}
    if photos:
        photo_ids = [p.id for p in photos]
        rows = (
            EvidenceAssessment.query
            .filter(EvidenceAssessment.evidence_id.in_(photo_ids))
            .order_by(
                EvidenceAssessment.evidence_id, EvidenceAssessment.created_at.desc(), EvidenceAssessment.id.desc()
            )
            .all()
        )
        seen = set()
        for row in rows:
            if row.evidence_id in seen:
                continue
            seen.add(row.evidence_id)
            try:
                payload = json.loads(row.payload) if row.payload else {}
            except (ValueError, TypeError):
                payload = {}
            try:
                suggested = json.loads(row.suggested_fields) if row.suggested_fields else []
            except (ValueError, TypeError):
                suggested = []
            assessments[row.evidence_id] = {
                "id": row.id,
                "gate_passed": bool(payload.get("gate_passed")),
                "overall_observable": bool(row.overall_observable),
                "suggested_fields": suggested,
                "items": [
                    {
                        "field": it.get("field"),
                        "observable": bool(it.get("observable")),
                        "p_violation": it.get("p_violation"),
                    }
                    for it in (payload.get("items") or [])
                ],
                "error": row.error,
                "provider_model": row.provider_model,
                "latency_ms": row.latency_ms,
            }

    from app.shared.config import cfg

    # Linked sample (if any) for this inspection
    linked_sample = inspection.sample  # via relationship on Inspection (backref from Sample.inspection_id)

    # Samples for the same FBO (for the sample-adjudication picker)
    fbo_samples = []
    if inspection.fbo_name:
        fbo_samples = Sample.query.filter_by(fso_name=inspection.fso_name).order_by(Sample.collection_date.desc()).all()

    return render_template(
        "inspection/detail.html",
        inspection=inspection,
        photos=photos,
        fso_names=fso_names,
        checklist=checklist,
        assessments=assessments,
        photo_scoring_enabled=bool(cfg.photo_scoring_enabled),
        photo_scoring_threshold=float(cfg.photo_scoring_threshold),
        sample=linked_sample,
        fbo_samples=fbo_samples,
    )
