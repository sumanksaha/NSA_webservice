"""Inspection routes module.

Provides endpoints for Inspection CRUD operations and UI.
"""

import contextlib
import os
import uuid
from datetime import date, datetime
from pathlib import Path

from flask import current_app, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from app.extensions import db

# Import the blueprint from __init__.py
from app.inspection import inspection_bp
from app.inspection.audit import log_audit
from app.inspection.image_processing import process_and_stamp_image
from app.inspection.inspection_utils import calculate_compliance_deadline, generate_inspection_code
from app.inspection.verification_service import verify_photo_location
from app.models import FSO, Adjudication, Inspection, InspectionPhoto, PhotoEvidence
from app.services.sheets_sync import sync_to_sheets
from app.utils.filters import parse_date
from app.utils.fso_data import get_all_fso_names
from app.utils.lookup import lookup_ce, lookup_fssai
from app.utils.storage import delete_photo, upload_photo

# Lazy-load OCR task availability flag (graceful fallback if deps missing)
try:
    from app.inspection.tasks import run_ocr_extraction

    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


def _apply_inspection_sorting(query, sort_by, sort_order):
    """Apply common sorting to an Inspection query joined with FSO.

    Args:
        query: SQLAlchemy query with Inspection joined to FSO.
        sort_by: Column name to sort by.
        sort_order: 'asc' or 'desc'.

    Returns:
        Sorted query.

    """
    # Order clause mapping for sort_by values
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
    # Get query parameters
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    sort_by = request.args.get("sort_by", "inspection_date")
    sort_order = request.args.get("sort_order", "desc")
    filter_fso = request.args.get("fso_name")
    filter_date_from = request.args.get("inspection_date_from")
    filter_date_to = request.args.get("inspection_date_to")

    # Base query
    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name)

    # Apply filters
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

    # Apply sorting
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

    # Paginate
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get all FSO names for filter dropdown
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


@inspection_bp.route("/lookup_fssai", methods=["POST"])
def lookup_fssai_route():
    """Lookup FSSAI license information."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    fssai_number = data.get("fssai_license", "").strip()
    if not fssai_number:
        return jsonify({"error": "FSSAI license number is required"}), 400

    # Use existing lookup function
    result, error = lookup_fssai(fssai_number)

    if error:
        return jsonify({"error": error, "source": "fssai"}), 404

    if result:
        return jsonify({
            "fbo_name": result.get("companyName"),
            "fbo_address": result.get("fullAddress"),
            "expiry_date": result.get("expiryDate"),
            "source": result.get("source"),
        })

    return jsonify({"error": "FSSAI license not found"}), 404


@inspection_bp.route("/lookup_ce", methods=["POST"])
def lookup_ce_route():
    """Lookup CE (KMC Trade) license information."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    ce_number = data.get("ce_license_no", "").strip()
    if not ce_number:
        return jsonify({"error": "CE license number is required"}), 400

    # Use existing lookup function
    try:
        result = lookup_ce(ce_number)
    except Exception as e:
        return jsonify({"error": f"KMC lookup failed: {e!s}"}), 502

    if not result:
        return jsonify({"error": "CE license not found"}), 404

    # Return the full result with identity for consistency with adjudication
    return jsonify(result)


