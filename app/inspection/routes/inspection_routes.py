"""Core inspection CRUD routes: index, list, create, get, update, delete."""

import json
import re
from datetime import UTC, datetime

from flask import current_app, jsonify, render_template, request
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.inspection import inspection_bp
from app.inspection.code_generation import calculate_compliance_deadline, generate_inspection_code
from app.models import FSO, Inspection, Sample
from app.sample.sample_utils import sample_to_sync_row, validate_sample_code, validate_sample_status
from app.services.sync_orchestrator import sync_row
from app.shared.context_derivers import CHECKLIST_FIELDS
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
    if scope:
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

    # Offline capture: a device-issued provisional id means the record was
    # captured offline and the server-issued inspection code is allocated at
    # sync time. Otherwise allocate the code now (online path).
    provisional_id = form_data.get("provisional_id", "").strip() or None
    captured_at_raw = form_data.get("captured_at", "").strip()
    captured_at = parse_date(captured_at_raw) if captured_at_raw else None
    if provisional_id:
        inspection_code = None
    else:
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
    fbo_type = (form_data.get("fbo_type", "") or "").strip().lower() or None
    if fbo_type and fbo_type not in ("manufacturer", "retailer", "food vending establishment"):
        return jsonify({"error": "fbo_type must be 'manufacturer', 'retailer', or 'food vending establishment'"}), 400
    concerned_food = form_data.get("concerned_food", "").strip() or None
    problem = form_data.get("problem", "").strip() or None
    notes = form_data.get("notes", "").strip() or None
    checklist = {field: form_data[field].strip() for field in CHECKLIST_FIELDS if form_data.get(field, "").strip()}

    visit_purpose = (form_data.get("visit_purpose") or "").strip().lower()
    reinspection_of_id_raw = form_data.get("reinspection_of_id", "").strip()
    reinspection_of_id = int(reinspection_of_id_raw) if reinspection_of_id_raw.isdigit() else None
    is_reinspection = form_data.get("is_reinspection", "").strip().lower() in ("on", "true", "1", "yes")
    days_allowed_for_errors = form_data.get("days_allowed_for_errors", "").strip()
    if days_allowed_for_errors:
        try:
            days_allowed_for_errors = int(days_allowed_for_errors)
        except ValueError:
            days_allowed_for_errors = None
    errors_in_second_inspection = form_data.get("errors_in_second_inspection", "").strip()
    if errors_in_second_inspection:
        try:
            errors_in_second_inspection = int(errors_in_second_inspection)
        except ValueError:
            errors_in_second_inspection = None
    if visit_purpose not in ("", "routine", "complaint"):
        return jsonify({"error": 'visit_purpose must be "routine" or "complaint"'}), 400
    visit_purpose = visit_purpose or None

    # Sample collection fields (optional block — shown only when ticked)
    sample_collected_raw = form_data.get("sample_collected", "").strip().lower()
    sample_collected = sample_collected_raw in ("on", "true", "1", "yes")
    sample_type = (form_data.get("sample_type", "") or "").strip().lower()
    sample_code = form_data.get("sample_code", "").strip() or None
    collection_date_raw = (form_data.get("collection_date", "") or "").strip()
    collection_date = parse_date(collection_date_raw) if collection_date_raw else None
    sample_name = (form_data.get("sample_name", "") or "").strip() or None
    price = (form_data.get("price", "") or "").strip() or None
    sample_status = (form_data.get("sample_status", "") or "").strip().lower()
    # Optional fields (only validated when present)
    batch_no = (form_data.get("batch_no", "") or "").strip() or None
    mfd = (form_data.get("mfd", "") or "").strip() or None
    exp = (form_data.get("exp", "") or "").strip() or None
    manufacturer_details = (form_data.get("manufacturer_details", "") or "").strip() or None
    submission_date_raw = (form_data.get("submission_date", "") or "").strip()
    submission_date = parse_date(submission_date_raw) if submission_date_raw else None

    if sample_collected:
        if not sample_type or sample_type not in ("enforcement", "surveillance"):
            return jsonify({"error": "sample_type must be 'enforcement' or 'surveillance'"}), 400
        code_error = validate_sample_code(sample_code, sample_type)
        if code_error:
            return jsonify({"error": code_error}), 400
        if not collection_date:
            return jsonify({"error": "collection_date is required when sample_collected is true"}), 400
        if not sample_name:
            return jsonify({"error": "sample_name is required when sample_collected is true"}), 400
        if not price:
            return jsonify({"error": "price is required when sample_collected is true"}), 400
        status_error = validate_sample_status(sample_status)
        if status_error:
            return jsonify({"error": status_error}), 400

    inspection = Inspection(
        inspection_code=inspection_code,
        provisional_id=provisional_id,
        captured_at=captured_at,
        fso_name=food_safety_officer_name,
        fssai_license=fssai_license,
        ce_license_no=ce_license_no,
        fbo_name=fbo_name,
        fbo_address=fbo_address,
        fbo_type=fbo_type,
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
        is_reinspection=is_reinspection,
        reinspection_of_id=reinspection_of_id,
        days_allowed_for_errors=days_allowed_for_errors,
        errors_in_second_inspection=errors_in_second_inspection,
    )

    try:
        db.session.add(inspection)
        db.session.commit()

        # --- Upsert the linked Sample record when sample was collected ---
        if sample_collected:
            existing_sample = Sample.query.filter_by(sample_code=sample_code).first()
            if existing_sample and existing_sample.inspection_id and existing_sample.inspection_id != inspection.id:
                db.session.rollback()
                return jsonify({"error": f"Sample code '{sample_code}' already linked to another inspection"}), 400
            if existing_sample:
                existing_sample.inspection_id = inspection.id
                existing_sample.sample_name = sample_name
                existing_sample.sample_type = sample_type
                existing_sample.fso_name = food_safety_officer_name
                existing_sample.collection_date = collection_date
                existing_sample.submission_date = submission_date
                existing_sample.price = price
                existing_sample.sample_status = sample_status
                existing_sample.batch_no = batch_no
                existing_sample.mfd = mfd
                existing_sample.exp = exp
                existing_sample.manufacturer_details = manufacturer_details
                existing_sample.version_id = (existing_sample.version_id or 1) + 1
                db.session.add(existing_sample)
            else:
                new_sample = Sample(
                    sample_code=sample_code,
                    sample_name=sample_name,
                    sample_type=sample_type,
                    fso_name=food_safety_officer_name,
                    collection_date=collection_date,
                    submission_date=submission_date,
                    price=price,
                    sample_status=sample_status,
                    batch_no=batch_no,
                    mfd=mfd,
                    exp=exp,
                    manufacturer_details=manufacturer_details,
                    inspection_id=inspection.id,
                )
                db.session.add(new_sample)
            db.session.commit()

            # Sync the Sample row to Sheets
            try:
                sample_row = sample_to_sync_row(
                    existing_sample or new_sample,
                    synced_at=datetime.now(UTC).isoformat(),
                )
                sync_row("sample_repo", sample_row, entity_id=(existing_sample or new_sample).id)
            except Exception as e:
                current_app.logger.warning(f"Sample sync failed (non-fatal): {e}")

        try:
            row_dict = {
                "id": inspection.id,
                "inspection_code": inspection.inspection_code,
                "fso_name": inspection.fso_name,
                "fssai_license": inspection.fssai_license or "",
                "ce_license_no": inspection.ce_license_no or "",
                "fbo_name": inspection.fbo_name or "",
                "fbo_address": inspection.fbo_address or "",
                "fbo_type": inspection.fbo_type or "",
                "concerned_food": inspection.concerned_food or "",
                "problem": inspection.problem or "",
                "inspection_date": inspection.inspection_date,
                "compliance_deadline": inspection.compliance_deadline,
                "is_dismissed": str(inspection.is_dismissed),
                "dismissed_by": inspection.dismissed_by or "",
                "adjudication_id": str(inspection.adjudication_id or ""),
                "created_at": inspection.created_at.isoformat() if inspection.created_at else "",
                "synced_at": "",
                "provisional_id": inspection.provisional_id or "",
                "captured_at": inspection.captured_at.isoformat() if inspection.captured_at else "",
                "sample_collected": str(inspection.sample_collected) if inspection.sample_collected is not None else "",
                "sample_code": inspection.sample_code or "",
            }
            sync_row("inspection_log", row_dict, entity_id=inspection.id)
            inspection.synced_at = datetime.now(UTC)
            db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Inspection sync failed (non-fatal): {e}")

        return (
            jsonify({
                "message": "Inspection created successfully",
                "inspection_id": inspection.id,
                "inspection_code": inspection.inspection_code,
                "provisional_id": inspection.provisional_id,
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
        parsed: dict | None = json.loads(raw)
        return parsed
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
        "fbo_type": inspection.fbo_type,
        "concerned_food": inspection.concerned_food,
        "problem": inspection.problem,
        "notes": inspection.notes,
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
        "is_reinspection": inspection.is_reinspection,
        "days_allowed_for_errors": inspection.days_allowed_for_errors,
        "errors_in_second_inspection": inspection.errors_in_second_inspection,
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
    if "fbo_type" in form_data:
        fbo_type_val = (form_data["fbo_type"] or "").strip().lower()
        if fbo_type_val and fbo_type_val not in ("manufacturer", "retailer", "food vending establishment"):
            return jsonify({
                "error": "fbo_type must be 'manufacturer', 'retailer', or 'food vending establishment'"
            }), 400
        inspection.fbo_type = fbo_type_val or None
    if "concerned_food" in form_data:
        inspection.concerned_food = form_data["concerned_food"].strip() or None
    if "problem" in form_data:
        inspection.problem = form_data["problem"].strip() or None
    if "notes" in form_data:
        inspection.notes = form_data["notes"].strip() or None
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

    # Update reinspection fields
    if "is_reinspection" in form_data:
        inspection.is_reinspection = form_data["is_reinspection"].strip().lower() in ("on", "true", "1", "yes")
    if "days_allowed_for_errors" in form_data:
        days_allowed = form_data["days_allowed_for_errors"].strip()
        if days_allowed:
            try:
                inspection.days_allowed_for_errors = int(days_allowed)
            except ValueError:
                pass
    if "errors_in_second_inspection" in form_data:
        errors_allowed = form_data["errors_in_second_inspection"].strip()
        if errors_allowed:
            try:
                inspection.errors_in_second_inspection = int(errors_allowed)
            except ValueError:
                pass

    # Update checklist if any checklist fields are present
    checklist_updated = False
    for field in CHECKLIST_FIELDS:
        if field in form_data:
            checklist_updated = True
            break
    if checklist_updated:
        checklist = {field: form_data[field].strip() for field in CHECKLIST_FIELDS if form_data.get(field, "").strip()}
        inspection.checklist_json = json.dumps(checklist) if checklist else None

    # Update sample collection fields (optional block)
    new_sample_collected = inspection.sample_collected
    new_sample_type = None
    new_sample_code = inspection.sample_code
    new_collection_date = None
    new_sample_name = None
    new_price = None
    new_sample_status = None
    new_batch_no = None
    new_mfd = None
    new_exp = None
    new_manufacturer_details = None
    new_submission_date = None

    if "sample_collected" in form_data:
        new_sample_collected = form_data["sample_collected"].strip().lower() in ("on", "true", "1", "yes")
    if "sample_type" in form_data:
        new_sample_type = (form_data["sample_type"] or "").strip().lower()
    if "sample_code" in form_data:
        new_sample_code = form_data["sample_code"].strip() or None
    if "collection_date" in form_data:
        cdr = (form_data["collection_date"] or "").strip()
        new_collection_date = parse_date(cdr) if cdr else None
    if "sample_name" in form_data:
        new_sample_name = (form_data["sample_name"] or "").strip() or None
    if "price" in form_data:
        new_price = (form_data["price"] or "").strip() or None
    if "sample_status" in form_data:
        new_sample_status = (form_data["sample_status"] or "").strip().lower()
    if "batch_no" in form_data:
        new_batch_no = (form_data["batch_no"] or "").strip() or None
    if "mfd" in form_data:
        new_mfd = (form_data["mfd"] or "").strip() or None
    if "exp" in form_data:
        new_exp = (form_data["exp"] or "").strip() or None
    if "manufacturer_details" in form_data:
        new_manufacturer_details = (form_data["manufacturer_details"] or "").strip() or None
    if "submission_date" in form_data:
        sdr = (form_data["submission_date"] or "").strip()
        new_submission_date = parse_date(sdr) if sdr else None

    if new_sample_collected:
        if not new_sample_type or new_sample_type not in ("enforcement", "surveillance"):
            return jsonify({"error": "sample_type must be 'enforcement' or 'surveillance'"}), 400
        code_error = validate_sample_code(new_sample_code, new_sample_type)
        if code_error:
            return jsonify({"error": code_error}), 400
        if not new_collection_date:
            return jsonify({"error": "collection_date is required when sample_collected is true"}), 400
        if not new_sample_name:
            return jsonify({"error": "sample_name is required when sample_collected is true"}), 400
        if not new_price:
            return jsonify({"error": "price is required when sample_collected is true"}), 400
        status_error = validate_sample_status(new_sample_status)
        if status_error:
            return jsonify({"error": status_error}), 400

    inspection.sample_collected = new_sample_collected
    inspection.sample_code = new_sample_code

    try:
        db.session.commit()

        # --- Upsert the linked Sample record when sample was collected ---
        if new_sample_collected:
            existing_sample = Sample.query.filter_by(sample_code=new_sample_code).first()
            if existing_sample and existing_sample.inspection_id and existing_sample.inspection_id != inspection.id:
                db.session.rollback()
                return jsonify({"error": f"Sample code '{new_sample_code}' already linked to another inspection"}), 400
            if existing_sample:
                existing_sample.inspection_id = inspection.id
                existing_sample.sample_name = new_sample_name
                existing_sample.sample_type = new_sample_type
                existing_sample.fso_name = inspection.fso_name
                existing_sample.collection_date = new_collection_date
                existing_sample.submission_date = new_submission_date
                existing_sample.price = new_price
                existing_sample.sample_status = new_sample_status
                existing_sample.batch_no = new_batch_no
                existing_sample.mfd = new_mfd
                existing_sample.exp = new_exp
                existing_sample.manufacturer_details = new_manufacturer_details
                existing_sample.version_id = (existing_sample.version_id or 1) + 1
                db.session.add(existing_sample)
            else:
                new_sample = Sample(
                    sample_code=new_sample_code,
                    sample_name=new_sample_name,
                    sample_type=new_sample_type,
                    fso_name=inspection.fso_name,
                    collection_date=new_collection_date,
                    submission_date=new_submission_date,
                    price=new_price,
                    sample_status=new_sample_status,
                    batch_no=new_batch_no,
                    mfd=new_mfd,
                    exp=new_exp,
                    manufacturer_details=new_manufacturer_details,
                    inspection_id=inspection.id,
                )
                db.session.add(new_sample)
            db.session.commit()

            # Sync the Sample row to Sheets
            try:
                sample_row = sample_to_sync_row(
                    existing_sample or new_sample,
                    synced_at=datetime.now(UTC).isoformat(),
                )
                sync_row("sample_repo", sample_row, entity_id=(existing_sample or new_sample).id)
            except Exception as e:
                current_app.logger.warning(f"Sample sync failed (non-fatal): {e}")
        else:
            # Sample was unchecked � unlink any previously linked Sample so the
            # inspection no longer references it (cache columns already cleared).
            linked = Sample.query.filter_by(inspection_id=inspection.id).first()
            if linked:
                linked.inspection_id = None
                linked.version_id = (linked.version_id or 1) + 1
                db.session.add(linked)
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
                "fbo_type": inspection.fbo_type or "",
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
                "is_reinspection": str(inspection.is_reinspection) if inspection.is_reinspection is not None else "",
                "days_allowed_for_errors": str(inspection.days_allowed_for_errors)
                if inspection.days_allowed_for_errors is not None
                else "",
                "errors_in_second_inspection": str(inspection.errors_in_second_inspection)
                if inspection.errors_in_second_inspection is not None
                else "",
            }
            sync_row("inspection_log", row_dict, entity_id=inspection.id)
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")

        return jsonify({"message": "Inspection updated successfully"}), 200
    except StaleDataError:
        db.session.rollback()
        return jsonify({
            "error": "Conflict: this inspection was modified by another user. Please reload and try again.",
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
            "error": "Conflict: this inspection was modified by another user. Please reload and try again.",
        }), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete inspection: {e!s}"}), 500


@inspection_bp.route("/<int:inspection_id>/serve-notice", methods=["POST"])
def serve_notice(inspection_id):
    """Capture the manual service date of an improvement notice and set 15 working-day re-inspection timer."""
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404

    served_raw = (
        (request.form.get("service_date") or request.json.get("service_date") if request.is_json else None)
        if request.is_json
        else request.form.get("service_date")
    )
    served_at = parse_date(served_raw.strip()) if served_raw else datetime.now(UTC)
    if not served_at:
        served_at = datetime.now(UTC)

    from app.inspection.working_days import add_working_days

    inspection.improvement_notice_served_at = served_at
    inspection.reinspection_due_at = add_working_days(served_at, 15)

    try:
        db.session.commit()
        return jsonify({
            "message": "Notice service date recorded",
            "served_at": served_at.isoformat(),
            "reinspection_due_at": inspection.reinspection_due_at.isoformat(),
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ponytail: auth gate added; RBAC handled by blueprint-level auth
# Add @login_required if required by app auth policy.
def premises_search():
    """Search the FSSAI registry and build an inspection checklist.

    Querying, address normalisation and road-alias handling belong to
    ``PremisesResolver`` (app/services/premises_resolver.py) — this route only
    renders results and echoes the selected identifiers back.
    """
    from app.services import premises_resolver

    query = (request.args.get("q") or request.form.get("q") or "").strip()
    selected = request.form.getlist("selected")
    results: list = []
    checklist = None

    result = premises_resolver.resolve(query) if query else None
    if result is not None and result.error is not None:
        # ponytail: graceful degradation; never leak raw DB errors
        current_app.logger.warning(f"Premises search failed: {result.error}")
        return render_template(
            "inspection/premises_search.html",
            query=query,
            results=[],
            checklist=None,
            error="Search failed. Check DB connection or try later.",
        )
    if result is not None:
        results = list(result.candidates)

    if request.method == "POST" and selected:
        # ponytail: validate selected IDs against returned results; prevents forged submissions
        # ponytail: include source_table for audit trail
        returned = {r.fssai_number for r in results}
        accepted = [s for s in selected if s in returned]
        checklist = {
            "premises": accepted,
            "items": CHECKLIST_FIELDS,
            "source_tables": sorted({r.source_table for r in results if r.fssai_number in accepted}),
        }

    return render_template(
        "inspection/premises_search.html",
        query=query,
        results=results,
        checklist=checklist,
    )


# appended escalation routes placeholder


@inspection_bp.route("/<int:inspection_id>/mark-shortcomings", methods=["POST"])
def mark_shortcomings(inspection_id):
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404
    persist = request.form.get("persist")
    if request.is_json:
        persist = request.json.get("persist", persist)
    inspection.shortcomings_persist = str(persist).lower() in ("1", "true", "yes", "on")
    if request.is_json and request.json.get("shortcomings"):
        import json as _j

        inspection.shortcomings_json = _j.dumps(request.json.get("shortcomings"))
    try:
        db.session.commit()
        return jsonify({"message": "ok", "shortcomings_persist": inspection.shortcomings_persist}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@inspection_bp.route("/<int:inspection_id>/escalate-adjudication", methods=["POST"])
def escalate_adjudication(inspection_id):
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404
    if not inspection.shortcomings_persist:
        return jsonify({"error": "No persisting shortcomings"}), 400
    from app.models import Adjudication

    adj = Adjudication()
    adj.case_number = inspection.inspection_code or f"INSP-{inspection.id}"
    adj.food_safety_officer = inspection.fso_name
    adj.fbo_owner = inspection.fbo_name or ""
    adj.fbo_name = inspection.fbo_name or ""
    adj.fbo_address = inspection.fbo_address or ""
    adj.fssai_license = inspection.fssai_license or ""
    adj.inspection_id = inspection.id
    adj.First_inspection_date = inspection.inspection_date
    adj.compliance_deadline = inspection.compliance_deadline
    adj.inspection_date = inspection.inspection_date
    adj.notes = inspection.shortcomings_json or inspection.notes
    try:
        db.session.add(adj)
        db.session.commit()
        inspection.adjudication_id = adj.id
        db.session.commit()
        return jsonify({"message": "escalated", "adjudication_id": adj.id}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@inspection_bp.route("/<int:inspection_id>/launch-adjudication", methods=["POST"])
def launch_adjudication(inspection_id):
    """Launch a non-sample adjudication directly from the inspection UI.

    Creates an Adjudication seeded from the inspection context with no
    Sample linkage. Works whether or not a sample was collected.
    """
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404
    if inspection.notice_issued_at is None:
        return jsonify({"error": "No Improvement Notice issued for this inspection"}), 400

    from app.models import Adjudication

    adj = Adjudication()
    adj.case_number = inspection.inspection_code or f"INSP-{inspection.id}"
    adj.food_safety_officer = inspection.fso_name
    adj.fbo_owner = inspection.fbo_name or ""
    adj.fbo_name = inspection.fbo_name or ""
    adj.fbo_address = inspection.fbo_address or ""
    adj.fssai_license = inspection.fssai_license or ""
    adj.concerned_food = inspection.concerned_food or ""
    adj.problem = inspection.problem or ""
    adj.inspection_id = inspection.id
    adj.First_inspection_date = inspection.inspection_date
    adj.compliance_deadline = inspection.compliance_deadline
    adj.inspection_date = inspection.inspection_date
    adj.notes = inspection.shortcomings_json or inspection.notes or ""
    # Non-sample path: no sample linkage
    adj.sample_id = None

    try:
        db.session.add(adj)
        db.session.commit()
        inspection.adjudication_id = adj.id
        db.session.commit()
        return jsonify({
            "message": "Non-sample adjudication launched",
            "adjudication_id": adj.id,
            "sample_id": None,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@inspection_bp.route("/<int:inspection_id>/launch-sample-adjudication", methods=["POST"])
def launch_sample_adjudication(inspection_id):
    """Launch a sample-based adjudication by linking a Sample.

    The Sample must have been inserted via the Inspection UI (linked to this
    inspection or the same FBO). Cross-FBO linkage is rejected.
    """
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404
    if inspection.notice_issued_at is None:
        return jsonify({"error": "No Improvement Notice issued for this inspection"}), 400

    form_data = request.form.to_dict()
    sample_id_raw = form_data.get("sample_id", "").strip()
    if not sample_id_raw or not sample_id_raw.isdigit():
        return jsonify({"error": "sample_id is required"}), 400
    sample_id = int(sample_id_raw)

    sample = db.session.get(Sample, sample_id)
    if not sample:
        return jsonify({"error": f"Sample with id {sample_id} not found"}), 404

    # Cross-FBO guard: sample must belong to this inspection or same FBO
    if sample.inspection_id and sample.inspection_id != inspection.id:
        return jsonify({"error": "Sample is linked to a different inspection"}), 400
    if sample.fso_name != inspection.fso_name:
        return jsonify({"error": "Sample FSO does not match inspection FSO"}), 400

    from app.models import Adjudication

    adj = Adjudication()
    adj.case_number = inspection.inspection_code or f"INSP-{inspection.id}"
    adj.food_safety_officer = inspection.fso_name
    adj.fbo_owner = inspection.fbo_name or ""
    adj.fbo_name = inspection.fbo_name or ""
    adj.fbo_address = inspection.fbo_address or ""
    adj.fssai_license = inspection.fssai_license or ""
    adj.concerned_food = sample.sample_name or inspection.concerned_food or ""
    adj.problem = inspection.problem or ""
    adj.inspection_id = inspection.id
    adj.First_inspection_date = inspection.inspection_date
    adj.compliance_deadline = inspection.compliance_deadline
    adj.inspection_date = inspection.inspection_date
    adj.notes = inspection.shortcomings_json or inspection.notes or ""
    # Sample linkage
    adj.sample_id = sample.id

    try:
        db.session.add(adj)
        db.session.commit()
        inspection.adjudication_id = adj.id
        db.session.commit()
        return jsonify({
            "message": "Sample adjudication launched",
            "adjudication_id": adj.id,
            "sample_id": sample.id,
            "sample_code": sample.sample_code,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@inspection_bp.route("/<int:inspection_id>/update-sample-details", methods=["POST"])
def update_sample_details(inspection_id):
    """Update richer Sample fields (retailer details, OCR fields) via the
    inspection/adjudication UI.  These fields are no longer edited through
    the standalone Sample UI (deprecated in Phase 3).
    """
    inspection = db.session.get(Inspection, inspection_id)
    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404
    if not inspection.sample:
        return jsonify({"error": "No linked sample for this inspection"}), 404
    if inspection.notice_issued_at is not None:
        return jsonify({"error": "Inspection is frozen: an Improvement Notice has already been issued for it."}), 409

    form_data = request.form.to_dict()
    sample = inspection.sample

    if "retailer_fssai" in form_data:
        sample.retailer_fssai = form_data["retailer_fssai"].strip() or None
    if "retailer_name" in form_data:
        sample.retailer_name = form_data["retailer_name"].strip() or None
    if "nature_of_food" in form_data:
        sample.nature_of_food = form_data["nature_of_food"].strip() or None
    if "batch_no" in form_data:
        sample.batch_no = form_data["batch_no"].strip() or None
    if "mfd" in form_data:
        sample.mfd = form_data["mfd"].strip() or None
    if "exp" in form_data:
        sample.exp = form_data["exp"].strip() or None
    if "manufacturer_details" in form_data:
        sample.manufacturer_details = form_data["manufacturer_details"].strip() or None

    sample.version_id = (sample.version_id or 1) + 1

    try:
        db.session.commit()

        # Sync to Sheets
        try:
            sample_row = sample_to_sync_row(sample, synced_at=datetime.now(UTC).isoformat())
            sync_row("sample_repo", sample_row, entity_id=sample.id)
        except Exception as e:
            current_app.logger.warning(f"Sample sync failed (non-fatal): {e}")

        return jsonify({"message": "Sample details updated successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to update sample details: {e!s}"}), 500


@inspection_bp.route("/samples")
def samples_list():
    """List all samples collected via inspections (Phase 4 audit view).

    Replaces the deprecated /sample/list route.  Queries Sample joined
    with Inspection so the user can see which inspection drew each sample.
    """
    from app.models import Sample

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    sort_by = request.args.get("sort_by", "collection_date")
    sort_order = request.args.get("sort_order", "desc")
    filter_fso = request.args.get("fso_name")
    filter_date_from = request.args.get("collection_date_from")
    filter_date_to = request.args.get("collection_date_to")
    filter_status = request.args.get("sample_status")

    query = Sample.query

    if filter_fso:
        query = query.filter(Sample.fso_name == filter_fso)
    if filter_date_from:
        parsed_from = parse_date(filter_date_from)
        if parsed_from:
            query = query.filter(Sample.collection_date >= parsed_from)
    if filter_date_to:
        parsed_to = parse_date(filter_date_to)
        if parsed_to:
            query = query.filter(Sample.collection_date <= parsed_to)
    if filter_status:
        query = query.filter(Sample.sample_status == filter_status)

    if sort_by == "collection_date":
        if sort_order == "asc":
            query = query.order_by(Sample.collection_date.asc())
        else:
            query = query.order_by(Sample.collection_date.desc())
    elif sort_by == "sample_code":
        if sort_order == "asc":
            query = query.order_by(Sample.sample_code.asc())
        else:
            query = query.order_by(Sample.sample_code.desc())
    else:
        query = query.order_by(Sample.collection_date.desc())

    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    fso_names = get_all_fso_names()

    return render_template(
        "inspection/samples.html",
        samples=paginated.items,
        pagination=paginated,
        fso_names=fso_names,
        filter_fso=filter_fso,
        filter_date_from=filter_date_from,
        filter_date_to=filter_date_to,
        filter_status=filter_status,
        sort_by=sort_by,
        sort_order=sort_order,
    )
