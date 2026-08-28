"""Core inspection CRUD routes: index, list, create, get, update, delete."""

import json
import re
from datetime import UTC, datetime

from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.inspection_utils import calculate_compliance_deadline, generate_inspection_code
from app.models import FSO, Inspection
from app.services.sync_orchestrator import sync_row
from app.shared.context_derivers import CHECKLIST_FIELDS
from app.utils.filters import parse_date
from app.utils.fso_data import get_all_fso_names


# Sample code format: SL/WB/XXXXXX/XXXX/XXXXX
SAMPLE_CODE_PATTERN = re.compile(r"^SL/WB/\d{6}/\d{4}/\d{5}$")


def validate_sample_code(code):
    """Return True when ``code`` matches the SL/WB/XXXXXX/XXXX/XXXXX pattern or is empty."""
    if not code:
        return True
    return bool(SAMPLE_CODE_PATTERN.match(code))


def _apply_inspection_sorting(query, sort_by, sort_order):
    """Apply common sorting to an Inspection query joined with FSO.

    Args:
        query: SQLAlchemy query with Inspection joined to FSO.
        sort_by: Column name to sort by.
        sort_order: 'asc' or 'desc'.

    Returns:
        Sorted query.
    """
    order_map = {
        "inspection_code": Inspection.inspection_code,
        "inspection_date": Inspection.inspection_date,
        "compliance_deadline": Inspection.compliance_deadline,
        "dismissed_at": Inspection.dismissed_at,
        "fso_name": FSO.fso_name,
    }
    col = order_map.get(sort_by)
    if col is None:
        col = Inspection.compliance_deadline
    if sort_order == "asc":
        return query.order_by(col.asc())
    return query.order_by(col.desc())


@inspection_bp.route("/")
def index():
    """Inspection entry form page."""
    fso_names = get_all_fso_names()
    return render_template("inspection/index.html", fso_names=fso_names)


@inspection_bp.route("/<int:inspection_id>/edit")
def edit_inspection(inspection_id):
    """Inspection edit form page."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    if inspection.notice_issued_at is not None:
        return jsonify({"error": "Inspection is frozen: an Improvement Notice has already been issued for it."}), 409

    fso_names = get_all_fso_names()

    # Parse checklist JSON for template rendering
    checklist = None
    if inspection.checklist_json:
        try:
            checklist = json.loads(inspection.checklist_json)
        except (ValueError, TypeError):
            checklist = None

    return render_template(
        "inspection/edit.html",
        inspection=inspection,
        fso_names=fso_names,
        checklist=checklist or {},
    )


@inspection_bp.route("/list")
def list_inspections():
    """List all inspections with pagination, sorting, and filtering."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    sort_by = request.args.get("sort_by", "inspection_date")
    sort_order = request.args.get("sort_order", "desc")
    filter_fso = request.args.get("fso_name")
    filter_date_from = request.args.get("inspection_date_from")
    filter_date_to = request.args.get("inspection_date_to")

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name)

    # Phase 18 RBAC: fso-role users see only their own inspections.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None:
        query = query.filter(Inspection.fso_name == scope)

    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)

    if filter_date_from:
        parsed_from = parse_date(filter_date_from)
        if parsed_from:
            query = query.filter(Inspection.inspection_date >= parsed_from)

    if filter_date_to:
        parsed_to = parse_date(filter_date_to)
        if parsed_to:
            query = query.filter(Inspection.inspection_date <= parsed_to)

    if sort_by == "inspection_date":
        if sort_order == "asc":
            query = query.order_by(Inspection.inspection_date.asc())
        else:
            query = query.order_by(Inspection.inspection_date.desc())
    elif sort_by == "compliance_deadline":
        if sort_order == "asc":
            query = query.order_by(Inspection.compliance_deadline.asc())
        else:
            query = query.order_by(Inspection.compliance_deadline.desc())
    elif sort_by == "fso_name":
        query = query.order_by(FSO.fso_name.asc()) if sort_order == "asc" else query.order_by(FSO.fso_name.desc())
    elif sort_by == "inspection_code":
        if sort_order == "asc":
            query = query.order_by(Inspection.inspection_code.asc())
        else:
            query = query.order_by(Inspection.inspection_code.desc())
    else:
        query = query.order_by(Inspection.inspection_date.desc())

    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    all_fso_names = get_all_fso_names()

    return render_template(
        "inspection/list.html",
        inspections=paginated.items,
        pagination=paginated,
        fso_names=all_fso_names,
        sort_by=sort_by,
        sort_order=sort_order,
        filter_fso=filter_fso,
        filter_date_from=filter_date_from,
        filter_date_to=filter_date_to,
    )