@inspection_bp.route("/create", methods=["POST"])
def create_inspection():
    """Create a new inspection record."""
    form_data = request.form.to_dict()

    # Required fields - using canonical keys from Step 2
    food_safety_officer_name = form_data.get("food_safety_officer_name", "").strip()
    inspection_date = form_data.get("inspection_date", "").strip()

    if not food_safety_officer_name:
        return jsonify({"error": "food_safety_officer_name is required"}), 400
    if not inspection_date:
        return jsonify({"error": "inspection_date is required"}), 400

    # Validate FSO exists - map canonical to DB column
    fso = FSO.query.get(food_safety_officer_name)
    if not fso:
        return jsonify({"error": f'FSO "{food_safety_officer_name}" not found in database'}), 400

    # Generate inspection code
    inspection_code = generate_inspection_code()

    # Calculate compliance deadline
    compliance_deadline = form_data.get("compliance_deadline", "").strip()
    if not compliance_deadline:
        # Auto-calculate if not provided
        compliance_deadline = calculate_compliance_deadline(parse_date(inspection_date))
    else:
        compliance_deadline = parse_date(compliance_deadline)

    # Get form fields
    fssai_license = form_data.get("fssai_license", "").strip() or None
    ce_license_no = form_data.get("ce_license_no", "").strip() or None
    fbo_name = form_data.get("fbo_name", "").strip() or None
    fbo_address = form_data.get("fbo_address", "").strip() or None
    concerned_food = form_data.get("concerned_food", "").strip() or None
    problem = form_data.get("problem", "").strip() or None

    # Create inspection record
    # Map canonical keys to DB columns: food_safety_officer_name -> fso_name (FK)
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
        created_at=datetime.utcnow(),
    )

    try:
        db.session.add(inspection)
        db.session.commit()

        # Sync to Google Sheets (Step 5)
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
                # Update synced_at timestamp
                inspection.synced_at = datetime.utcnow()
                db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Inspection Sheets sync failed: {e}")

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


@inspection_bp.route("/<int:inspection_id>", methods=["GET"])
def get_inspection(inspection_id):
    """Get a specific inspection by ID."""
    inspection = Inspection.query.get(inspection_id)
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
        "inspection_date": inspection.inspection_date,
        "compliance_deadline": inspection.compliance_deadline,
        "is_dismissed": inspection.is_dismissed,
        "dismissed_by": inspection.dismissed_by,
        "dismissed_at": inspection.dismissed_at.isoformat() if inspection.dismissed_at else None,
        "adjudication_id": inspection.adjudication_id,
        "created_at": inspection.created_at.isoformat() if inspection.created_at else None,
        "synced_at": inspection.synced_at.isoformat() if inspection.synced_at else None,
    })


@inspection_bp.route("/<int:inspection_id>", methods=["PUT"])
def update_inspection(inspection_id):
    """Update an inspection record."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    form_data = request.form.to_dict()

    # Update fields
    # Map canonical key to DB column: food_safety_officer_name -> fso_name
    if "food_safety_officer_name" in form_data:
        food_safety_officer_name = form_data["food_safety_officer_name"].strip()
        # Validate FSO exists
        fso = FSO.query.get(food_safety_officer_name)
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
        # Recalculate compliance deadline if inspection_date changes and compliance_deadline is not provided
        if "compliance_deadline" not in form_data or not form_data.get("compliance_deadline", "").strip():
            inspection.compliance_deadline = calculate_compliance_deadline(inspection.inspection_date)
    if "compliance_deadline" in form_data:
        inspection.compliance_deadline = parse_date(form_data["compliance_deadline"].strip())

    try:
        db.session.commit()

        # Sync to Google Sheets (Step 5)
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
                "synced_at": datetime.utcnow().isoformat(),
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
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    try:
        db.session.delete(inspection)
        db.session.commit()
        return jsonify({"message": "Inspection deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete inspection: {e!s}"}), 500


# ============================================================================
# Step 4: Derived-State Views and Actions
# ============================================================================


@inspection_bp.route("/open")
def open_issues():
    """Open Issues view: inspections where compliance_deadline >= today AND is_dismissed = false AND adjudication_id IS NULL."""
    today = date.today().isoformat()
    sort_by = request.args.get("sort_by", "compliance_deadline")
    sort_order = request.args.get("sort_order", "asc")
    filter_fso = request.args.get("fso_name")

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        Inspection.compliance_deadline >= today,
        not Inspection.is_dismissed,
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
    today = date.today().isoformat()
    sort_by = request.args.get("sort_by", "compliance_deadline")
    sort_order = request.args.get("sort_order", "asc")
    filter_fso = request.args.get("fso_name")

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        Inspection.compliance_deadline < today,
        not Inspection.is_dismissed,
        Inspection.adjudication_id.is_(None),
    )

    if filter_fso:
        query = query.filter(Inspection.fso_name == filter_fso)

    inspections = _apply_inspection_sorting(query, sort_by, sort_order).all()

    # Calculate days overdue for each inspection
    today_date = datetime.utcnow()
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
    sort_by = request.args.get("sort_by", "dismissed_at")
    sort_order = request.args.get("sort_order", "desc")
    filter_fso = request.args.get("fso_name")
    filter_type = request.args.get("type", "all")  # 'all', 'dismissed', 'adjudicated'

    query = Inspection.query.join(FSO, Inspection.fso_name == FSO.fso_name).filter(
        (Inspection.is_dismissed) | (Inspection.adjudication_id.isnot(None)),
    )

    # Apply type filter
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


@inspection_bp.route("/<int:inspection_id>/dismiss", methods=["POST"])
def dismiss_inspection(inspection_id):
    """Dismiss an inspection (Pending Action only)."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    # Verify this is a Pending Action inspection
    today = date.today().isoformat()
    if inspection.compliance_deadline >= today:
        return jsonify({"error": "Only Pending Action inspections (past deadline) can be dismissed"}), 400

    if inspection.is_dismissed:
        return jsonify({"error": "Inspection is already dismissed"}), 400

    if inspection.adjudication_id:
        return jsonify({"error": "Inspection already linked to adjudication"}), 400

    # Get dismissed_by from form data, default to inspection's FSO if not provided
    dismissed_by = request.form.get("dismissed_by", inspection.fso_name)

    # Update inspection
    inspection.is_dismissed = True
    inspection.dismissed_by = dismissed_by
    inspection.dismissed_at = datetime.utcnow()

    try:
        db.session.commit()
        return (
            jsonify({
                "message": "Inspection dismissed successfully",
                "inspection_id": inspection.id,
                "inspection_code": inspection.inspection_code,
            }),
            200,
        )
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to dismiss inspection: {e!s}"}), 500


