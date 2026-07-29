"""Sample routes module.

Provides endpoints for Sample CRUD operations and UI.
"""

from datetime import datetime

from flask import current_app, jsonify, render_template, request

from app.extensions import db
from app.models import FSO, Sample

# Import the blueprint from __init__.py
from app.sample import sample_bp
from app.sample.sample_utils import generate_sample_code
from app.services.sheets_sync import sync_to_sheets
from app.utils.filters import parse_date
from app.utils.fso_data import get_all_fso_names
from app.utils.lookup import lookup_fssai

# Sample types: enforcement or surveillance only
SAMPLE_TYPES = ["enforcement", "surveillance"]


@sample_bp.route("/")
def index():
    """Sample entry form page."""
    fso_names = get_all_fso_names()
    return render_template("sample/index.html", fso_names=fso_names, sample_types=SAMPLE_TYPES)


@sample_bp.route("/list")
def list_samples():
    """List all samples with pagination, sorting, and filtering."""
    # Get query parameters
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    sort_by = request.args.get("sort_by", "collection_date")
    sort_order = request.args.get("sort_order", "desc")
    filter_fso = request.args.get("fso_name")
    filter_date_from = request.args.get("collection_date_from")
    filter_date_to = request.args.get("collection_date_to")

    # Base query
    query = Sample.query.join(FSO, Sample.fso_name == FSO.fso_name)

    # Apply filters
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

    # Apply sorting
    if sort_by == "collection_date":
        if sort_order == "asc":
            query = query.order_by(Sample.collection_date.asc())
        else:
            query = query.order_by(Sample.collection_date.desc())
    elif sort_by == "fso_name":
        query = query.order_by(FSO.fso_name.asc()) if sort_order == "asc" else query.order_by(FSO.fso_name.desc())
    elif sort_by == "sample_code":
        if sort_order == "asc":
            query = query.order_by(Sample.sample_code.asc())
        else:
            query = query.order_by(Sample.sample_code.desc())
    else:
        query = query.order_by(Sample.collection_date.desc())

    # Paginate
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get all FSO names for filter dropdown
    all_fso_names = get_all_fso_names()

    return render_template(
        "sample/list.html",
        samples=paginated.items,
        pagination=paginated,
        fso_names=all_fso_names,
        sort_by=sort_by,
        sort_order=sort_order,
        filter_fso=filter_fso,
        filter_date_from=filter_date_from,
        filter_date_to=filter_date_to,
    )


