"""Adjudication blueprint — thin routes + DocumentCaseManager.

Common CRUD, editor, xref/toc, and renumber routes are registered via
:class:`app.shared.document_case_manager.DocumentCaseManager`.  This file
retains only the adjudication-specific helpers and routes:

- ``adjudication_to_dict``
- ``CHECKLIST`` / ``RULES``
- ``lookup_ce_route`` / ``suggest_sections_route`` / ``lookup_fbo_issues``
- ``generate_all`` (synchronous in-memory PDF generation)
- ``regenerate_adjudication_documents`` (in-memory ZIP)

Backward-compatible imports preserved for callers (tests, renderers, etc.).
"""

import io
import json
import zipfile
from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_file
from flask_login import login_required
from sqlalchemy import or_
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import csrf, db
from app.models import Adjudication, Evidence, FboIssue
from app.plugins.registry import PluginRegistry
from app.services.audit import log_audit
from app.services.sync_orchestrator import sync_row
from app.shared.case_keys import (
    DERIVED_APPLICABLE_SECTIONS,
    DERIVED_CASE_TRACK,
    DERIVED_SAME_ENTITY,
    DERIVED_SECTIONS_DISPLAY,
    DERIVED_VIOLATIONS,
    SECTION_55,
    SECTION_56,
    SECTION_58,
    SECTION_63,
    SECTION_64,
    SHARED_COMPLAINT_LODGED,
    SHARED_NON_LICENSE,
    SHARED_PRE_AUTHORIZATION,
)
from app.shared.context_derivers import (
    derive_applicable_sections_from_adjudication,
    derive_case_track,
    derive_sections_display,
    derive_violations,
)
from app.shared.document_case_manager import DocumentCaseManager
from app.utils.filters import parse_date
from app.utils.lookup import lookup_ce, lookup_fssai
from app.utils.pdf_utils import embed_photos_as_base64, generate_pdf_from_html, post_process_pdf_html

adjudication_bp = Blueprint("adjudication", __name__, template_folder="templates", static_folder="static")

CHECKLIST = [
    "clean_premise",
    "refrigerator_clean",
    "proper_attire",
    "proper_covered_utensil",
    "date_tag",
    "veg_nonveg_separation",
    "food_segregation",
    "license_display",
    "artificial_colour",
    "Expired_item",
    "Pest_report",
    "Water_report",
]

RULES = {
    "clean_premise": ("Unclean Premises", "The premises were found inadequately maintained and unhygienic."),
    "refrigerator_clean": ("Improper Refrigerator Maintenance", "Refrigeration facilities were found unclean."),
    "proper_attire": ("Improper Protective Attire", "Food handlers lacked prescribed attire."),
    "proper_covered_utensil": ("Improper Covering of Food", "Food and utensils were uncovered."),
    "date_tag": ("Absence of Date Tagging", "Stored food items lacked traceability."),
    "veg_nonveg_separation": ("Improper Veg/Non-Veg Separation", "Segregation not maintained."),
    "food_segregation": ("Improper Food Segregation", "Risk of cross contamination."),
    "license_display": ("Improper License Display", "License not prominently displayed."),
    "Expired_item": ("Expired Items", "Expired items present."),
    "Pest_report": ("Pest Control Report Missing", "Routine pest control not documented."),
    "Water_report": ("Water Test Report Missing", "Potable water testing unavailable."),
}