@inspection_bp.route("/<int:inspection_id>/create_adjudication", methods=["GET"])
def create_adjudication_from_inspection(inspection_id):
    """Redirect to Adjudication form with prefill data from inspection."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    # Verify this is a Pending Action inspection
    today = date.today().isoformat()
    if inspection.compliance_deadline >= today:
        return jsonify({"error": "Only Pending Action inspections (past deadline) can create adjudication"}), 400

    if inspection.is_dismissed:
        return jsonify({"error": "Dismissed inspections cannot create adjudication"}), 400

    if inspection.adjudication_id:
        return jsonify({"error": "Inspection already linked to adjudication"}), 400

    # Build prefill query parameters - using canonical keys for Step 3
    # Semantic mapping: Inspection.inspection_date -> adjudication.first_inspection_date
    prefill = {
        "from_inspection": inspection_id,
        "food_safety_officer_name": inspection.fso_name,  # canonical
        "fbo_name": inspection.fbo_name or "",
        "fbo_address": inspection.fbo_address or "",
        "fssai_license": inspection.fssai_license or "",
        "ce_license_no": inspection.ce_license_no or "",
        "first_inspection_date": inspection.inspection_date,  # canonical: inspection date -> first inspection
        "compliance_deadline": inspection.compliance_deadline,
        # Do NOT set followup_inspection_date from inspection - leave for user
        "concerned_food": inspection.concerned_food or "",
        "problem": inspection.problem or "",
    }

    # Redirect to adjudication form with prefill data
    # We'll use GET parameters to pass the prefill data
    return redirect(url_for("adjudication.index", **prefill))


@inspection_bp.route("/<int:inspection_id>/link_adjudication/<int:adjudication_id>", methods=["POST"])
def link_adjudication(inspection_id, adjudication_id):
    """Link an inspection to an adjudication after successful save."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    adjudication = Adjudication.query.get(adjudication_id)
    if not adjudication:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    # Verify inspection is eligible for linking
    if inspection.is_dismissed:
        return jsonify({"error": "Dismissed inspections cannot be linked to adjudication"}), 400

    if inspection.adjudication_id:
        return jsonify({"error": "Inspection already linked to adjudication"}), 400

    # Link the inspection to the adjudication
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
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    photos = PhotoEvidence.query.filter_by(inspection_id=inspection_id).order_by(PhotoEvidence.uploaded_at.desc()).all()
    fso_names = get_all_fso_names()
    return render_template("inspection/detail.html", inspection=inspection, photos=photos, fso_names=fso_names)


