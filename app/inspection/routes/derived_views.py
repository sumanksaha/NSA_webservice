"""Derived-state views: open issues, pending action, history, and dismissal."""

from datetime import UTC, date, datetime

from flask import jsonify, redirect, render_template, request, url_for

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.routes.inspection_routes import _apply_inspection_sorting
from app.models import Adjudication, Inspection
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
    from app.models import Evidence

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
    return render_template("inspection/detail.html", inspection=inspection, photos=photos, fso_names=fso_names)
