import json

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy.orm.exc import StaleDataError

from app.bill_generator.utils import get_billable_samples, mark_samples_as_billed
from app.extensions import db
from app.models import Bill, FboIssue
from app.services.sheets_sync import sync_to_sheets
from app.utils.filters import parse_date

bill_generator_bp = Blueprint("bill_generator", __name__, template_folder="templates", static_folder="static")


@bill_generator_bp.route("/")
def index():
    return render_template("bill_generator/index.html")


@bill_generator_bp.route("/lookup_fbo_issues", methods=["GET"])
def lookup_fbo_issues():
    """
    Lookup FBO issues by fbo_id to provide pre-fill options for bill generation.
    Returns open and permission_granted issues that can be used to pre-fill bill forms.
    Query params: fbo_id (required), issue_id (optional - specific issue lookup)
    """
    fbo_id = request.args.get("fbo_id")
    issue_id = request.args.get("issue_id")

    if not fbo_id and not issue_id:
        return jsonify({"error": "Either fbo_id or issue_id is required"}), 400

    query = FboIssue.query.filter(FboIssue.state.in_(["open", "permission_granted"]))

    if issue_id:
        # Specific issue lookup by ID
        query = query.filter_by(id=int(issue_id))
    elif fbo_id:
        # Lookup all issues for this FBO
        query = query.filter_by(fbo_id=fbo_id)

    issues = query.order_by(FboIssue.created_at.desc()).all()

    result = []
    for issue in issues:
        # Parse detail_json
        detail = None
        if issue.detail_json:
            try:
                detail = json.loads(issue.detail_json)
            except Exception:
                detail = issue.detail_json

        # Extract relevant pre-fill data for billing
        prefill_data = {
            "issue_id": issue.id,
            "fbo_id": issue.fbo_id,
            "manufacturer_fbo_id": issue.manufacturer_fbo_id,
            "fbo_name": issue.fbo_name,
            "source_type": issue.source_type,
            "state": issue.state,
            "fso_name": issue.fso_name,
            "created_at": issue.created_at,
            "detail": detail,
        }

        # Add source-specific prefill mappings for bill form fields
        if issue.source_type == "sample" and detail:
            # For sample issues, we can pre-fill sample-related billing fields
            prefill_data["prefill"] = {
                "Name": issue.fbo_name,  # FBO name as the primary name
                "EMP_ID": issue.fso_name,  # FSO name as default
                "Designation": "Food Safety Officer",
                "sample_code": detail.get("sample_code"),
                "sample_name": detail.get("sample_name"),
                "price": detail.get("price"),
                "sampling_date": detail.get("sampling_date"),
            }
            # If there's a manufacturer, they might be the bill recipient
            if issue.manufacturer_fbo_id:
                prefill_data["prefill"]["manufacturer_fbo_id"] = issue.manufacturer_fbo_id
        elif issue.source_type == "inspection" and detail:
            # For inspection issues, pre-fill with general info
            prefill_data["prefill"] = {
                "Name": issue.fbo_name,
                "EMP_ID": issue.fso_name,
                "Designation": "Food Safety Officer",
                "inspection_details": ", ".join(detail.get("checklist", [])),
            }
        else:
            # Generic pre-fill
            prefill_data["prefill"] = {
                "Name": issue.fbo_name,
                "EMP_ID": issue.fso_name,
                "Designation": "Food Safety Officer",
            }

        result.append(prefill_data)

    return jsonify(result), 200


@bill_generator_bp.route("/bill/preview", methods=["GET"])
def bill_preview():
    """
    Preview bill for a date range.
    Query params: start, end (ISO date strings YYYY-MM-DD)
    """
    start = request.args.get("start")
    end = request.args.get("end")

    # Validate
    if not start or not end:
        return jsonify({"error": "Both start and end dates are required"}), 400

    if end < start:
        return jsonify({"error": "End date must be >= start date"}), 400

    try:
        result = get_billable_samples(start, end)
        return jsonify(result), 200
    except Exception as e:
        current_app.logger.error("Bill preview error: %s", e)
        return jsonify({"error": str(e)}), 500