@inspection_bp.route("/<int:inspection_id>/photo-evidence", methods=["GET"])
def get_inspection_photo_evidence(inspection_id):
    """Get all PhotoEvidence records for an inspection (legacy model, JSON)."""
    inspection = Inspection.query.get(inspection_id)
    if not inspection:
        return jsonify({"error": f"Inspection with id {inspection_id} not found"}), 404

    photos = PhotoEvidence.query.filter_by(inspection_id=inspection_id).order_by(PhotoEvidence.uploaded_at.desc()).all()
    return jsonify([
        {
            "image_id": p.image_id,
            "filepath": p.filepath,
            "raw_lat": p.raw_lat,
            "raw_lng": p.raw_lng,
            "accuracy": p.accuracy,
            "captured_at": p.captured_at.isoformat() if p.captured_at else None,
            "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
            "locality": p.locality,
            "verification_status": p.verification_status,
            "stamped": p.stamped,
        }
        for p in photos
    ])


def _extract_exif_gps(file_obj):
    """Attempt to extract GPS latitude, longitude, and altitude/accuracy from image EXIF.
    Returns (lat, lng, accuracy) or (None, None, None) if unavailable.
    """
    try:
        from PIL import ExifTags, Image

        img = Image.open(file_obj)
        exif = img.getexif()
        if not exif:
            return None, None, None

        gps_info = {}
        for tag, value in exif.items():
            decoded = ExifTags.TAGS.get(tag, tag)
            if decoded == "GPSInfo":
                for gps_tag in value:
                    gps_decoded = ExifTags.GPSTAGS.get(gps_tag, gps_tag)
                    gps_info[gps_decoded] = value[gps_tag]

        def _convert_to_degrees(ref, values):
            if not values or len(values) < 3:
                return None
            d, m, s = values
            try:
                deg = float(d) + float(m) / 60.0 + float(s) / 3600.0
                if ref in ("S", "W"):
                    deg = -deg
                return deg
            except Exception:
                return None

        lat = _convert_to_degrees(gps_info.get("GPSLatitudeRef"), gps_info.get("GPSLatitude"))
        lng = _convert_to_degrees(gps_info.get("GPSLongitudeRef"), gps_info.get("GPSLongitude"))

        # Some cameras store GPSAltitude as (num, den) or a single float
        accuracy = None
        if "GPSAltitude" in gps_info:
            alt = gps_info["GPSAltitude"]
            try:
                accuracy = float(alt)
            except Exception:
                try:
                    accuracy = float(alt[0]) / float(alt[1])
                except Exception:
                    accuracy = None

        return lat, lng, accuracy
    except Exception:
        return None, None, None