def adjudication_to_dict(adj):
    """Convert an Adjudication model instance to a dictionary for JSON serialization."""
    return {
        "id": adj.id,
        "case_number": adj.case_number,
        "food_safety_officer_name": adj.food_safety_officer,
        "non_license": adj.non_license,
        "pre_authorization": adj.pre_authorization,
        "complaint_lodged": adj.complaint_lodged,
        "ce_license_no": adj.ce_license_no,
        "ce_trade_name": adj.ce_trade_name,
        "ce_proprietor": adj.ce_proprietor,
        "ce_address": adj.ce_address,
        "ce_status": adj.ce_status,
        "fbo_owner": adj.fbo_owner,
        "fbo_name": adj.fbo_name,
        "fbo_address": adj.fbo_address,
        "fssai_license": adj.fssai_license,
        "concerned_food": adj.concerned_food,
        "problem": adj.problem,
        "first_inspection_date": (adj.First_inspection_date.isoformat() if adj.First_inspection_date else None),
        "compliance_deadline": adj.compliance_deadline.isoformat() if adj.compliance_deadline else None,
        "complaint_date": adj.Complaint_date.isoformat() if adj.Complaint_date else None,
        "followup_inspection_date": (adj.inspection_date.isoformat() if adj.inspection_date else None),
        "authorization_date": adj.authorization_date.isoformat() if adj.authorization_date else None,
        "clean_premise": adj.clean_premise,
        "refrigerator_clean": adj.refrigerator_clean,
        "proper_attire": adj.proper_attire,
        "proper_covered_utensil": adj.proper_covered_utensil,
        "date_tag": adj.date_tag,
        "veg_nonveg_separation": adj.veg_nonveg_separation,
        "food_segregation": adj.food_segregation,
        "license_display": adj.license_display,
        "artificial_colour": adj.artificial_colour,
        "Expired_item": adj.Expired_item,
        "Pest_report": adj.Pest_report,
        "Water_report": adj.Water_report,
        "section_55": adj.section_55,
        "section_56": adj.section_56,
        "section_58": adj.section_58,
        "section_63": adj.section_63,
        "section_64": adj.section_64,
        "created_at": adj.created_at.isoformat() if adj.created_at else None,
        "synced_at": adj.synced_at.isoformat() if adj.synced_at else None,
    }


def _process_adjudication_form(form_data):
    """Create an Adjudication model instance from validated form data."""
    return Adjudication(
        case_number=form_data.get("case_number", ""),
        food_safety_officer=form_data.get("food_safety_officer_name", ""),
        non_license=form_data.get("non_license", "no"),
        pre_authorization=form_data.get("pre_authorization", "no"),
        complaint_lodged=form_data.get("complaint_lodged", "no"),
        ce_license_no=form_data.get("ce_license_no", ""),
        ce_trade_name=form_data.get("ce_trade_name", ""),
        ce_proprietor=form_data.get("ce_proprietor", ""),
        ce_address=form_data.get("ce_address", ""),
        ce_status=form_data.get("ce_status", ""),
        fbo_owner=form_data.get("fbo_owner", ""),
        fbo_name=form_data.get("fbo_name", ""),
        fbo_address=form_data.get("fbo_address", ""),
        fssai_license=form_data.get("fssai_license", ""),
        concerned_food=form_data.get("concerned_food", ""),
        problem=form_data.get("problem", ""),
        First_inspection_date=parse_date(form_data.get("first_inspection_date", "")),
        compliance_deadline=parse_date(form_data.get("compliance_deadline", "")),
        Complaint_date=parse_date(form_data.get("complaint_date", "")),
        inspection_date=parse_date(form_data.get("followup_inspection_date", "")),
        authorization_date=parse_date(form_data.get("authorization_date", "")),
        clean_premise=form_data.get("clean_premise", "yes"),
        refrigerator_clean=form_data.get("refrigerator_clean", "yes"),
        proper_attire=form_data.get("proper_attire", "yes"),
        proper_covered_utensil=form_data.get("proper_covered_utensil", "yes"),
        date_tag=form_data.get("date_tag", "yes"),
        veg_nonveg_separation=form_data.get("veg_nonveg_separation", "yes"),
        food_segregation=form_data.get("food_segregation", "yes"),
        license_display=form_data.get("license_display", "yes"),
        artificial_colour=form_data.get("artificial_colour", "no"),
        Expired_item=form_data.get("Expired_item", "no"),
        Pest_report=form_data.get("Pest_report", "yes"),
        Water_report=form_data.get("Water_report", "yes"),
        section_55=form_data.get("section_55", "no"),
        section_56=form_data.get("section_56", "no"),
        section_58=form_data.get("section_58", "no"),
        section_63=form_data.get("section_63", "no"),
        section_64=form_data.get("section_64", "no"),
    )