@sample_bp.route("/lookup_retailer", methods=["POST"])
def lookup_retailer():
    """Lookup retailer information by FSSAI number."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    # Accept both old and new keys for backward compatibility during transition
    fssai_number = data.get("retailer_fssai_license", data.get("retailer_fssai", "")).strip()
    if not fssai_number:
        return jsonify({"error": "FSSAI number is required"}), 400

    # Use existing lookup function
    result, error = lookup_fssai(fssai_number)

    if error:
        return jsonify({"error": error}), 404

    if result:
        return jsonify({
            "companyName": result.get("companyName"),
            "fullAddress": result.get("fullAddress"),
            "expiryDate": result.get("expiryDate"),
            "source": result.get("source"),
        })

    return jsonify({"error": "Retailer not found"}), 404


@sample_bp.route("/create", methods=["POST"])
def create_sample():
    """Create a new sample record."""
    form_data = request.form.to_dict()

    # Required fields - using canonical keys from Step 2
    sample_name = form_data.get("sample_name", "").strip()
    food_safety_officer_name = form_data.get("food_safety_officer_name", "").strip()
    sample_draw_date = form_data.get("sample_draw_date", "").strip()

    if not sample_name:
        return jsonify({"error": "sample_name is required"}), 400
    if not food_safety_officer_name:
        return jsonify({"error": "food_safety_officer_name is required"}), 400
    if not sample_draw_date:
        return jsonify({"error": "sample_draw_date is required"}), 400

    # Validate sample_type is provided and valid
    sample_type_val = form_data.get("sample_type", "").strip()
    if not sample_type_val:
        return jsonify({"error": "sample_type is required"}), 400
    if sample_type_val not in ["enforcement", "surveillance"]:
        return jsonify({"error": f"sample_type must be 'enforcement' or 'surveillance', got '{sample_type_val}'"}), 400

    # Validate FSO exists - map canonical to DB column
    fso = FSO.query.get(food_safety_officer_name)
    if not fso:
        return jsonify({"error": f'FSO "{food_safety_officer_name}" not found in database'}), 400

    # Generate sample code
    sample_code = generate_sample_code()

    # Handle retailer autofill - using canonical keys
    retailer_fssai_license = form_data.get("retailer_fssai_license", "").strip()
    retailer_person_name = form_data.get("retailer_person_name", "").strip()

    # If retailer_fssai_license is provided but retailer_person_name is empty, try to autofill
    if retailer_fssai_license and not retailer_person_name:
        result, error = lookup_fssai(retailer_fssai_license)
        if result and not error:
            retailer_person_name = result.get("companyName", retailer_fssai_license)

    # Create sample record - map canonical to DB columns
    sample = Sample(
        sample_code=sample_code,
        sample_name=sample_name,
        sample_type=form_data.get("sample_type", "").strip() or None,
        fso_name=food_safety_officer_name,  # DB column: fso_name
        collection_date=parse_date(sample_draw_date),  # DB column: collection_date
        submission_date=parse_date(
            form_data.get("sample_submission_date", "").strip() or None,
        ),  # DB column: submission_date
        retailer_fssai=retailer_fssai_license or None,  # DB column: retailer_fssai
        retailer_name=retailer_person_name or None,  # DB column: retailer_name
        price=form_data.get("total_cost", "").strip() or None,  # DB column: price (canonical: total_cost)
        created_at=datetime.utcnow(),
    )

    try:
        db.session.add(sample)
        db.session.commit()

        # Sync to Google Sheets (Step 5)
        try:
            row_dict = {
                "id": sample.id,
                "sample_code": sample.sample_code,
                "sample_name": sample.sample_name,
                "sample_type": sample.sample_type or "",
                "fso_name": sample.fso_name,
                "collection_date": sample.collection_date,
                "submission_date": sample.submission_date or "",
                "retailer_fssai": sample.retailer_fssai or "",
                "retailer_name": sample.retailer_name or "",
                "price": sample.price or "",
                "created_at": sample.created_at.isoformat() if sample.created_at else "",
                "synced_at": "",
            }
            success = sync_to_sheets("sample_repo", row_dict)
            if success:
                # Update synced_at timestamp
                sample.synced_at = datetime.utcnow()
                db.session.commit()
        except Exception as e:
            current_app.logger.warning(f"Sample Sheets sync failed: {e}")

        return jsonify({
            "message": "Sample created successfully",
            "sample_id": sample.id,
            "sample_code": sample.sample_code,
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to create sample: {e!s}"}), 500


@sample_bp.route("/<int:sample_id>", methods=["GET"])
def get_sample(sample_id):
    """Get a specific sample by ID."""
    sample = Sample.query.get(sample_id)
    if not sample:
        return jsonify({"error": f"Sample with id {sample_id} not found"}), 404

    return jsonify({
        "id": sample.id,
        "sample_code": sample.sample_code,
        "sample_name": sample.sample_name,
        "sample_type": sample.sample_type,
        "fso_name": sample.fso_name,
        "collection_date": sample.collection_date.isoformat() if sample.collection_date else None,
        "submission_date": sample.submission_date.isoformat() if sample.submission_date else None,
        "retailer_fssai": sample.retailer_fssai,
        "retailer_name": sample.retailer_name,
        "price": sample.price,
        "created_at": sample.created_at.isoformat() if sample.created_at else None,
        "synced_at": sample.synced_at.isoformat() if sample.synced_at else None,
    })


@sample_bp.route("/<int:sample_id>", methods=["PUT"])
def update_sample(sample_id):
    """Update a sample record."""
    sample = Sample.query.get(sample_id)
    if not sample:
        return jsonify({"error": f"Sample with id {sample_id} not found"}), 404

    form_data = request.form.to_dict()

    # Update fields
    if "sample_name" in form_data:
        sample.sample_name = form_data["sample_name"].strip()
    if "sample_type" in form_data:
        sample.sample_type = form_data["sample_type"].strip() or None
    if "sample_type" in form_data:
        sample_type_val = form_data["sample_type"].strip()
        if not sample_type_val:
            return jsonify({"error": "sample_type cannot be empty"}), 400
        if sample_type_val not in ["enforcement", "surveillance"]:
            return jsonify({
                "error": f"sample_type must be 'enforcement' or 'surveillance', got '{sample_type_val}'",
            }), 400
        sample.sample_type = sample_type_val

    if "fso_name" in form_data:
        fso_name = form_data["fso_name"].strip()
        # Validate FSO exists
        fso = FSO.query.get(fso_name)
        if not fso:
            return jsonify({"error": f'FSO "{fso_name}" not found'}), 400
        sample.fso_name = fso_name
    if "collection_date" in form_data:
        sample.collection_date = parse_date(form_data["collection_date"].strip())
    if "submission_date" in form_data:
        sample.submission_date = parse_date(form_data["submission_date"].strip() or None)
    if "retailer_fssai" in form_data:
        sample.retailer_fssai = form_data["retailer_fssai"].strip() or None
    if "retailer_name" in form_data:
        sample.retailer_name = form_data["retailer_name"].strip() or None
    if "price" in form_data:
        sample.price = form_data["price"].strip() or None

    try:
        db.session.commit()

        # Sync to Google Sheets (Step 5)
        try:
            row_dict = {
                "id": sample.id,
                "sample_code": sample.sample_code,
                "sample_name": sample.sample_name,
                "sample_type": sample.sample_type or "",
                "fso_name": sample.fso_name,
                "collection_date": sample.collection_date,
                "submission_date": sample.submission_date or "",
                "retailer_fssai": sample.retailer_fssai or "",
                "retailer_name": sample.retailer_name or "",
                "price": sample.price or "",
                "created_at": sample.created_at.isoformat() if sample.created_at else "",
                "synced_at": datetime.utcnow().isoformat(),
            }
            sync_to_sheets("sample_repo", row_dict)
        except Exception as e:
            current_app.logger.warning(f"Sample Sheets sync failed: {e}")

        return jsonify({"message": "Sample updated successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to update sample: {e!s}"}), 500


@sample_bp.route("/<int:sample_id>", methods=["DELETE"])
def delete_sample(sample_id):
    """Delete a sample record."""
    sample = Sample.query.get(sample_id)
    if not sample:
        return jsonify({"error": f"Sample with id {sample_id} not found"}), 404

    try:
        db.session.delete(sample)
        db.session.commit()
        return jsonify({"message": "Sample deleted successfully"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete sample: {e!s}"}), 500
