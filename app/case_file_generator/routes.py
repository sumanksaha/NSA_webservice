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

import io
from datetime import date, datetime

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
from app.shared.generation_access import check_generation_allowed
from app.shared.rcm_policy import EXEMPT_DATE_FIELDS as _RCM_EXEMPT_DATE_FIELDS
from app.shared.rcm_policy import EXEMPT_FIELDS as _RCM_EXEMPT_FIELDS
from app.shared.rcm_policy import is_rcm as _rcm_policy_is_rcm
from app.shared.rcm_policy import stripped_for_save as _rcm_stripped_for_save
from app.utils.auth import admin_required
from app.utils.filters import form_date
from app.utils.filters import format_date_indian, parse_date
from app.utils.lookup import lookup_fssai
from app.utils.qstash_client import make_dedup_key, publish_task

from .adoc_renderer import render_docx

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


def _safe_int(value, default=None):
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


# Model-cased keys accepted as aliases of their canonical form keys.
# ``case_file_to_dict()`` / export payloads use model column names
# (e.g. ``Lab_Registration_No``) while forms, templates, and the canonical
# contract (``app.shared.case_keys``) use ``lab_registration_no``.
_FIELD_ALIASES: dict[str, str] = {
    "Lab_Registration_No": "lab_registration_no",
}


def _lookup_field(form_data: dict, field: str):
    """Return the value for *field*, falling back to model-cased aliases."""
    value = form_data.get(field, "")
    if value is None or (isinstance(value, str) and not value.strip()):
        for alias, canonical in _FIELD_ALIASES.items():
            if canonical == field:
                value = form_data.get(alias, "")
                if value is not None and (not isinstance(value, str) or value.strip()):
                    break
    return value


def _is_retailer_cum_manufacturer(form_data: dict) -> bool:
    """Is the case a Retailer-cum-Manufacturer loose food (no separate
    manufacturer, no batch/mfg/expiry)?

    Thin alias over the RCM policy seam (app/shared/rcm_policy.py);
    retained so existing importers keep working.
    """
    return _rcm_policy_is_rcm(form_data)


def _generation_gate_info(retailer_receive, manufacturer_receive) -> dict:
    """Non-blocking gate info for preview responses (warn, don't refuse)."""
    from app.timeline.engine import generation_gate

    gate = generation_gate(retailer_receive, manufacturer_receive)
    earliest = gate["earliest"]
    handover = gate["handover"]
    return {
        "generation_blocked": gate["blocked"],
        "generation_allowed_from": earliest.isoformat() if earliest else None,
        "handover_date": handover.isoformat() if handover else None,
    }