def _prepare_adjudication_context(case_data):
    """Prepare template rendering context for adjudication documents."""
    form_data = case_data
    section_55 = form_data.get(SECTION_55, "no")
    section_56 = form_data.get(SECTION_56, "no")
    section_58 = form_data.get(SECTION_58, "no")
    section_63 = form_data.get(SECTION_63, "no")
    section_64 = form_data.get(SECTION_64, "no")

    non_license = form_data.get(SHARED_NON_LICENSE, "no")
    pre_authorization = form_data.get(SHARED_PRE_AUTHORIZATION, "no")
    complaint_lodged = form_data.get(SHARED_COMPLAINT_LODGED, "no")

    applicable_sections = derive_applicable_sections_from_adjudication(
        section_55=section_55,
        section_56=section_56,
        section_58=section_58,
        section_63=section_63,
        section_64=section_64,
    )

    context = form_data.copy()
    context[DERIVED_APPLICABLE_SECTIONS] = applicable_sections
    context[DERIVED_SECTIONS_DISPLAY] = derive_sections_display(applicable_sections)
    context[DERIVED_CASE_TRACK] = derive_case_track(
        non_license=non_license,
        pre_authorization=pre_authorization,
        complaint_lodged=complaint_lodged,
        is_sample=False,
    )
    context[DERIVED_VIOLATIONS] = derive_violations(form_data)
    context[DERIVED_SAME_ENTITY] = False
    context["violations"] = context[DERIVED_VIOLATIONS]
    return context


# --------------------------------------------------------------------------- #
# Form validation
# --------------------------------------------------------------------------- #

_ADJUDICATION_REQUIRED_FIELDS: dict[str, str] = {
    "case_number": "Case Number",
    "food_safety_officer_name": "Food Safety Officer Name",
    "fbo_owner": "FBO Owner",
    "fbo_name": "FBO Name",
    "fbo_address": "FBO Address",
    "fssai_license": "FSSAI License / Registration No",
    "first_inspection_date": "First Inspection Date",
    "compliance_deadline": "Compliance Deadline",
    "followup_inspection_date": "Follow-up Inspection Date",
}

_ADJUDICATION_DATE_FIELDS: list[str] = [
    "first_inspection_date",
    "compliance_deadline",
    "complaint_date",
    "followup_inspection_date",
    "authorization_date",
]


def validate_adjudication_form(form_data: dict) -> dict[str, str]:
    """Validate adjudication form data. Returns dict of field → error message."""
    errors: dict[str, str] = {}
    for field, label in _ADJUDICATION_REQUIRED_FIELDS.items():
        value = form_data.get(field, "")
        if value is None or (isinstance(value, str) and not value.strip()):
            errors[field] = f"{label} is required."

    for field in _ADJUDICATION_DATE_FIELDS:
        value = form_data.get(field, "")
        if not value:
            continue
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except (TypeError, ValueError):
            label = _ADJUDICATION_REQUIRED_FIELDS.get(field, field.replace("_", " ").title())
            errors[field] = f"{label} must be a valid date."

    # authorization_date is required when NOT pre-authorization
    is_pre_authorization = str(form_data.get("pre_authorization", "no")).strip().lower() == "yes"
    if not is_pre_authorization and not form_data.get("authorization_date", "").strip():
        errors["authorization_date"] = "Authorization Date is required for non-pre-authorization cases."

    return errors


# --------------------------------------------------------------------------- #
# DocumentCaseManager — common routes delegation
# --------------------------------------------------------------------------- #

_manager = DocumentCaseManager(
    model=Adjudication,
    template_dir="adjudication",
    bp_name="adjudication",
    case_type="adjudication",
    model_to_dict_fn=adjudication_to_dict,
    process_form_fn=_process_adjudication_form,
    prepare_context_fn=_prepare_adjudication_context,
    templates={
        "permission": "adjudication/Legal_NonsampleAdjudication_Template.html",
        "petition": "adjudication/template_nonsample_petition.html",
    },
)
_manager.register_routes(adjudication_bp)


# --------------------------------------------------------------------------- #
# Model-specific routes (not covered by DocumentCaseManager)
# --------------------------------------------------------------------------- #


@csrf.exempt
@adjudication_bp.route("/lookup_ce", methods=["POST"])
def lookup_ce_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    if not license_no:
        return jsonify({"error": "License number is required."}), 400
    try:
        result = lookup_ce(license_no)
    except Exception:
        return jsonify({"error": "Could not reach KMC portal. Try again."}), 502
    if not result:
        return jsonify({"error": "License not found."}), 404
    return jsonify(result)