@bill_generator_bp.route("/generate_bill", methods=["POST"])
def generate_bill_route():
    form_data = request.form.to_dict()

    # Get date range and recompute from samples
    start_date = form_data.get("start_date")
    end_date = form_data.get("end_date")

    if not start_date or not end_date:
        return jsonify({"error": "Both start and end dates are required"}), 400

    if end_date < start_date:
        return jsonify({"error": "End date must be >= start date"}), 400

    # Get billable samples and recompute server-side
    sample_data = get_billable_samples(start_date, end_date)

    # Create bill record with server-computed values
    total_amount = sample_data["enforcement_price"] + sample_data["surveillance_price"]
    bill_record = Bill(
        Name=form_data.get("Name", ""),
        EMP_ID=form_data.get("EMP_ID", ""),
        Designation=form_data.get("Designation", "Food Safety Officer"),
        Enf_samp_No=sample_data["enforcement_no"],
        Surv_samp_No=sample_data["surveillance_no"],
        enforcement_price=sample_data["enforcement_price"],
        surveillance_price=sample_data["surveillance_price"],
        Total_bill=str(total_amount),
        No_of_enfbills=form_data.get("No_of_enfbills", ""),
        No_of_survbills=form_data.get("No_of_survbills", ""),
        TR_Value=form_data.get("TR_Value", ""),
        TR_date=parse_date(form_data.get("TR_date", "")),
        Submission_date=parse_date(form_data.get("Submission_date", "")),
        start_date=parse_date(start_date),
        end_date=parse_date(end_date),
    )

    db.session.add(bill_record)
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        return jsonify({"error": "This bill was modified by another user. Please reload and try again."}), 409

    # Mark samples as billed and link to bill
    actual_sample_ids = [s["sample_id"] for s in sample_data["samples"]]
    mark_samples_as_billed(actual_sample_ids, bill_record.id)

    # Try syncing to Google Sheets (new module-based sync)
    try:
        row_dict = {k: v for k, v in form_data.items() if k in bill_record.__dict__}
        row_dict["created_at"] = bill_record.created_at.isoformat() if bill_record.created_at else ""
        success = sync_to_sheets("billing", row_dict)
        if not success:
            current_app.logger.warning("Bill Generator: Sheets sync returned False - sync failed but not blocking")
    except Exception as e:
        current_app.logger.warning(f"Bill Generator: Sheets sync failed: {e}")

    # Build template variables for synchronous PDF generation
    _ALLOWED_TEMPLATE_VARS = {
        "Name",
        "EMP_ID",
        "Designation",
        "Enf_samp_No",
        "Surv_samp_No",
        "Total_bill",
        "No_of_enfbills",
        "No_of_survbills",
        "TR_Value",
        "TR_date",
        "Submission_date",
        "start_date",
        "end_date",
        "enforcement_price",
        "surveillance_price",
    }
    template_vars = {k: form_data.get(k, "") for k in _ALLOWED_TEMPLATE_VARS}

    # Reverted to synchronous execution (.apply()) — no worker currently deployed.
    # Switch to .delay() once a persistent Celery worker is available.
    from app.bill_generator.tasks import generate_bill_pdf

    try:
        result = generate_bill_pdf.apply(
            kwargs=dict(bill_id=bill_record.id, template_vars=template_vars),
        ).result
    except Exception as exc:
        current_app.logger.error("Bill PDF generation failed: %s", exc)
        return jsonify({"error": f"Bill PDF generation failed: {exc}"}), 500

    # Unwrap task error metadata for consistent HTTP error responses
    # ponytail: handle case where result might be an exception object (e.g., OSError from WeasyPrint import)
    if isinstance(result, Exception):
        current_app.logger.error("Bill PDF generation returned exception: %s", result)
        return jsonify({"error": f"Bill PDF generation failed: {result}"}), 500

    if isinstance(result, dict) and result.get("status") == "error":
        error_msg = result.get("error", "PDF generation failed")
        current_app.logger.error("Bill PDF generation returned error: %s", error_msg)
        return jsonify({"error": error_msg}), 500

    return jsonify({
        "message": "Bill created; PDF generated",
        "bill_id": bill_record.id,
        "pdf_result": result,
    }), 200