def validate_case_file_form(form_data: dict) -> dict[str, str]:
    rcm = _is_retailer_cum_manufacturer(form_data)
    errors: dict[str, str] = {}

    # --- Required fields ---
    # Standard required fields for all cases.
    # ``authorization_date`` is intentionally excluded: it is issued by the
    # Designated Officer when the permission file is submitted, i.e. after
    # first data entry.  It stays editable and gates petition generation.
    standard_required = tuple(
        field
        for field, label in _REQUIRED_FIELDS.items()
        if field not in ("authorization_date", *_RCM_EXEMPT_FIELDS)
    )
    for field, label in _REQUIRED_FIELDS.items():
        if field not in standard_required:
            continue
        value = _lookup_field(form_data, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[field] = f"{label} is required."

    # RCM cases: manufacturer identity is the retailer, and loose foods
    # have no batch/mfg/expiry dates. The missing manufacturer/batch/mfg/expiry
    # fields are therefore acceptable; manufacturer_report_receive_date is
    # also absent because no separate manufacturer is served the report.
    if not rcm:
        for field in sorted(_RCM_EXEMPT_FIELDS):
            label = _REQUIRED_FIELDS.get(field, field)
            if field == "mfg_date" or field == "expiry_date":
                value = form_data.get(field, "").strip()
                if not value:
                    errors[field] = f"{label} is required."
                continue
            value = _lookup_field(form_data, field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors[field] = f"{label} is required."

    # --- Numeric validations ---
    packet_count = form_data.get("packet_count", "")
    if packet_count not in (None, ""):
        try:
            pkt = int(packet_count)
            if pkt <= 0:
                errors["packet_count"] = "Packet Count must be a positive number."
        except (TypeError, ValueError):
            errors["packet_count"] = "Packet Count must be a valid integer."

    total_cost = (form_data.get("total_cost") or "").strip()
    if total_cost:
        try:
            float(total_cost)
        except (TypeError, ValueError):
            errors["total_cost"] = "Total Cost must be a valid number."

    # --- Time format validation ---
    inspection_time = (form_data.get("inspection_time") or "").strip()
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
        if rcm and field in _RCM_EXEMPT_DATE_FIELDS:
            # Stale hidden values on RCM cases: the server blanks these on
            # save, so they must not fail validation (this also keeps them
            # out of parsed_dates, skipping the future/ordering rules).
            continue
        dt = _parse_date(value)
        if dt is None:
            errors[field] = f"{_REQUIRED_FIELDS.get(field, field)} must be a valid date."
        else:
            parsed_dates[field] = dt

    # --- No-future-dates rule ---
    # These dates record events that already happened (authorization,
    # draws, receipts, reports). Expiry is excluded: it is normally in
    # the future (and is separately constrained to be after mfg_date).
    today = date.today()
    for field, dt in parsed_dates.items():
        if field != "expiry_date" and dt.date() > today:
            errors[field] = f"{_REQUIRED_FIELDS.get(field, field)} must not be a future date."

    # --- Date ordering validation ---
    # Only enforced when both dates are provided (e.g. non-RCM packaged food).
    if (
        "mfg_date" in parsed_dates
        and "expiry_date" in parsed_dates
        and parsed_dates["mfg_date"] >= parsed_dates["expiry_date"]
    ):
        errors["expiry_date"] = "Date of Expiry must be after Date of Manufacturing."

    if (
        "do_receipt_date" in parsed_dates
        and "analyst_report_date" in parsed_dates
        and parsed_dates["do_receipt_date"] > parsed_dates["analyst_report_date"]
    ):
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
        if key in date_fields:
            dt = parse_date(value)
            if dt is not None:
                case_data[key] = dt.strftime("%d-%m-%Y")
            else:
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
    _rcm = _is_retailer_cum_manufacturer(case_data)
    case_data["retailer_cum_manufacturer"] = _rcm
    case_data["same_entity"] = same_entity or _rcm
    case_data[DERIVED_SAME_ENTITY] = same_entity or _rcm

    for field in date_fields:
        if field in case_data:
            case_data[field] = format_date_indian(case_data[field])

    # Alias model-cased keys to their canonical form keys so templates
    # (which use ``lab_registration_no``) render on regenerate/docx/editor
    # paths built from ``case_file_to_dict()`` (which uses model columns).
    for alias, canonical in _FIELD_ALIASES.items():
        if alias in case_data and not case_data.get(canonical):
            case_data[canonical] = case_data[alias]

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

    Uses SQLAlchemy __table__.columns for the core fields, with
    date/computed fields handled separately.
    """
    cols = [c.name for c in CaseFile.__table__.columns]
    result = {c: getattr(case_file, c, None) for c in cols}

    # Date fields that need isoformat serialization
    for date_field in (
        "authorization_date",
        "inspection_date",
        "mfg_date",
        "expiry_date",
        "do_receipt_date",
        "analyst_report_date",
        "directive_letter_date",
        "retailer_report_receive_date",
        "manufacturer_report_receive_date",
        "archived_at",
        "created_at",
        "synced_at",
    ):
        val = result.get(date_field)
        result[date_field] = val.isoformat() if val else None

    # Boolean fields need string representation for templates
    result["is_misbranded"] = "misbranded" if result.get("is_misbranded") else ""
    result["is_substandard"] = "substandard" if result.get("is_substandard") else ""

    return result


def case_file_form_dict(case_file) -> dict:
    """Convert a CaseFile record to form-keyed values for the edit page."""
    record = case_file_to_dict(case_file)
    form = dict(record)
    for field in _DATE_FIELDS:
        form[field] = form_date(record.get(field))
    form["lab_registration_no"] = record.get("Lab_Registration_No") or ""
    form["sample_id"] = record.get("sample_id") or ""
    return form


def apply_case_file_update(case_file, form_data: dict) -> None:
    """Apply validated edit-form data onto an existing CaseFile (in place).

    ``case_number`` is never touched (immutable identity, enforced by the
    shared PUT route). All other entered fields are updatable.
    """
    case_file.food_safety_officer_name = form_data.get("food_safety_officer_name", "")
    case_file.authorization_date = parse_date(form_data.get("authorization_date", ""))
    case_file.inspection_date = parse_date(form_data.get("inspection_date", ""))
    case_file.inspection_time = form_data.get("inspection_time", "")
    sample_id_raw = (
        (form_data.get("sample_id") or "").strip()
        if isinstance(form_data.get("sample_id"), str)
        else form_data.get("sample_id")
    )
    case_file.sample_id = _safe_int(sample_id_raw) if sample_id_raw not in (None, "") else None
    case_file.retailer_cum_manufacturer = _is_retailer_cum_manufacturer(form_data)
    case_file.product_name = form_data.get("product_name", "")

    # RCM-exempt fields arrive blanked (by decision of the RCM policy
    # seam), so one unconditional assignment block serves both modes.
    form_data = _rcm_stripped_for_save(form_data)
    case_file.manufacturer_fssai = form_data.get("manufacturer_fssai", "")
    case_file.manufacturer_name = form_data.get("manufacturer_name", "")
    case_file.manufacturer_fbo_name = form_data.get("manufacturer_fbo_name", "")
    case_file.manufacturer_address = form_data.get("manufacturer_address", "")
    case_file.batch_no = form_data.get("batch_no", "")
    case_file.mfg_date = parse_date(form_data.get("mfg_date", ""))
    case_file.expiry_date = parse_date(form_data.get("expiry_date", ""))
    case_file.manufacturer_report_receive_date = parse_date(
        form_data.get("manufacturer_report_receive_date", "")
    )
    case_file.other_food_articles = form_data.get("other_food_articles", "")
    case_file.total_cost = form_data.get("total_cost", "")
    case_file.cost_in_words = form_data.get("cost_in_words", "")
    case_file.sample_code = form_data.get("sample_code", "")
    case_file.Lab_Registration_No = _lookup_field(form_data, "lab_registration_no")
    case_file.sample_submission_date = parse_date(form_data.get("do_receipt_date", ""))
    case_file.do_receipt_date = parse_date(form_data.get("do_receipt_date", ""))
    case_file.is_misbranded = form_data.get("is_misbranded") == "misbranded"
    case_file.is_substandard = form_data.get("is_substandard") == "substandard"
    case_file.analyst_report_no = form_data.get("analyst_report_no", "")
    case_file.analyst_report_date = parse_date(form_data.get("analyst_report_date", ""))
    case_file.directive_letter_no = form_data.get("directive_letter_no", "")
    case_file.directive_letter_date = parse_date(form_data.get("directive_letter_date", ""))
    case_file.retailer_report_receive_date = parse_date(form_data.get("retailer_report_receive_date", ""))
    case_file.applicable_regulation = form_data.get("applicable_regulation", "")
    case_file.applicable_clause = form_data.get("applicable_clause", "")
    case_file.applicable_sections = ", ".join(get_applicable_sections(form_data))


def _process_case_file_form(form_data):
    """Create a CaseFile model instance from validated form data."""
    sample_id = _safe_int(form_data.get("sample_id")) if form_data.get("sample_id") else None

    packet_count = _safe_int(form_data.get("packet_count"), 4)

    # RCM-exempt fields arrive blanked (by decision of the RCM
    # policy seam): parse_date("")/"" land the right types downstream.
    form_data = _rcm_stripped_for_save(form_data)
    rcm = _is_retailer_cum_manufacturer(form_data)

    return CaseFile(
        case_number=form_data.get("case_number", ""),
        food_safety_officer_name=form_data.get("food_safety_officer_name", ""),
        authorization_date=parse_date(form_data.get("authorization_date", "")),
        inspection_date=parse_date(form_data.get("inspection_date", "")),
        inspection_time=form_data.get("inspection_time", ""),
        sample_id=sample_id,
        retailer_cum_manufacturer=rcm,
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
        Lab_Registration_No=_lookup_field(form_data, "lab_registration_no"),
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
    """Regenerate both Petition and Permission Letter from an existing case.

    Deliberately NOT gated on authorization: the permission file must be
    obtainable before authorization is issued.  When unauthorized, the
    bundled petition artifact is a pre-authorization draft — petition-only
    downloads stay 403 until the date is recorded.
    """
    case_file = CaseFile.query.get_or_404(case_id)
    # Pre-authorization draft bundle: the embargo applies, authorization
    # does not (petition-only downloads stay 403 until the date exists).
    access = check_generation_allowed(
        case_type="case_file",
        doc_type="both",
        require_authorization=False,
        retailer_receive=case_file.retailer_report_receive_date,
        manufacturer_receive=case_file.manufacturer_report_receive_date,
    )
    if not access.allowed:
        return jsonify(access.payload()), access.status
    form_data = case_file_to_dict(case_file)
    case_data = process_form_data(form_data)

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
                "authorization_issued": bool(case_file.authorization_date),
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
            "authorization_issued": bool(case_file.authorization_date),
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
    apply_update_fn=apply_case_file_update,
    form_dict_fn=case_file_form_dict,
    sheets_module="sample",
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
    result = lookup_fssai(license_no)
    if result.error:
        status_code = 400 if "required" in result.error or "prefix" in result.error else 404
        return jsonify({"error": result.error}), status_code
    return jsonify({"identity": result.data})


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
    if scope:
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

    response = {
        "petition_html": petition_html,
        "permission_html": permission_html,
        "case_number": case_data.get("case_number", ""),
        "authorization_issued": bool(parse_date(form_data.get("authorization_date", ""))),
    }
    response.update(
        _generation_gate_info(
            form_data.get("retailer_report_receive_date", ""),
            form_data.get("manufacturer_report_receive_date", ""),
        )
    )
    return jsonify(response)


@case_file_generator_bp.route("/generate_case_file", methods=["POST"])
def generate_case_file_route():
    form_data = request.form.to_dict()

    # Phase 18 RBAC: an fso-role account always owns what it creates — the
    # bound officer name overrides whatever the form submitted.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope:
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

    # The 30-day appeal embargo blocks file generation, NOT data entry: the
    # record is always persisted below; PDF generation is deferred when the
    # handover dates are still inside the window.
    from app.timeline.engine import generation_gate

    # Record construction (including RCM blanking) lives in
    # _process_case_file_form — the route owns scoping, persistence,
    # sync, gating, and PDF dispatch, not field mapping.
    case_file_record = _process_case_file_form(form_data)

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
        sync_row("sample", row_dict, entity_id=case_file_record.id)
        sync_warning = None
    except Exception as e:
        # Best-effort: the record is committed above, so a Sheets outage
        # must not fail the save — surface it as a non-blocking warning.
        current_app.logger.warning(f"Case file {case_file_record.id} sync failed (non-fatal): {e}")
        sync_warning = f"Table sync failed ({e}); the case file was saved."

    case_data = process_form_data(form_data)

    gate = generation_gate(
        case_file_record.retailer_report_receive_date,
        case_file_record.manufacturer_report_receive_date,
    )
    if gate["blocked"]:
        earliest = gate["earliest"]
        handover = gate["handover"]
        earliest_str = format_date_indian(earliest) if earliest else "—"
        return (
            jsonify({
                "message": (
                    "Case file saved; PDF generation deferred until "
                    f"{earliest_str} (30-day appeal window after report handover)."
                ),
                "case_file_id": case_file_record.id,
                "pdf_deferred": True,
                "earliest_allowed_date": earliest.isoformat() if earliest else None,
                "handover_date": handover.isoformat() if handover else None,
                "authorization_issued": bool(case_file_record.authorization_date),
                "sync_warning": sync_warning,
            }),
            201,
        )

    # Synchronous PDF generation (QStash/Celery removed).
    # NOTE: creation is deliberately NOT gated on authorization — first data
    # entry precedes it.  When unauthorized, the bundled petition artifact is
    # a pre-authorization draft; petition-only downloads stay 403 until the
    # date is recorded.
    from app.case_file_generator.tasks import generate_case_file_pdf

    try:
        pdf_result = generate_case_file_pdf(case_file_id=case_file_record.id, case_data=case_data)
    except Exception as exc:
        current_app.logger.error("Case file PDF generation failed: %s", exc)
        return jsonify({"error": f"Case file PDF generation failed: {exc}"}), 500

    return (
        jsonify({
            "message": "Case file created; PDF generated synchronously",
            "case_file_id": case_file_record.id,
            "pdf_result": pdf_result,
            "pdf_deferred": False,
            "authorization_issued": bool(case_file_record.authorization_date),
            "sync_warning": sync_warning,
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

    access = check_generation_allowed(
        case_type="case_file",
        doc_type="petition",
        authorization_date=case.authorization_date,
        retailer_receive=case.retailer_report_receive_date,
        manufacturer_receive=case.manufacturer_report_receive_date,
    )
    if not access.allowed:
        return jsonify(access.payload()), access.status

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)
    docx_bytes = render_docx("petition", case_data)

    buf = io.BytesIO(docx_bytes)
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

    access = check_generation_allowed(
        case_type="case_file",
        doc_type="permission",
        retailer_receive=case.retailer_report_receive_date,
        manufacturer_receive=case.manufacturer_report_receive_date,
    )
    if not access.allowed:
        return jsonify(access.payload()), access.status

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)
    docx_bytes = render_docx("permission", case_data)

    buf = io.BytesIO(docx_bytes)
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

    access = check_generation_allowed(
        case_type="case_file",
        doc_type="both",
        authorization_date=case.authorization_date,
        retailer_receive=case.retailer_report_receive_date,
        manufacturer_receive=case.manufacturer_report_receive_date,
    )
    if not access.allowed:
        return jsonify(access.payload()), access.status

    import io as _io
    import zipfile

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)

    petition_docx = render_docx("petition", case_data)
    permission_docx = render_docx("permission", case_data)

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
# Petition PDF download — validated single-file download
# ---------------------------------------------------------------------------


@case_file_generator_bp.route("/case/<int:case_id>/pdf/petition")
@login_required
def download_petition_pdf(case_id: int):
    """Download the Petition as a PDF file.

    Validates that every required field made it into the rendered petition
    — returns 400 listing the missing fields instead of a half-empty PDF.
    """
    from app.shared.petition_check import has_unresolved_jinja, missing_required_fields
    from app.utils.pdf_utils import generate_pdf_from_html, post_process_pdf_html

    # Visibility check first: fails closed on unknown ids, so missing and
    # out-of-scope cases both get the same JSON 404.
    if not _case_visible_to_current_user(case_id, "case_file"):
        return jsonify({"error": "Case not found"}), 404
    case = db.session.get(CaseFile, case_id)
    if case is None:
        return jsonify({"error": "Case not found"}), 404

    access = check_generation_allowed(
        case_type="case_file",
        doc_type="petition",
        authorization_date=case.authorization_date,
        retailer_receive=case.retailer_report_receive_date,
        manufacturer_receive=case.manufacturer_report_receive_date,
    )
    if not access.allowed:
        return jsonify(access.payload()), access.status

    form_data = case_file_to_dict(case)
    case_data = process_form_data(form_data)

    missing = missing_required_fields(_REQUIRED_FIELDS, case_data)
    if missing:
        return (
            jsonify({
                "error": "Petition is incomplete — these fields are missing and would render blank.",
                "missing_fields": missing,
            }),
            400,
        )

    petition_html = str(render_template("case_file_generator/petition.html", **case_data))
    petition_html = post_process_pdf_html(petition_html, case_id=case_id)
    if has_unresolved_jinja(petition_html):
        return (
            jsonify({"error": "Petition template has unresolved placeholders and cannot be generated."}),
            500,
        )

    pdf_bytes, error = generate_pdf_from_html(petition_html)
    if not pdf_bytes:
        current_app.logger.error("Petition PDF generation failed: %s", error)
        return jsonify({"error": f"PDF generation failed: {error}"}), 500

    return send_file(
        io.BytesIO(pdf_bytes),
        as_attachment=True,
        download_name=f"Petition_{case.case_number or case_id}.pdf",
        mimetype="application/pdf",
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

    access = check_generation_allowed(
        case_type="case_file",
        doc_type=doc_type,
        authorization_date=case.authorization_date,
        retailer_receive=case.retailer_report_receive_date,
        manufacturer_receive=case.manufacturer_report_receive_date,
    )
    if not access.allowed:
        return jsonify(access.payload()), access.status

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