@csrf.exempt
@adjudication_bp.route("/lookup_fssai", methods=["POST"])
def lookup_fssai_route():
    payload = request.get_json() or {}
    license_no = payload.get("license_no", "").strip()
    result, error = lookup_fssai(license_no)
    if error:
        status_code = 400 if "required" in error or "prefix" in error else 404
        return jsonify({"error": error}), status_code
    return jsonify({"identity": result})


@adjudication_bp.route("/lookup_fbo_issues", methods=["GET"])
def lookup_fbo_issues():
    """Lookup FBO issues by fbo_id to provide pre-fill options for adjudication cases."""
    fbo_id = request.args.get("fbo_id")
    issue_id = request.args.get("issue_id")

    if not fbo_id and not issue_id:
        return jsonify({"error": "Either fbo_id or issue_id is required"}), 400

    query = FboIssue.query.filter(FboIssue.state.in_(["open", "permission_granted"]))

    if issue_id:
        try:
            issue_id_int = int(issue_id)
        except ValueError:
            return jsonify({"error": "issue_id must be an integer"}), 400
        query = query.filter_by(id=issue_id_int)
    elif fbo_id:
        query = query.filter_by(fbo_id=fbo_id)

    issues = query.order_by(FboIssue.created_at.desc()).all()

    result = []
    for issue in issues:
        detail = None
        if issue.detail_json:
            try:
                detail = json.loads(issue.detail_json)
            except Exception:
                detail = issue.detail_json

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

        if issue.source_type == "inspection" and detail:
            prefill_data["prefill"] = {
                "fbo_name": issue.fbo_name,
                "fssai_license": issue.fbo_id,
                "fbo_owner": issue.fbo_name,
                "concerned_food": detail.get("checklist", []),
                "problem": ", ".join(detail.get("checklist", [])),
                "inspection_date": detail.get("inspection_date"),
                "food_safety_officer": issue.fso_name,
            }
        elif issue.source_type == "sample" and detail:
            prefill_data["prefill"] = {
                "fbo_name": issue.fbo_name,
                "fssai_license": issue.fbo_id,
                "fbo_owner": issue.fbo_name,
                "concerned_food": detail.get("sample_name"),
                "problem": f"Sample issue: {detail.get('sample_name', '')} - {detail.get('sample_code', '')}",
                "sample_code": detail.get("sample_code"),
                "sample_name": detail.get("sample_name"),
                "sampling_date": detail.get("sampling_date"),
                "price": detail.get("price"),
                "food_safety_officer": issue.fso_name,
            }
            if issue.manufacturer_fbo_id:
                prefill_data["prefill"]["manufacturer_fssai"] = issue.manufacturer_fbo_id
        else:
            prefill_data["prefill"] = {
                "fbo_name": issue.fbo_name,
                "fssai_license": issue.fbo_id,
                "fbo_owner": issue.fbo_name,
                "food_safety_officer": issue.fso_name,
            }

        result.append(prefill_data)

    return jsonify(result), 200


@adjudication_bp.route("/suggest_sections", methods=["POST"])
def suggest_sections_route():
    form_data = request.form.to_dict()
    rule_provider = PluginRegistry.get_instance().get_active("rules")
    suggestions = rule_provider.suggest_sections(form_data)
    return jsonify(suggestions)


