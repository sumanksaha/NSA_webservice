"""Core inspection CRUD routes: index, list, create, get, update, delete."""

from datetime import UTC, datetime

from flask import current_app, jsonify, render_template, request

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.inspection_utils import calculate_compliance_deadline, generate_inspection_code
from app.models import FSO, Inspection
from app.services.sheets_sync import sync_to_sheets
from app.utils.filters import parse_date
from app.utils.fso_data import get_all_fso_names


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

    inspection = Inspection(
        inspection_code=inspection_code,
        fso_name=food_safety_officer_name,
        fssai_license=fssai_license,
        ce_license_no=ce_license_no,
        fbo_name=fbo_name,
        fbo_address=fbo_address,
        concerned_food=concerned_food,
        problem=problem,
        inspection_date=parse_date(inspection_date),
        compliance_deadline=compliance_deadline,
        is_dismissed=False,
        created_at=datetime.now(UTC),
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
            }
            success = sync_to_sheets("inspection_log", row_dict)
            if success:
                inspection.synced_at = datetime.now(UTC)
                db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")

        # Multi-target sync: Airtable (best-effort)
        try:
            from app.services.airtable_sync import sync_to_airtable

            sync_to_airtable("inspection_log", row_dict, inspection.id)
        except Exception as e:
            current_app.logger.warning(f"Inspection: Airtable sync failed: {e}")

        # Multi-target sync: Excel Online (best-effort)
        try:
            from app.services.excel_sync import sync_to_excel

            sync_to_excel("inspection_log", row_dict)
        except Exception as e:
            current_app.logger.warning(f"Inspection: Excel sync failed: {e}")

        return (
            jsonify(
                {
                    "message": "Inspection created successfully",
                    "inspection_id": inspection.id,
                    "inspection_code": inspection.inspection_code,
                }
            ),
            201,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to create inspection: {e!s}"}), 500


@inspection_bp.route("/<int:inspection_id>", methods=["GET"])
def get_inspection(inspection_id):
    """Get a specific inspection by ID."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    return jsonify(
        {
            "id": inspection.id,
            "inspection_code": inspection.inspection_code,
            "fso_name": inspection.fso_name,
            "fssai_license": inspection.fssai_license,
            "ce_license_no": inspection.ce_license_no,
            "fbo_name": inspection.fbo_name,
            "fbo_address": inspection.fbo_address,
            "concerned_food": inspection.concerned_food,
            "problem": inspection.problem,
            "inspection_date": inspection.inspection_date,
            "compliance_deadline": inspection.compliance_deadline,
            "is_dismissed": inspection.is_dismissed,
            "dismissed_by": inspection.dismissed_by,
            "dismissed_at": inspection.dismissed_at.isoformat() if inspection.dismissed_at else None,
            "adjudication_id": inspection.adjudication_id,
            "created_at": inspection.created_at.isoformat() if inspection.created_at else None,
            "synced_at": inspection.synced_at.isoformat() if inspection.synced_at else None,
        }
    )


@inspection_bp.route("/<int:inspection_id>", methods=["PUT"])
def update_inspection(inspection_id):
    """Update an inspection record."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

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
    if "inspection_date" in form_data:
        inspection.inspection_date = parse_date(form_data["inspection_date"].strip())
        if "compliance_deadline" not in form_data or not form_data.get("compliance_deadline", "").strip():
            inspection.compliance_deadline = calculate_compliance_deadline(inspection.inspection_date)
    if "compliance_deadline" in form_data:
        inspection.compliance_deadline = parse_date(form_data["compliance_deadline"].strip())

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
            }
            sync_to_sheets("inspection_log", row_dict)
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")

        return jsonify({"message": "Inspection updated successfully"}), 200
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
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete inspection: {e!s}"}), 500
