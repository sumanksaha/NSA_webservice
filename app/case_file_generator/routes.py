"""Case File Generator blueprint — thin routes + DocumentCaseManager.

Common CRUD, editor, xref/toc, and renumber routes are registered via
:class:`app.shared.document_case_manager.DocumentCaseManager`.  This file
retains only the case-file-specific helpers and routes:

- ``validate_case_file_form`` / ``get_applicable_sections`` / ``process_form_data``
- ``case_file_to_dict``
- ``lookup_fssai_route`` / ``lookup_sample`` / ``list_samples_for_datalist``
- ``generate_case_file_route`` (QStash async PDF dispatch)

Backward-compatible imports preserved for callers (tests, renderers, etc.).
"""

from datetime import datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from flask_login import login_required
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import csrf, db
from app.models import CaseFile, Sample
from app.services.sync_orchestrator import sync_row
from app.shared.case_keys import (
    DERIVED_APPLICABLE_SECTIONS,
    DERIVED_CASE_TRACK,
    DERIVED_SAME_ENTITY,
    DERIVED_SECTIONS_DISPLAY,
    DERIVED_VIOLATIONS,
)
from app.shared.context_derivers import (
    derive_applicable_sections_from_case_file,
    derive_same_entity,
    derive_sections_display,
)
from app.shared.document_case_manager import DocumentCaseManager
from app.utils.auth import admin_required
from app.utils.filters import format_date_indian, parse_date
from app.utils.lookup import lookup_fssai
from app.utils.qstash_client import make_dedup_key, publish_task

case_file_generator_bp = Blueprint("case_file_generator", __name__, template_folder="templates", static_folder="static")


_REQUIRED_FIELDS: dict[str, str] = {
    "case_number": "Case Number",
    "food_safety_officer_name": "Food Safety Officer Name",
    "authorization_date": "Authorization Date",
    "inspection_date": "Sample Draw Date",
    "inspection_time": "Sample Draw Time",
    "manufacturer_fssai": "Manufacturer FSSAI Number",
    "manufacturer_name": "Manufacturer Name",
    "manufacturer_fbo_name": "Manufacturer FBO Name",
    "manufacturer_address": "Manufacturer Address",
    "retailer_fssai": "Retailer FSSAI Number",
    "retailer_name": "Retailer Name",
    "retailer_fbo_name": "Retailer FBO Name",
    "retailer_address": "Retailer Address",
    "product_name": "Product Name",
    "batch_no": "Batch Number",
    "sample_quantity": "Sample Quantity",
    "packet_count": "Packet Count",
    "mfg_date": "Date of Manufacturing",
    "expiry_date": "Date of Expiry",
    "sample_code": "Sample Code",
    "lab_registration_no": "Lab Registration Number",
    "do_receipt_date": "DO Receipt Date",
    "analyst_report_no": "Analyst Report Number",
    "analyst_report_date": "Analyst Report Date",
    "directive_letter_no": "Directive Letter Number",
    "directive_letter_date": "Directive Letter Date",
    "retailer_report_receive_date": "Retailer Report Receive Date",
    "manufacturer_report_receive_date": "Manufacturer Report Receive Date",
}