@adjudication_bp.route("/regenerate/<int:case_id>", methods=["GET"])
def regenerate_adjudication_documents(case_id):
    """Regenerate documents from an existing adjudication case."""
    adj = Adjudication.query.get_or_404(case_id)
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None and adj.food_safety_officer != scope:
        return jsonify({"error": "Case not found"}), 404
    form_data = adjudication_to_dict(adj)

    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    include_flagged = request.args.get("include_flagged", "false").lower() == "true"
    flag_override_reason = request.args.get("flag_override_reason", "").strip()

    all_photos = (
        Evidence.query
        .filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )

    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    flagged_photos = [p for p in all_photos if p.verification_status == "FLAG"]

    if include_flagged:
        if not flag_override_reason:
            return jsonify({"error": "flag_override_reason is required when include_flagged=true"}), 400
        final_photos = verified_photos + flagged_photos
        flagged_image_ids = [p.id for p in flagged_photos]
        if flagged_image_ids:
            log_audit(
                "photo",
                ",".join(flagged_image_ids),
                "FLAGGED_PHOTO_INCLUDED",
                actor=form_data.get("food_safety_officer_name", "unknown"),
                details={"reason": flag_override_reason},
            )
    else:
        final_photos = verified_photos

    context["adjudication"] = {
        "photos": final_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in final_photos]),
    }

    image_ids = [p.id for p in final_photos]
    statuses = [p.verification_status for p in final_photos]
    log_audit(
        "adjudication_order",
        str(case_id),
        "ADJUDICATION_ORDER_REGENERATED",
        actor=form_data.get("food_safety_officer_name", "unknown"),
        details={"image_ids": image_ids, "statuses": statuses},
    )

    outputs = []
    is_pre_authorization = str(form_data.get("pre_authorization", "no")).strip().lower() == "yes"
    if is_pre_authorization:
        templates_to_generate = [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
    else:
        if not form_data.get("authorization_date"):
            return jsonify({"error": "authorization_date is required for non-pre-authorization cases."}), 400
        templates_to_generate = [("adjudication/template_nonsample_petition.html", "Petition")]

    for tpl, prefix in templates_to_generate:
        rendered_html = render_template(tpl, **context)
        rendered_html = post_process_pdf_html(rendered_html, adjudication_id=case_id)
        pdf_bytes, error = generate_pdf_from_html(rendered_html)
        if pdf_bytes:
            outputs.append((f"{prefix}.pdf", pdf_bytes))
        else:
            current_app.logger.error(f"PDF generation failed for {tpl}: {error}")
            return (
                jsonify({
                    "error": f"PDF generation failed: {error}. Documents cannot be generated without WeasyPrint.",
                }),
                500,
            )

    zip_prefix = "PermissionLetter" if is_pre_authorization else "Petition"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, data in outputs:
            z.writestr(fname, data)
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"{zip_prefix}_Case_{form_data.get('case_number', 'unknown')}_Regenerated.zip",
        mimetype="application/zip",
    )


@adjudication_bp.route("/preview", methods=["POST"])
@login_required
def preview_adjudication_route():
    """Render Petition + Permission Letter HTML from form data for review.

    Unlike ``generate_all``, this does NOT create an Adjudication record or
    generate a PDF — it returns the rendered HTML so the user can review both
    documents in the Quill editor before committing.
    """
    form_data = request.form.to_dict()

    # Phase 18 RBAC: an fso-role account always owns what it creates — the
    # bound officer name overrides whatever the form submitted.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None:
        form_data["food_safety_officer_name"] = scope

    validation_errors = validate_adjudication_form(form_data)
    if validation_errors:
        return (
            jsonify({
                "error": "Please correct the highlighted fields below.",
                "errors": validation_errors,
            }),
            400,
        )

    context = _prepare_adjudication_context(form_data)

    # Templates reference ``adjudication.photos`` — set empty since we're
    # previewing from form data, not an existing record.
    context["adjudication"] = {"photos": [], "photo_embeds": []}

    petition_html = str(render_template("adjudication/template_nonsample_petition.html", **context))
    permission_html = str(render_template("adjudication/Legal_NonsampleAdjudication_Template.html", **context))

    # Phase 6+7: cross-reference pass (renumbering, enclosures, TOC).
    # No case_id available — photo/embed enrichment is skipped gracefully.
    from app.utils.pdf_utils import post_process_pdf_html

    petition_html = post_process_pdf_html(petition_html)
    permission_html = post_process_pdf_html(permission_html)

    return jsonify({
        "petition_html": petition_html,
        "permission_html": permission_html,
        "case_number": form_data.get("case_number", ""),
    })