@inspection_bp.route("/create", methods=["POST"])
def create_inspection():
    """Create a new inspection record."""
    form_data = request.form.to_dict()

    # Phase 18 RBAC: an fso-role account always owns what it creates.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None:
        form_data["food_safety_officer_name"] = scope

    food_safety_officer_name = form_data.get("food_safety_officer_name", "").strip()
    inspection_date = form_data.get("inspection_date", "").strip()

    if not food_safety_officer_name:
        return jsonify({"error": "food_safety_officer_name is required"}), 400
    if not inspection_date:
        return jsonify({"error": "inspection_date is required"}), 400

    fso = db.session.get(FSO, food_safety_officer_name)
    if not fso:
        return jsonify({"error": f'FSO "{food_safety_officer_name}" not found in database'}), 400

    inspection_code = generate_inspection_code()

    compliance_deadline = form_data.get("compliance_deadline", "").strip()
    if not compliance_deadline:
        compliance_deadline = calculate_compliance_deadline(parse_date(inspection_date))
    else:
        compliance_deadline = parse_date(compliance_deadline)

    fssai_license = form_data.get("fssai_license", "").strip() or None
    ce_license_no = form_data.get("ce_license_no", "").strip() or None
    fbo_name = form_data.get("fbo_name", "").strip() or None
    fbo_address = form_data.get("fbo_address", "").strip() or None
    concerned_food = form_data.get("concerned_food", "").strip() or None
    problem = form_data.get("problem", "").strip() or None
    checklist = {field: form_data[field].strip() for field in CHECKLIST_FIELDS if form_data.get(field, "").strip()}

    visit_purpose = (form_data.get("visit_purpose") or "").strip().lower()
    if visit_purpose not in ("", "routine", "complaint"):
        return jsonify({"error": 'visit_purpose must be "routine" or "complaint"'}), 400
    visit_purpose = visit_purpose or None

    # Sample collection fields
    sample_collected_raw = form_data.get("sample_collected", "").strip().lower()
    sample_collected = sample_collected_raw in ("on", "true", "1", "yes")
    sample_code = form_data.get("sample_code", "").strip() or None

    if sample_collected and not sample_code:
        return jsonify({"error": "sample_code is required when sample_collected is true"}), 400

    if sample_collected and sample_code and not validate_sample_code(sample_code):
        return jsonify({"error": "sample_code must match format SL/WB/XXXXXX/XXXX/XXXXX"}), 400

    inspection = Inspection(
        inspection_code=inspection_code,
        fso_name=food_safety_officer_name,
        fssai_license=fssai_license,
        ce_license_no=ce_license_no,
        fbo_name=fbo_name,
        fbo_address=fbo_address,
        concerned_food=concerned_food,
        problem=problem,
        visit_purpose=visit_purpose,
        checklist_json=json.dumps(checklist) if checklist else None,
        inspection_date=parse_date(inspection_date),
        compliance_deadline=compliance_deadline,
        is_dismissed=False,
        created_at=datetime.now(UTC),
        sample_collected=sample_collected,
        sample_code=sample_code,
    )

    try:
        db.session.add(inspection)
        db.session.commit()

        try:
            row_dict = {
                "id": inspection.id,
                "inspection_code": inspection.inspection_code,
                "fso_name": inspection.fso_name,
                "fssai_license": inspection.fssai_license or "",
                "ce_license_no": inspection.ce_license_no or "",
                "fbo_name": inspection.fbo_name or "",
                "fbo_address": inspection.fbo_address or "",
                "concerned_food": inspection.concerned_food or "",
                "problem": inspection.problem or "",
                "inspection_date": inspection.inspection_date,
                "compliance_deadline": inspection.compliance_deadline,
                "is_dismissed": str(inspection.is_dismissed),
                "dismissed_by": inspection.dismissed_by or "",
                "adjudication_id": str(inspection.adjudication_id or ""),
                "created_at": inspection.created_at.isoformat() if inspection.created_at else "",
                "synced_at": "",
                "sample_collected": str(inspection.sample_collected) if inspection.sample_collected is not None else "",
                "sample_code": inspection.sample_code or "",
            }
            result = sync_row("inspection_log", row_dict, entity_id=inspection.id)
            if result["sheets"]:
                inspection.synced_at = datetime.now(UTC)
                db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Inspection sync failed: {e}")

        return (
            jsonify({
                "message": "Inspection created successfully",
                "inspection_id": inspection.id,
                "inspection_code": inspection.inspection_code,
            }),
            201,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to create inspection: {e!s}"}), 500


def _parse_checklist(raw: str | None) -> dict | None:
    """Parse the stored checklist JSON; corrupt rows degrade to None."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


@inspection_bp.route("/<int:inspection_id>", methods=["GET"])
def get_inspection(inspection_id):
    """Get a specific inspection by ID."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    return jsonify({
        "id": inspection.id,
        "inspection_code": inspection.inspection_code,
        "fso_name": inspection.fso_name,
        "fssai_license": inspection.fssai_license,
        "ce_license_no": inspection.ce_license_no,
        "fbo_name": inspection.fbo_name,
        "fbo_address": inspection.fbo_address,
        "concerned_food": inspection.concerned_food,
        "problem": inspection.problem,
        "visit_purpose": inspection.visit_purpose,
        "checklist": _parse_checklist(inspection.checklist_json),
        "inspection_date": inspection.inspection_date,
        "compliance_deadline": inspection.compliance_deadline,
        "is_dismissed": inspection.is_dismissed,
        "dismissed_by": inspection.dismissed_by,
        "dismissed_at": inspection.dismissed_at.isoformat() if inspection.dismissed_at else None,
        "adjudication_id": inspection.adjudication_id,
        "created_at": inspection.created_at.isoformat() if inspection.created_at else None,
        "synced_at": inspection.synced_at.isoformat() if inspection.synced_at else None,
        "sample_collected": inspection.sample_collected,
        "sample_code": inspection.sample_code,
    })