@inspection_bp.route("/photo-upload", methods=["POST"])
def upload_photo_evidence():
    """Upload photo evidence for an inspection."""
    # Check if the post request has the file part
    if "image" not in request.files:
        return jsonify({"error": "No image file provided"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    # Accept either inspection_id (preferred) or case_id (legacy)
    inspection_id = request.form.get("inspection_id")
    case_id = request.form.get("case_id")

    if not inspection_id and not case_id:
        return jsonify({"error": "Either inspection_id or case_id is required"}), 400

    # Determine the inspection record
    if inspection_id:
        try:
            inspection_id = int(inspection_id)
        except ValueError:
            return jsonify({"error": "inspection_id must be an integer"}), 400
        inspection = Inspection.query.get(inspection_id)
    else:
        inspection = Inspection.query.get(case_id)
        if inspection:
            inspection_id = inspection.id

    if not inspection:
        return jsonify({"error": "Inspection not found"}), 404

    # Check if this is a sample case (substandard/misbranded violation type)
    # Photo evidence is only applicable for non-sample inspection cases
    if inspection.adjudication_id:
        from app.models import Adjudication

        adjudication = Adjudication.query.get(inspection.adjudication_id)
        if adjudication:
            from app.models import CaseFile

            sample_case = CaseFile.query.filter_by(
                food_safety_officer_name=adjudication.food_safety_officer,
                inspection_date=adjudication.First_inspection_date,
            ).first()
            if sample_case and (sample_case.is_substandard or sample_case.is_misbranded):
                return jsonify({"error": "Photo evidence not applicable for this violation type"}), 400

    # Validate required form fields
    required_fields = ["lat", "lng", "accuracy", "captured_at"]
    for field in required_fields:
        if field not in request.form:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    # Extract EXIF/GPS data if available
    exif_lat, exif_lng, exif_accuracy = _extract_exif_gps(file)

    # Prefer form values, fall back to EXIF, then to 0.0 defaults
    def _pick(form_key, fallback):
        val = request.form.get(form_key)
        if val is not None and str(val).strip() != "":
            return val
        if fallback is not None:
            return fallback
        return 0.0

    try:
        lat = float(_pick("lat", exif_lat))
        lng = float(_pick("lng", exif_lng))
        accuracy = float(_pick("accuracy", exif_accuracy if exif_accuracy is not None else 0.0))
    except ValueError:
        return jsonify({"error": "lat, lng, and accuracy must be valid floats"}), 400

    captured_at_str = request.form["captured_at"]

    # Parse captured_at from ISO string
    try:
        captured_at = datetime.fromisoformat(captured_at_str)
    except ValueError:
        return jsonify({"error": "captured_at must be a valid ISO format datetime string"}), 400

    # Generate image_id using uuid4
    image_id = str(uuid.uuid4())

    # Save uploaded file temporarily
    filename = secure_filename(file.filename)
    temp_dir = Path(current_app.instance_path) / "temp_uploads"
    os.makedirs(str(temp_dir), exist_ok=True)
    temp_path = temp_dir / f"{image_id}_{filename}"
    file.save(str(temp_path))

    # Insert PhotoEvidence row with inspection_id
    photo_evidence = PhotoEvidence(
        image_id=image_id,
        inspection_id=inspection_id,
        case_id=case_id or str(inspection_id),
        filepath=temp_path,
        raw_lat=lat,
        raw_lng=lng,
        accuracy=accuracy,
        captured_at=captured_at,
        uploaded_at=datetime.utcnow(),
        verification_status="PENDING",
        stamped=False,
    )

    try:
        db.session.add(photo_evidence)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        if temp_path.exists():
            os.remove(str(temp_path))
        return jsonify({"error": f"Failed to save photo evidence: {e!s}"}), 500

    # Log audit for upload received
    actor = request.remote_addr
    log_audit("photo", image_id, "UPLOAD_RECEIVED", actor, {"raw_lat": lat, "raw_lng": lng, "accuracy": accuracy})

    # Verify photo location
    result = verify_photo_location(lat, lng, accuracy, request.remote_addr, inspection)

    # Log audit for verification run
    log_audit("photo", image_id, "VERIFICATION_RUN", actor, result)

    # Process and stamp image
    try:
        filepath = process_and_stamp_image(
            file,
            result["locality"],
            captured_at_str,
            result["verification_status"],
            image_id,
            str(inspection_id),
        )
    except ValueError as exc:
        # Clean up the DB row since processing failed
        try:
            db.session.delete(photo_evidence)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify({"error": f"Image processing failed: {exc}"}), 400

    # Update PhotoEvidence row
    photo_evidence.locality = result["locality"]
    photo_evidence.ip_match = result["ip_match"]
    photo_evidence.distance_to_fbo_m = result["distance_to_fbo_m"]
    photo_evidence.verification_status = result["verification_status"]
    photo_evidence.filepath = filepath
    photo_evidence.stamped = True

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to update photo evidence: {e!s}"}), 500

    # Log audit for photo saved
    log_audit("photo", image_id, "PHOTO_SAVED", actor, {"filepath": filepath})

    # Trigger async OCR extraction on the stamped image
    if _OCR_AVAILABLE:
        try:
            ocr_task = run_ocr_extraction.delay(file_path=filepath)
            ocr_task_id = ocr_task.id
        except Exception as exc:
            current_app.logger.warning("Failed to enqueue OCR task: %s", exc)
            ocr_task_id = None
    else:
        ocr_task_id = None

    return (
        jsonify({
            "image_id": image_id,
            "verification_status": result["verification_status"],
            "ocr_task_id": ocr_task_id,
        }),
        201,
    )


# ============================================================================
# Photo evidence CRUD (R2/B2 storage + InspectionPhoto model)
# ============================================================================


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["POST"])
def upload_adjudication_photo(adjudication_id):
    """Upload a photo for an adjudication via R2/B2 storage."""
    adjudication = Adjudication.query.get(adjudication_id)
    if not adjudication:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    if "photo" not in request.files:
        return jsonify({"error": 'No photo file provided. Use field name "photo".'}), 400

    file = request.files["photo"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    # TIER 1.1: Sanitize filename and validate extension server-side (defense in depth)
    original_filename = file.filename
    safe_filename = secure_filename(original_filename)
    if not safe_filename:
        return jsonify({"error": "Invalid filename."}), 400

    allowed_extensions = {"jpg", "jpeg", "png", "webp", "heic"}
    ext = Path(safe_filename).suffix.lower().lstrip(".")
    if ext not in allowed_extensions:
        return (
            jsonify({
                "error": f"Unsupported file extension '.{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
            }),
            400,
        )

    try:
        file_url = upload_photo(file, adjudication_id, safe_filename)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        current_app.logger.exception(f"Failed to upload photo for adjudication {adjudication_id}")
        return jsonify({"error": "Storage service unavailable."}), 502

    caption = request.form.get("caption", "").strip()

    photo = InspectionPhoto(
        adjudication_id=adjudication_id,
        file_url=file_url,
        caption=caption or None,
    )
    try:
        db.session.add(photo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Best-effort cleanup of the uploaded object
        with contextlib.suppress(Exception):
            delete_photo(file_url)
        current_app.logger.exception(f"Failed to save InspectionPhoto for adjudication {adjudication_id}")
        return jsonify({"error": "Database error."}), 500

    return (
        jsonify({
            "id": photo.id,
            "file_url": photo.file_url,
            "caption": photo.caption,
            "uploaded_at": photo.uploaded_at.isoformat() if photo.uploaded_at else None,
        }),
        201,
    )


@inspection_bp.route("/photos/<int:photo_id>", methods=["DELETE"])
def delete_adjudication_photo(photo_id):
    """Delete a photo from storage and remove its DB record."""
    photo = InspectionPhoto.query.get(photo_id)
    if not photo:
        return jsonify({"error": f"Photo with id {photo_id} not found"}), 404

    # Delete from storage; log warning but do not block on failure
    if not delete_photo(photo.file_url):
        current_app.logger.warning(
            "Storage delete returned False for photo %s (url=%s); proceeding with DB delete",
            photo_id,
            photo.file_url,
        )

    try:
        db.session.delete(photo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(f"Failed to delete photo record {photo_id}")
        return jsonify({"error": "Database error."}), 500

    return "", 204


@inspection_bp.route("/<int:adjudication_id>/photos", methods=["GET"])
def list_adjudication_photos(adjudication_id):
    """List all photos for an adjudication, ordered by upload time. Supports pagination."""
    adjudication = Adjudication.query.get(adjudication_id)
    if not adjudication:
        return jsonify({"error": f"Adjudication with id {adjudication_id} not found"}), 404

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    per_page = min(per_page, 200)  # Cap max per_page

    paginated = (
        InspectionPhoto.query
        .filter_by(adjudication_id=adjudication_id)
        .order_by(InspectionPhoto.uploaded_at.asc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    return jsonify({
        "items": [
            {
                "id": p.id,
                "file_url": p.file_url,
                "caption": p.caption,
                "uploaded_at": p.uploaded_at.isoformat() if p.uploaded_at else None,
            }
            for p in paginated.items
        ],
        "page": paginated.page,
        "per_page": paginated.per_page,
        "total": paginated.total,
    })