@adjudication_bp.route("/generate_all", methods=["POST"])
def generate_all():
    """Create a new adjudication case and generate PDFs in-memory."""
    form_data = request.form.to_dict()

    # Phase 18 RBAC: an fso-role account always owns what it creates.
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None:
        form_data["food_safety_officer_name"] = scope

    adj = _process_adjudication_form(form_data)
    db.session.add(adj)
    try:
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        return jsonify({"error": "This adjudication was modified by another user. Please reload and try again."}), 409

    # Link back to inspection if this was created from one
    from_inspection = form_data.get("from_inspection")
    if from_inspection:
        try:
            from app.models import Inspection

            inspection = db.session.get(Inspection, int(from_inspection))
            if inspection and not inspection.adjudication_id and not inspection.is_dismissed:
                today = datetime.now(UTC)
                if inspection.compliance_deadline and inspection.compliance_deadline < today:
                    inspection.adjudication_id = adj.id
                try:
                    db.session.commit()
                except StaleDataError:
                    db.session.rollback()
                    current_app.logger.warning(
                        f"Adjudication {adj.id}: StaleDataError linking inspection {from_inspection}",
                    )
        except Exception as e:
            current_app.logger.warning(f"Adjudication: Failed to link inspection {from_inspection}: {e}")
            db.session.rollback()

    # Sheets sync
    allowed_sheets_columns = {
        "case_number",
        "food_safety_officer",
        "non_license",
        "pre_authorization",
        "complaint_lodged",
        "ce_license_no",
        "ce_trade_name",
        "ce_proprietor",
        "ce_address",
        "ce_status",
        "fbo_owner",
        "fbo_name",
        "fbo_address",
        "fssai_license",
        "concerned_food",
        "problem",
        "First_inspection_date",
        "compliance_deadline",
        "Complaint_date",
        "inspection_date",
        "authorization_date",
        "clean_premise",
        "refrigerator_clean",
        "proper_attire",
        "proper_covered_utensil",
        "date_tag",
        "veg_nonveg_separation",
        "food_segregation",
        "license_display",
        "artificial_colour",
        "Expired_item",
        "Pest_report",
        "Water_report",
        "section_55",
        "section_56",
        "section_58",
        "section_63",
        "section_64",
    }
    try:
        row_dict = {k: v for k, v in form_data.items() if k in allowed_sheets_columns}
        row_dict["created_at"] = adj.created_at.isoformat() if adj.created_at else ""
        result = sync_row("non_sample", row_dict, entity_id=adj.id)
        if not result["sheets"]:
            current_app.logger.warning("Adjudication: Sheets sync failed - not blocking")
    except Exception as e:
        current_app.logger.warning(f"Adjudication sync failed: {e}")

    # Prepare context
    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    include_flagged = request.form.get("include_flagged", "false").lower() == "true"
    flag_override_reason = request.form.get("flag_override_reason", "").strip()

    all_photos = (
        Evidence.query
        .filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == adj.id, Evidence.adjudication_id == adj.id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )

    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    flagged_photos = [p for p in all_photos if p.verification_status == "FLAG"]

    if include_flagged:
        if not flag_override_reason:
            return jsonify({"error": "flag_override_reason is required when include_flagged=true"}), 400
        final_photos = verified_photos + flagged_photos
        flagged_image_ids = [p.id for p in flagged_photos]
        if flagged_image_ids:
            log_audit(
                "photo",
                ",".join(flagged_image_ids),
                "FLAGGED_PHOTO_INCLUDED",
                actor=form_data.get("food_safety_officer_name", "unknown"),
                details={"reason": flag_override_reason},
            )
    else:
        final_photos = verified_photos

    context["adjudication"] = {
        "photos": final_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in final_photos]),
    }

    image_ids = [p.id for p in final_photos]
    statuses = [p.verification_status for p in final_photos]
    log_audit(
        "adjudication_order",
        str(adj.id),
        "ADJUDICATION_ORDER_GENERATED",
        actor=form_data.get("food_safety_officer_name", "unknown"),
        details={"image_ids": image_ids, "statuses": statuses},
    )

    outputs = []
    is_pre_authorization = str(form_data.get("pre_authorization", "no")).strip().lower() == "yes"
    if is_pre_authorization:
        templates_to_generate = [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
    else:
        if not form_data.get("authorization_date"):
            return jsonify({"error": "authorization_date is required for non-pre-authorization cases."}), 400
        templates_to_generate = [("adjudication/template_nonsample_petition.html", "Petition")]

    for tpl, prefix in templates_to_generate:
        rendered_html = render_template(tpl, **context)
        rendered_html = post_process_pdf_html(rendered_html, adjudication_id=adj.id)
        pdf_bytes, error = generate_pdf_from_html(rendered_html)
        if pdf_bytes:
            outputs.append((f"{prefix}.pdf", pdf_bytes))
        else:
            current_app.logger.error(f"PDF generation failed for {tpl}: {error}")
            return (
                jsonify({
                    "error": f"PDF generation failed: {error}. Documents cannot be generated without WeasyPrint.",
                }),
                500,
            )

    zip_prefix = "PermissionLetter" if is_pre_authorization else "Petition"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, data in outputs:
            z.writestr(fname, data)
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"{zip_prefix}_Final.zip",
        mimetype="application/zip",
    )