@inspection_bp.route("/<int:inspection_id>", methods=["PUT"])
def update_inspection(inspection_id):
    """Update an inspection record."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    if inspection.notice_issued_at is not None:
        return jsonify({"error": "Inspection is frozen: an Improvement Notice has already been issued for it."}), 409

    form_data = request.form.to_dict()

    if "food_safety_officer_name" in form_data:
        food_safety_officer_name = form_data["food_safety_officer_name"].strip()
        fso = db.session.get(FSO, food_safety_officer_name)
        if not fso:
            return jsonify({"error": f'FSO "{food_safety_officer_name}" not found'}), 400
        inspection.fso_name = food_safety_officer_name

    if "fssai_license" in form_data:
        inspection.fssai_license = form_data["fssai_license"].strip() or None
    if "ce_license_no" in form_data:
        inspection.ce_license_no = form_data["ce_license_no"].strip() or None
    if "fbo_name" in form_data:
        inspection.fbo_name = form_data["fbo_name"].strip() or None
    if "fbo_address" in form_data:
        inspection.fbo_address = form_data["fbo_address"].strip() or None
    if "concerned_food" in form_data:
        inspection.concerned_food = form_data["concerned_food"].strip() or None
    if "problem" in form_data:
        inspection.problem = form_data["problem"].strip() or None
    if "visit_purpose" in form_data:
        purpose_value = (form_data["visit_purpose"] or "").strip().lower()
        if purpose_value not in ("", "routine", "complaint"):
            return jsonify({"error": 'visit_purpose must be "routine" or "complaint"'}), 400
        inspection.visit_purpose = purpose_value or None
    if "inspection_date" in form_data:
        inspection.inspection_date = parse_date(form_data["inspection_date"].strip())
        if "compliance_deadline" not in form_data or not form_data.get("compliance_deadline", "").strip():
            inspection.compliance_deadline = calculate_compliance_deadline(inspection.inspection_date)
    if "compliance_deadline" in form_data:
        inspection.compliance_deadline = parse_date(form_data["compliance_deadline"].strip())

    # Update checklist if any checklist fields are present
    checklist_updated = False
    for field in CHECKLIST_FIELDS:
        if field in form_data:
            checklist_updated = True
            break
    if checklist_updated:
        checklist = {field: form_data[field].strip() for field in CHECKLIST_FIELDS if form_data.get(field, "").strip()}
        inspection.checklist_json = json.dumps(checklist) if checklist else None

    # Update sample collection fields
    if "sample_collected" in form_data:
        sample_collected_raw = form_data["sample_collected"].strip().lower()
        inspection.sample_collected = sample_collected_raw in ("on", "true", "1", "yes")
    if "sample_code" in form_data:
        inspection.sample_code = form_data["sample_code"].strip() or None

    if inspection.sample_collected and not inspection.sample_code:
        return jsonify({"error": "sample_code is required when sample_collected is true"}), 400

    if inspection.sample_collected and inspection.sample_code and not validate_sample_code(inspection.sample_code):
        return jsonify({"error": "sample_code must match format SL/WB/XXXXXX/XXXX/XXXXX"}), 400

    try:
        db.session.commit()

        try:
            row_dict = {
                "id": inspection.id,
                "inspection_code": inspection.inspection_code,
                "fso_name": inspection.fso_name,
                "fssai_license": inspection.fssai_license or "",
                "ce_license_no": inspection.ce_license_no or "",
                "fbo_name": inspection.fbo_name or "",
                "fbo_address": inspection.fbo_address or "",
                "concerned_food": inspection.concerned_food or "",
                "problem": inspection.problem or "",
                "inspection_date": inspection.inspection_date,
                "compliance_deadline": inspection.compliance_deadline,
                "is_dismissed": str(inspection.is_dismissed),
                "dismissed_by": inspection.dismissed_by or "",
                "adjudication_id": str(inspection.adjudication_id or ""),
                "created_at": inspection.created_at.isoformat() if inspection.created_at else "",
                "synced_at": datetime.now(UTC).isoformat(),
                "sample_collected": str(inspection.sample_collected) if inspection.sample_collected is not None else "",
                "sample_code": inspection.sample_code or "",
            }
            sync_row("inspection_log", row_dict, entity_id=inspection.id)
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")

        return jsonify({"message": "Inspection updated successfully"}), 200
    except StaleDataError:
        db.session.rollback()
        return jsonify({
            "error": "Conflict: this inspection was modified by another user. Please reload and try again."
        }), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to update inspection: {e!s}"}), 500


@inspection_bp.route("/<int:inspection_id>", methods=["DELETE"])
def delete_inspection(inspection_id):
    """Delete an inspection record."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    try:
        db.session.delete(inspection)
        db.session.commit()
        return jsonify({"message": "Inspection deleted successfully"}), 200
    except StaleDataError:
        db.session.rollback()
        return jsonify({
            "error": "Conflict: this inspection was modified by another user. Please reload and try again."
        }), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete inspection: {e!s}"}), 500