_DATE_FIELDS: list[str] = [
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


def _parse_date(value: str) -> datetime | None:
    """Try to parse a YYYY-MM-DD date string; return None on failure."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def validate_case_file_form(form_data: dict) -> dict[str, str]:
    errors: dict[str, str] = {}
    for field, label in _REQUIRED_FIELDS.items():
        value = form_data.get(field, "")
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[field] = f"{label} is required."

    # --- Numeric validations ---
    packet_count = form_data.get("packet_count", "")
    if packet_count:
        try:
            if int(packet_count) <= 0:
                errors["packet_count"] = "Packet Count must be a positive number."
        except (TypeError, ValueError):
            errors["packet_count"] = "Packet Count must be a valid integer."

    total_cost = form_data.get("total_cost", "").strip()
    if total_cost:
        try:
            float(total_cost)
        except (TypeError, ValueError):
            errors["total_cost"] = "Total Cost must be a valid number."

    # --- Time format validation ---
    inspection_time = form_data.get("inspection_time", "").strip()
    if inspection_time:
        try:
            datetime.strptime(inspection_time, "%H:%M")
        except (TypeError, ValueError):
            errors["inspection_time"] = "Sample Draw Time must be in HH:MM format."

    # --- Date format validation ---
    parsed_dates: dict[str, datetime] = {}
    for field in _DATE_FIELDS:
        value = form_data.get(field, "")
        if not value:
            continue
        dt = _parse_date(value)
        if dt is None:
            errors[field] = f"{_REQUIRED_FIELDS.get(field, field)} must be a valid date."
        else:
            parsed_dates[field] = dt

    # --- Date ordering validation ---
    if "mfg_date" in parsed_dates and "expiry_date" in parsed_dates:
        if parsed_dates["mfg_date"] >= parsed_dates["expiry_date"]:
            errors["expiry_date"] = "Date of Expiry must be after Date of Manufacturing."

    if "do_receipt_date" in parsed_dates and "analyst_report_date" in parsed_dates:
        if parsed_dates["do_receipt_date"] > parsed_dates["analyst_report_date"]:
            errors["analyst_report_date"] = "Analyst Report Date must be on or after DO Receipt Date."
    return errors


def get_applicable_sections(form_data: dict) -> list:
    sections = []
    is_misbranded = form_data.get("is_misbranded") == "misbranded"
    is_substandard = form_data.get("is_substandard") == "substandard"
    if is_substandard:
        sections.append("51")
    if is_misbranded:
        sections.append("52")
    return sorted(sections)


def process_form_data(form_data):
    date_fields = _DATE_FIELDS
    case_data = {}
    for key, value in form_data.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if key in date_fields and isinstance(value, str):
            try:
                dt = datetime.strptime(value, "%Y-%m-%d")
                case_data[key] = dt.strftime("%d/%m/%Y")
            except ValueError:
                case_data[key] = value
        else:
            case_data[key] = value

    is_misbranded = form_data.get("is_misbranded") == "misbranded"
    is_substandard = form_data.get("is_substandard") == "substandard"
    case_data["is_misbranded"] = is_misbranded
    case_data["is_substandard"] = is_substandard

    if is_misbranded and is_substandard:
        case_data["analysis_result"] = "misbranded and substandard"
    elif is_misbranded:
        case_data["analysis_result"] = "misbranded"
    elif is_substandard:
        case_data["analysis_result"] = "substandard"
    else:
        case_data["analysis_result"] = ""

    applicable_sections = derive_applicable_sections_from_case_file(
        is_substandard=is_substandard,
        is_misbranded=is_misbranded,
    )
    case_data["applicable_sections"] = applicable_sections
    case_data["applicable_sections_str"] = " and ".join(applicable_sections)
    case_data[DERIVED_APPLICABLE_SECTIONS] = applicable_sections
    case_data[DERIVED_SECTIONS_DISPLAY] = derive_sections_display(applicable_sections)
    case_data[DERIVED_CASE_TRACK] = "sample"
    case_data[DERIVED_VIOLATIONS] = []

    manufacturer_fssai = case_data.get("manufacturer_fssai", "").strip()
    retailer_fssai = case_data.get("retailer_fssai", "").strip()
    same_entity = derive_same_entity(manufacturer_fssai, retailer_fssai)
    case_data["same_entity"] = same_entity
    case_data[DERIVED_SAME_ENTITY] = same_entity

    for field in date_fields:
        if field in case_data:
            case_data[field] = format_date_indian(case_data[field])

    if "cost_in_words" not in case_data or not case_data["cost_in_words"]:
        total_cost = case_data.get("total_cost", "0")
        try:
            from app.utils.filters import to_words

            case_data["cost_in_words"] = to_words(total_cost) + " Only"
        except Exception:
            case_data["cost_in_words"] = ""

    return case_data


def case_file_to_dict(case_file):
    """Convert a CaseFile model instance to a dictionary for JSON serialization.

    Keys match the model column names so templates can use them directly
    without aliasing.
    """
    return {
        "id": case_file.id,
        "case_number": case_file.case_number,
        "food_safety_officer_name": case_file.food_safety_officer_name,
        "authorization_date": case_file.authorization_date.isoformat() if case_file.authorization_date else None,
        "inspection_date": (case_file.inspection_date.isoformat() if case_file.inspection_date else None),
        "inspection_time": case_file.inspection_time,
        "sample_id": case_file.sample_id,
        "manufacturer_fssai": case_file.manufacturer_fssai,
        "manufacturer_name": case_file.manufacturer_name,
        "manufacturer_fbo_name": case_file.manufacturer_fbo_name,
        "manufacturer_address": case_file.manufacturer_address,
        "retailer_fssai": case_file.retailer_fssai,
        "retailer_name": case_file.retailer_name,
        "retailer_fbo_name": case_file.retailer_fbo_name,
        "retailer_address": case_file.retailer_address,
        "product_name": case_file.product_name,
        "batch_no": case_file.batch_no,
        "sample_quantity": case_file.sample_quantity,
        "packet_count": case_file.packet_count,
        "mfg_date": case_file.mfg_date.isoformat() if case_file.mfg_date else None,
        "expiry_date": case_file.expiry_date.isoformat() if case_file.expiry_date else None,
        "other_food_articles": case_file.other_food_articles,
        "total_cost": case_file.total_cost,
        "cost_in_words": case_file.cost_in_words,
        "sample_code": case_file.sample_code,
        "lab_registration_no": case_file.Lab_Registration_No,
        "do_receipt_date": case_file.do_receipt_date.isoformat() if case_file.do_receipt_date else None,
        "is_misbranded": "misbranded" if case_file.is_misbranded else "",
        "is_substandard": "substandard" if case_file.is_substandard else "",
        "analyst_report_no": case_file.analyst_report_no,
        "analyst_report_date": case_file.analyst_report_date.isoformat() if case_file.analyst_report_date else None,
        "directive_letter_no": case_file.directive_letter_no,
        "directive_letter_date": (
            case_file.directive_letter_date.isoformat() if case_file.directive_letter_date else None
        ),
        "retailer_report_receive_date": (
            case_file.retailer_report_receive_date.isoformat() if case_file.retailer_report_receive_date else None
        ),
        "manufacturer_report_receive_date": (
            case_file.manufacturer_report_receive_date.isoformat()
            if case_file.manufacturer_report_receive_date
            else None
        ),
        "applicable_regulation": case_file.applicable_regulation,
        "applicable_clause": case_file.applicable_clause,
        "applicable_sections": case_file.applicable_sections,
        "created_at": case_file.created_at.isoformat() if case_file.created_at else None,
        "synced_at": case_file.synced_at.isoformat() if case_file.synced_at else None,
    }


def _process_case_file_form(form_data):
    """Create a CaseFile model instance from validated form data."""
    sample_id = None
    import contextlib

    with contextlib.suppress(ValueError):
        sample_id = int(form_data.get("sample_id", "")) if form_data.get("sample_id") else None

    packet_count = int(form_data.get("packet_count", 4))

    return CaseFile(
        case_number=form_data.get("case_number", ""),
        food_safety_officer_name=form_data.get("food_safety_officer_name", ""),
        authorization_date=parse_date(form_data.get("authorization_date", "")),
        inspection_date=parse_date(form_data.get("inspection_date", "")),
        inspection_time=form_data.get("inspection_time", ""),
        sample_id=sample_id,
        manufacturer_fssai=form_data.get("manufacturer_fssai", ""),
        manufacturer_name=form_data.get("manufacturer_name", ""),
        manufacturer_fbo_name=form_data.get("manufacturer_fbo_name", ""),
        manufacturer_address=form_data.get("manufacturer_address", ""),
        retailer_fssai=form_data.get("retailer_fssai", ""),
        retailer_name=form_data.get("retailer_name", ""),
        retailer_fbo_name=form_data.get("retailer_fbo_name", ""),
        retailer_address=form_data.get("retailer_address", ""),
        product_name=form_data.get("product_name", ""),
        batch_no=form_data.get("batch_no", ""),
        sample_quantity=form_data.get("sample_quantity", ""),
        packet_count=packet_count,
        mfg_date=parse_date(form_data.get("mfg_date", "")),
        expiry_date=parse_date(form_data.get("expiry_date", "")),
        other_food_articles=form_data.get("other_food_articles", ""),
        total_cost=form_data.get("total_cost", ""),
        cost_in_words=form_data.get("cost_in_words", ""),
        sample_code=form_data.get("sample_code", ""),
        Lab_Registration_No=form_data.get("lab_registration_no", ""),
        sample_submission_date=parse_date(form_data.get("do_receipt_date", "")),  # merged into do_receipt_date
        do_receipt_date=parse_date(form_data.get("do_receipt_date", "")),
        is_misbranded=form_data.get("is_misbranded") == "misbranded",
        is_substandard=form_data.get("is_substandard") == "substandard",
        analyst_report_no=form_data.get("analyst_report_no", ""),
        analyst_report_date=parse_date(form_data.get("analyst_report_date", "")),
        directive_letter_no=form_data.get("directive_letter_no", ""),
        directive_letter_date=parse_date(form_data.get("directive_letter_date", "")),
        retailer_report_receive_date=parse_date(form_data.get("retailer_report_receive_date", "")),
        manufacturer_report_receive_date=parse_date(form_data.get("manufacturer_report_receive_date", "")),
        applicable_regulation=form_data.get("applicable_regulation", ""),
        applicable_clause=form_data.get("applicable_clause", ""),
        applicable_sections=", ".join(get_applicable_sections(form_data)),
    )


def _regenerate_case_file(case_id):
    """Regenerate both Petition and Permission Letter from an existing case."""
    case_file = CaseFile.query.get_or_404(case_id)
    form_data = case_file_to_dict(case_file)
    case_data = process_form_data(form_data)

    from app.utils.qstash_client import make_dedup_key, publish_task

    payload = {"case_file_id": case_file.id, "case_data": case_data}
    try:
        dispatched = publish_task(
            "generate_case_file_pdf",
            payload=payload,
            dedup_key=make_dedup_key("generate_case_file_pdf", case_file.id, payload),
        )
    except Exception as exc:
        current_app.logger.error("Case file PDF dispatch failed: %s", exc)
        return jsonify({"error": f"Case file PDF regeneration failed: {exc}"}), 500

    if dispatched["mode"] == "async":
        return (
            jsonify({
                "message": "Case file PDF regeneration queued",
                "case_file_id": case_file.id,
                "task_id": dispatched["message_id"],
            }),
            202,
        )

    result = dispatched["result"]
    if result.get("status") == "error":
        error_msg = result.get("error", "PDF regeneration failed")
        current_app.logger.error("Case file PDF regeneration returned error: %s", error_msg)
        return jsonify({"error": error_msg}), 500

    return (
        jsonify({
            "message": "Case file PDF regenerated",
            "case_file_id": case_file.id,
            "pdf_result": result,
        }),
        200,
    )


# --------------------------------------------------------------------------- #
# DocumentCaseManager — common routes delegation
# --------------------------------------------------------------------------- #

_manager = DocumentCaseManager(
    model=CaseFile,
    template_dir="case_file_generator",
    bp_name="case_file_generator",
    case_type="case_file",
    model_to_dict_fn=case_file_to_dict,
    process_form_fn=_process_case_file_form,
    validate_form_fn=validate_case_file_form,
    templates={
        "petition": "case_file_generator/petition.html",
        "permission": "case_file_generator/permission_letter.html",
    },
)
_manager.register_routes(case_file_generator_bp)


# --------------------------------------------------------------------------- #
# Model-specific routes (not covered by DocumentCaseManager)
# --------------------------------------------------------------------------- #


@csrf.exempt
@case_file_generator_bp.route("/lookup_fssai", methods=["POST"])
def lookup_fssai_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    result, error = lookup_fssai(license_no)
    if error:
        status_code = 400 if "required" in error or "prefix" in error else 404
        return jsonify({"error": error}), status_code
    return jsonify({"identity": result})


@case_file_generator_bp.route("/regenerate/<int:case_id>", methods=["GET"])
def regenerate_case_files(case_id):
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404
    return _regenerate_case_file(case_id)


@case_file_generator_bp.route("/preview", methods=["POST"])
def preview_case_file_route():
    """Render Petition + Permission Letter HTML from form data for review.

    Unlike ``generate_case_file_route``, this does NOT create a CaseFile
    record or dispatch a PDF task — it returns the rendered HTML so the
    user can review both documents in the Quill editor before committing.
    """
    form_data = request.form.to_dict()

    # Phase 18 RBAC: an fso-role account always owns what it creates — the
    # bound officer name overrides whatever the form submitted.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None:
        form_data["food_safety_officer_name"] = scope

    validation_errors = validate_case_file_form(form_data)
    if validation_errors:
        return (
            jsonify({
                "error": "Please correct the highlighted fields below.",
                "errors": validation_errors,
            }),
            400,
        )

    case_data = process_form_data(form_data)

    petition_html = str(render_template("case_file_generator/petition.html", **case_data))
    permission_html = str(render_template("case_file_generator/permission_letter.html", **case_data))

    # Phase 6+7: cross-reference pass (renumbering, enclosures, TOC).
    # No case_id available — photo/embed enrichment is skipped gracefully.
    from app.utils.pdf_utils import post_process_pdf_html

    petition_html = post_process_pdf_html(petition_html)
    permission_html = post_process_pdf_html(permission_html)

    return jsonify({
        "petition_html": petition_html,
        "permission_html": permission_html,
        "case_number": case_data.get("case_number", ""),
    })


@case_file_generator_bp.route("/generate_case_file", methods=["POST"])
def generate_case_file_route():
    form_data = request.form.to_dict()

    # Phase 18 RBAC: an fso-role account always owns what it creates — the
    # bound officer name overrides whatever the form submitted.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None:
        form_data["food_safety_officer_name"] = scope

    validation_errors = validate_case_file_form(form_data)
    if validation_errors:
        return (
            jsonify({
                "error": "Please correct the highlighted fields below.",
                "errors": validation_errors,
            }),
            400,
        )

    case_file_record = CaseFile(
        case_number=form_data.get("case_number", ""),
        food_safety_officer_name=form_data.get("food_safety_officer_name", ""),
        authorization_date=parse_date(form_data.get("authorization_date", "")),
        inspection_date=parse_date(form_data.get("inspection_date", "")),
        inspection_time=form_data.get("inspection_time", ""),
        sample_id=int(form_data["sample_id"]) if form_data.get("sample_id") else None,
        manufacturer_fssai=form_data.get("manufacturer_fssai", ""),
        manufacturer_name=form_data.get("manufacturer_name", ""),
        manufacturer_fbo_name=form_data.get("manufacturer_fbo_name", ""),
        manufacturer_address=form_data.get("manufacturer_address", ""),
        retailer_fssai=form_data.get("retailer_fssai", ""),
        retailer_name=form_data.get("retailer_name", ""),
        retailer_fbo_name=form_data.get("retailer_fbo_name", ""),
        retailer_address=form_data.get("retailer_address", ""),
        product_name=form_data.get("product_name", ""),
        batch_no=form_data.get("batch_no", ""),
        sample_quantity=form_data.get("sample_quantity", ""),
        packet_count=int(form_data.get("packet_count", 4)),
        mfg_date=parse_date(form_data.get("mfg_date", "")),
        expiry_date=parse_date(form_data.get("expiry_date", "")),
        other_food_articles=form_data.get("other_food_articles", ""),
        total_cost=form_data.get("total_cost", ""),
        cost_in_words=form_data.get("cost_in_words", ""),
        sample_code=form_data.get("sample_code", ""),
        Lab_Registration_No=form_data.get("lab_registration_no", ""),
        sample_submission_date=parse_date(form_data.get("do_receipt_date", "")),  # merged into do_receipt_date
        do_receipt_date=parse_date(form_data.get("do_receipt_date", "")),
        is_misbranded=form_data.get("is_misbranded") == "misbranded",
        is_substandard=form_data.get("is_substandard") == "substandard",
        analyst_report_no=form_data.get("analyst_report_no", ""),
        analyst_report_date=parse_date(form_data.get("analyst_report_date", "")),
        directive_letter_no=form_data.get("directive_letter_no", ""),
        directive_letter_date=parse_date(form_data.get("directive_letter_date", "")),
        retailer_report_receive_date=parse_date(form_data.get("retailer_report_receive_date", "")),
        manufacturer_report_receive_date=parse_date(form_data.get("manufacturer_report_receive_date", "")),
        applicable_regulation=form_data.get("applicable_regulation", ""),
        applicable_clause=form_data.get("applicable_clause", ""),
        applicable_sections=", ".join(get_applicable_sections(form_data)),
    )

    db.session.add(case_file_record)
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        return jsonify({"error": "This case file was modified by another user. Please reload and try again."}), 409

    allowed_sheets_columns = set(_REQUIRED_FIELDS.keys()) | {
        "is_misbranded",
        "is_substandard",
        "applicable_regulation",
        "applicable_clause",
        "applicable_sections",
    }
    try:
        row_dict = {k: v for k, v in form_data.items() if k in allowed_sheets_columns}
        row_dict["created_at"] = case_file_record.created_at.isoformat() if case_file_record.created_at else ""
        row_dict["applicable_sections"] = case_file_record.applicable_sections
        row_dict["sample_id"] = case_file_record.sample_id
        result = sync_row("sample", row_dict, entity_id=case_file_record.id)
        if not result["sheets"]:
            current_app.logger.warning("Case File: Sheets sync failed - sync failed but not blocking")
    except Exception as e:
        current_app.logger.warning(f"Case File sync failed: {e}")

    case_data = process_form_data(form_data)
    payload = {"case_file_id": case_file_record.id, "case_data": case_data}
    try:
        dispatched = publish_task(
            "generate_case_file_pdf",
            payload=payload,
            dedup_key=make_dedup_key("generate_case_file_pdf", case_file_record.id, payload),
        )
    except Exception as exc:
        current_app.logger.error("Case file PDF dispatch failed: %s", exc)
        return jsonify({"error": f"Case file PDF generation failed: {exc}"}), 500

    if dispatched["mode"] == "async":
        return (
            jsonify({
                "message": "Case file created; PDF generation queued",
                "case_file_id": case_file_record.id,
                "task_id": dispatched["message_id"],
            }),
            202,
        )

    result = dispatched["result"]
    if result.get("status") == "error":
        error_msg = result.get("error", "PDF generation failed")
        current_app.logger.error("Case file PDF generation returned error: %s", error_msg)
        return jsonify({"error": error_msg}), 500

    return (
        jsonify({
            "message": "Case file created; PDF generated",
            "case_file_id": case_file_record.id,
            "pdf_result": result,
        }),
        200,
    )


@case_file_generator_bp.route("/lookup_sample", methods=["GET"])
def lookup_sample():
    """Lookup sample by sample_code for CaseFile prefill."""
    sample_code = request.args.get("sample_code", "").strip()
    if not sample_code:
        return jsonify({"error": "sample_code is required"}), 400

    sample = Sample.query.filter_by(sample_code=sample_code).first()
    if not sample:
        return jsonify({"error": f"Sample with code {sample_code} not found"}), 404

    return jsonify({
        "id": sample.id,
        "sample_code": sample.sample_code,
        "product_name": sample.sample_name or "",
        "retailer_fssai": sample.retailer_fssai or "",
        "retailer_name": sample.retailer_name or "",
        "total_cost": sample.price or "",
    })


@case_file_generator_bp.route("/samples", methods=["GET"])
def list_samples_for_datalist():
    """List all samples for datalist dropdown (returns sample codes only). Supports pagination."""
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 100, type=int)
    per_page = min(per_page, 500)

    paginated = Sample.query.order_by(Sample.sample_code.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        "sample_codes": [s.sample_code for s in paginated.items],
        "page": paginated.page,
        "per_page": paginated.per_page,
        "total": paginated.total,
    })


# ---------------------------------------------------------------------------
# Phase 16 — case export / import HTTP endpoints
# ---------------------------------------------------------------------------


def _case_type_from_args() -> str:
    """Read ``?case_type=`` (default ``case_file``) from the query string."""
    return request.args.get("case_type", "case_file")


def _case_visible_to_current_user(case_id: int, case_type: str) -> bool:
    """Phase 18 record-level scope for module-level case routes."""
    from flask_login import current_user

    from app.shared.rbac import case_visible_to_user

    return case_visible_to_user(current_user, case_type, case_id)


@case_file_generator_bp.route("/api/cases/<int:case_id>/export.json", methods=["GET"])
@login_required
def export_case_json_route(case_id: int):
    """Full JSON export of a case + annexures + evidence + versions."""
    from app.case_file_generator.services import export_case_as_json

    if not _case_visible_to_current_user(case_id, _case_type_from_args()):
        return jsonify({"error": "Case not found"}), 404
    try:
        data = export_case_as_json(case_id, _case_type_from_args())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify(data), 200


@case_file_generator_bp.route("/api/cases/<int:case_id>/export.zip", methods=["GET"])
@login_required
def export_case_zip_route(case_id: int):
    """ZIP export: JSON manifest + compiled PDFs + annexure/evidence files."""
    import io

    from flask import send_file

    from app.case_file_generator.services import export_case_as_zip

    case_type = _case_type_from_args()
    if not _case_visible_to_current_user(case_id, case_type):
        return jsonify({"error": "Case not found"}), 404
    try:
        zip_bytes = export_case_as_zip(case_id, case_type)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    return send_file(
        io.BytesIO(zip_bytes),
        as_attachment=True,
        download_name=f"case_{case_id}_{case_type}_export.zip",
        mimetype="application/zip",
    )


@case_file_generator_bp.route("/api/cases/import", methods=["POST"])
@login_required
@admin_required
def import_case_route():
    """Import a case-export JSON (multipart ``file`` field) as a new case."""
    import json as json_mod

    from app.case_file_generator.services import import_case_from_json

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"error": "Please upload a case export JSON file."}), 400
    try:
        json_data = json_mod.load(upload.stream)
    except (ValueError, UnicodeDecodeError) as exc:
        return jsonify({"error": f"Invalid JSON upload: {exc}"}), 400
    if not isinstance(json_data, dict):
        return jsonify({"error": "Upload must be a JSON object."}), 400

    try:
        new_case_id = import_case_from_json(json_data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"new_case_id": new_case_id}), 201


# ---------------------------------------------------------------------------
# Word (.docx) download routes
# ---------------------------------------------------------------------------


@case_file_generator_bp.route("/case/<int:case_id>/docx/petition")
@login_required
def download_petition_docx(case_id: int):
    """Download the Petition as a Word (.docx) document."""
    case = CaseFile.query.get_or_404(case_id)
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404

    from app.case_file_generator.word_converter import CaseFileWordConverter

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)
    converter = CaseFileWordConverter()
    docx_bytes = converter.build_petition(case_data)

    import io as _io

    buf = _io.BytesIO(docx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"Petition_{case.case_number or case_id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@case_file_generator_bp.route("/case/<int:case_id>/docx/permission")
@login_required
def download_permission_docx(case_id: int):
    """Download the Permission Letter as a Word (.docx) document."""
    case = CaseFile.query.get_or_404(case_id)
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404

    from app.case_file_generator.word_converter import CaseFileWordConverter

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)
    converter = CaseFileWordConverter()
    docx_bytes = converter.build_permission_letter(case_data)

    import io as _io

    buf = _io.BytesIO(docx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"Permission_Letter_{case.case_number or case_id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@case_file_generator_bp.route("/case/<int:case_id>/docx/zip")
@login_required
def download_both_docx(case_id: int):
    """Download both Petition + Permission Letter as a single ZIP of .docx files."""
    case = CaseFile.query.get_or_404(case_id)
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404

    import io as _io
    import zipfile

    from app.case_file_generator.word_converter import CaseFileWordConverter

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)
    converter = CaseFileWordConverter()

    petition_docx = converter.build_petition(case_data)
    permission_docx = converter.build_permission_letter(case_data)

    label = case.case_number or str(case_id)
    zip_buf = _io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Petition_{label}.docx", petition_docx)
        zf.writestr(f"Permission_Letter_{label}.docx", permission_docx)
    zip_buf.seek(0)
    return send_file(
        zip_buf,
        as_attachment=True,
        download_name=f"Case_File_{label}_Word.zip",
        mimetype="application/zip",
    )


# ---------------------------------------------------------------------------
# Copy Letter — returns rendered HTML for copy-paste into Gmail
# ---------------------------------------------------------------------------


@case_file_generator_bp.route("/case/<int:case_id>/copy-letter/<doc_type>")
@login_required
def copy_letter(case_id: int, doc_type: str):
    """Return rendered HTML letter body for copy-paste into Gmail.

    ``doc_type`` is ``petition`` or ``permission``.
    """
    case = CaseFile.query.get_or_404(case_id)
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404

    if doc_type not in ("petition", "permission"):
        return jsonify({"error": "Invalid doc_type"}), 400

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)

    template_map = {
        "petition": "case_file_generator/petition.html",
        "permission": "case_file_generator/permission_letter.html",
    }
    html = str(render_template(template_map[doc_type], **case_data))

    from app.utils.pdf_utils import post_process_pdf_html

    html = post_process_pdf_html(html)

    return html, 200, {"Content-Type": "text/html; charset=utf-8"}