# ---------------------------------------------------------------------------
# Word (.docx) download routes
# ---------------------------------------------------------------------------


@adjudication_bp.route("/case/<int:case_id>/docx/petition")
@login_required
def download_petition_docx(case_id: int):
    """Download the Adjudication Petition as a Word (.docx) document."""
    adj = Adjudication.query.get_or_404(case_id)
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None and adj.food_safety_officer != scope:
        return jsonify({"error": "Case not found"}), 404

    from app.adjudication.word_converter import AdjudicationWordConverter

    form_data = adjudication_to_dict(adj)
    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # Include photos for the context
    all_photos = (
        Evidence.query
        .filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )
    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    context["adjudication"] = {
        "photos": verified_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in verified_photos]),
    }

    converter = AdjudicationWordConverter()
    docx_bytes = converter.build_petition(context)

    buf = io.BytesIO(docx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"Adjudication_Petition_{adj.case_number or case_id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@adjudication_bp.route("/case/<int:case_id>/docx/permission")
@login_required
def download_permission_docx(case_id: int):
    """Download the Adjudication Permission Letter as a Word (.docx) document."""
    adj = Adjudication.query.get_or_404(case_id)
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None and adj.food_safety_officer != scope:
        return jsonify({"error": "Case not found"}), 404

    from app.adjudication.word_converter import AdjudicationWordConverter

    form_data = adjudication_to_dict(adj)
    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    converter = AdjudicationWordConverter()
    docx_bytes = converter.build_permission_letter(context)

    buf = io.BytesIO(docx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"Permission_Letter_{adj.case_number or case_id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@adjudication_bp.route("/case/<int:case_id>/docx/zip")
@login_required
def download_both_docx(case_id: int):
    """Download both Petition + Permission Letter as a single ZIP of .docx files."""
    adj = Adjudication.query.get_or_404(case_id)
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None and adj.food_safety_officer != scope:
        return jsonify({"error": "Case not found"}), 404

    import zipfile

    from app.adjudication.word_converter import AdjudicationWordConverter

    form_data = adjudication_to_dict(adj)
    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # Include photos for the petition context
    all_photos = (
        Evidence.query
        .filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )
    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    context["adjudication"] = {
        "photos": verified_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in verified_photos]),
    }

    converter = AdjudicationWordConverter()
    petition_docx = converter.build_petition(context)
    permission_docx = converter.build_permission_letter(context)

    label = adj.case_number or str(case_id)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"Adjudication_Petition_{label}.docx", petition_docx)
        zf.writestr(f"Permission_Letter_{label}.docx", permission_docx)
    zip_buf.seek(0)
    return send_file(
        zip_buf,
        as_attachment=True,
        download_name=f"Adjudication_{label}_Word.zip",
        mimetype="application/zip",
    )


# ---------------------------------------------------------------------------
# Copy Letter — returns rendered HTML letter body for copy-paste into Gmail
# ---------------------------------------------------------------------------


@adjudication_bp.route("/case/<int:case_id>/copy-letter/<doc_type>")
@login_required
def copy_letter(case_id: int, doc_type: str):
    """Return rendered HTML letter body for copy-paste into Gmail.

    ``doc_type`` is ``petition`` or ``permission``.
    """
    adj = Adjudication.query.get_or_404(case_id)
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is not None and adj.food_safety_officer != scope:
        return jsonify({"error": "Case not found"}), 404

    if doc_type not in ("petition", "permission"):
        return jsonify({"error": "Invalid doc_type"}), 400

    form_data = adjudication_to_dict(adj)
    context = _prepare_adjudication_context(form_data)
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # Include photos for petition rendering
    all_photos = (
        Evidence.query
        .filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )
    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    context["adjudication"] = {
        "photos": verified_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in verified_photos]),
    }

    template_map = {
        "petition": "adjudication/template_nonsample_petition.html",
        "permission": "adjudication/Legal_NonsampleAdjudication_Template.html",
    }
    html = str(render_template(template_map[doc_type], **context))

    from app.utils.pdf_utils import post_process_pdf_html

    html = post_process_pdf_html(html, adjudication_id=case_id)

    return html, 200, {"Content-Type": "text/html; charset=utf-8"}
