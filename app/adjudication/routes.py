import io
import json
import zipfile
from datetime import UTC, datetime

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, url_for
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import Adjudication, Evidence, FboIssue
from app.services.audit import log_audit
from app.services.sheets_sync import sync_to_sheets
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
from app.utils.filters import parse_date
from app.utils.lookup import lookup_ce, lookup_fssai
from app.utils.pdf_utils import embed_photos_as_base64, generate_pdf_from_html, post_process_pdf_html
from app.utils.suggester import suggest_sections

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
    """Convert an Adjudication model instance to a dictionary for JSON serialization.
    This includes all fields needed for form pre-population and document regeneration.
    Map DB columns to canonical keys for Step 3.
    """
    return {
        "id": adj.id,
        "case_number": adj.case_number,
        "food_safety_officer_name": adj.food_safety_officer,  # DB column: food_safety_officer
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
        "first_inspection_date": (
            adj.First_inspection_date.isoformat() if adj.First_inspection_date else None
        ),  # DB column: First_inspection_date
        "compliance_deadline": adj.compliance_deadline.isoformat() if adj.compliance_deadline else None,
        "complaint_date": adj.Complaint_date.isoformat() if adj.Complaint_date else None,  # DB column: Complaint_date
        "followup_inspection_date": (
            adj.inspection_date.isoformat() if adj.inspection_date else None
        ),  # DB column: inspection_date (follow-up)
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


@adjudication_bp.route("/")
def index():
    # Check for prefill data from inspection - using canonical keys from Step 3
    prefill_data = {}
    for key in [
        "from_inspection",
        "food_safety_officer_name",
        "fbo_name",
        "fbo_address",
        "fssai_license",
        "ce_license_no",
        "first_inspection_date",
        "compliance_deadline",
        "concerned_food",
        "problem",
        "ce_trade_name",
        "ce_proprietor",
        "ce_address",
        "ce_status",
    ]:
        value = request.args.get(key)
        if value:
            prefill_data[key] = value

    return render_template("adjudication/index.html", checklist=CHECKLIST, prefill=prefill_data)


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
    """Lookup FBO issues by fbo_id to provide pre-fill options for adjudication cases.
    Returns open and permission_granted issues that can be used to pre-fill adjudication forms.
    Query params: fbo_id (required), issue_id (optional - specific issue lookup)
    """
    fbo_id = request.args.get("fbo_id")
    issue_id = request.args.get("issue_id")

    if not fbo_id and not issue_id:
        return jsonify({"error": "Either fbo_id or issue_id is required"}), 400

    query = FboIssue.query.filter(FboIssue.state.in_(["open", "permission_granted"]))

    if issue_id:
        # Specific issue lookup by ID
        try:
            issue_id_int = int(issue_id)
        except ValueError:
            return jsonify({"error": "issue_id must be an integer"}), 400
        query = query.filter_by(id=issue_id_int)
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

        # Extract relevant pre-fill data for adjudication
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

        # Add source-specific prefill mappings for adjudication form fields
        if issue.source_type == "inspection" and detail:
            prefill_data["prefill"] = {
                "fbo_name": issue.fbo_name,
                "fssai_license": issue.fbo_id,
                "fbo_owner": issue.fbo_name,  # Map to fbo_owner as default
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
            # Add manufacturer info if present
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
    # Accept standard form data
    form_data = request.form.to_dict()
    suggestions = suggest_sections(form_data)
    return jsonify(suggestions)


# Adjudication retrieval endpoints for data reuse
@adjudication_bp.route("/cases", methods=["GET"])
def list_adjudication_cases():
    """List all existing adjudication cases."""
    cases = Adjudication.query.order_by(Adjudication.created_at.desc()).all()
    return jsonify(
        [
            {
                "id": c.id,
                "case_number": c.case_number,
                "fbo_name": c.fbo_name,
                "food_safety_officer": c.food_safety_officer,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in cases
        ]
    )


@adjudication_bp.route("/case/<int:case_id>", methods=["GET"])
def get_adjudication_case(case_id):
    """Retrieve a specific adjudication case by ID."""
    adj = Adjudication.query.get_or_404(case_id)
    return jsonify(adjudication_to_dict(adj))


@adjudication_bp.route("/case/by_number/<case_number>", methods=["GET"])
def get_adjudication_case_by_number(case_number):
    """Retrieve a specific adjudication case by case number."""
    adj = Adjudication.query.filter_by(case_number=case_number).first_or_404()
    return jsonify(adjudication_to_dict(adj))


@adjudication_bp.route("/<int:case_id>/editor", methods=["GET"])
def edit_adjudication(case_id):
    """Render the Quill document editor, pre-filled with an adjudication's documents."""
    adj = Adjudication.query.get_or_404(case_id)
    from app.document_viewer.renderer import render_adjudication_document

    return render_template(
        "document_viewer/editor.html",
        case_number=adj.case_number,
        case_id=adj.id,
        case_type="adjudication",
        petition_html=render_adjudication_document(case_id, "petition"),
        permission_html=render_adjudication_document(case_id, "permission"),
        report_url=url_for("adjudication.xref_report", case_id=case_id),
        toc_url=url_for("adjudication.toc_report", case_id=case_id),
    )


@adjudication_bp.route("/<int:case_id>/xref_report", methods=["GET"])
def xref_report(case_id):
    """Render the cross-reference (Xref) report for an adjudication case.

    Shows extracted paragraph / annexure / section references, their
    resolution status against stored annexures, and a live HTML preview
    of the document with enclosures auto-filled.
    """
    adj = Adjudication.query.get_or_404(case_id)
    doc_type = request.args.get("doc_type", "petition")

    from app.cross_reference import generate_xref_report_data
    from app.document_viewer.renderer import render_adjudication_document

    annotated_html = render_adjudication_document(case_id, doc_type)
    report = generate_xref_report_data(annotated_html, adjudication_id=case_id)

    return render_template(
        "xref_report.html",
        case_number=adj.case_number,
        fbo_name=adj.fbo_name,
        food_safety_officer=adj.food_safety_officer,
        doc_type=doc_type,
        report=report,
        annotated_html=annotated_html,
        report_url=url_for("adjudication.xref_report", case_id=case_id),
        renumber_url=url_for("adjudication.renumber_annexures", case_id=case_id),
    )


@adjudication_bp.route("/<int:case_id>/toc_report", methods=["GET"])
def toc_report(case_id):
    """Render the Table of Contents report for an adjudication case.

    Shows extracted headings with hierarchical numbering, the generated
    TOC HTML, and a live preview with heading IDs annotated.
    """
    adj = Adjudication.query.get_or_404(case_id)
    doc_type = request.args.get("doc_type", "petition")

    from app.document_viewer.renderer import render_adjudication_document
    from app.toc_generator import generate_toc_data
    from app.toc_generator.engine import TocGeneratorEngine

    annotated_html = render_adjudication_document(case_id, doc_type)
    toc_data = generate_toc_data(annotated_html)
    toc_html = TocGeneratorEngine().build_toc_html(TocGeneratorEngine().extract_toc(annotated_html))

    return render_template(
        "toc_report.html",
        case_number=adj.case_number,
        fbo_name=adj.fbo_name,
        food_safety_officer=adj.food_safety_officer,
        doc_type=doc_type,
        toc_data=toc_data,
        toc_html=toc_html,
        annotated_html=annotated_html,
        toc_url=url_for("adjudication.toc_report", case_id=case_id),
    )


@adjudication_bp.route("/<int:case_id>/renumber_annexures", methods=["POST"])
def renumber_annexures(case_id):
    """Renumber annexure letters (A, B, C, ...) in upload order."""
    from app.cross_reference.engine import CrossReferenceEngine

    updates = CrossReferenceEngine().renumber_annexures(adjudication_id=case_id)
    return jsonify({"status": "ok", "updates": updates, "count": len(updates)})


@adjudication_bp.route("/regenerate/<int:case_id>", methods=["GET"])
def regenerate_adjudication_documents(case_id):
    """Regenerate documents from an existing adjudication case."""
    adj = Adjudication.query.get_or_404(case_id)
    form_data = adjudication_to_dict(adj)

    is_pre_authorization = str(form_data.get("pre_authorization", "no")).strip().lower() == "yes"

    # STEP 4: Derive all context fields using shared helpers
    # Get section checkboxes
    section_55 = form_data.get(SECTION_55, "no")
    section_56 = form_data.get(SECTION_56, "no")
    section_58 = form_data.get(SECTION_58, "no")
    section_63 = form_data.get(SECTION_63, "no")
    section_64 = form_data.get(SECTION_64, "no")

    # Get case flags
    non_license = form_data.get(SHARED_NON_LICENSE, "no")
    pre_authorization = form_data.get(SHARED_PRE_AUTHORIZATION, "no")
    complaint_lodged = form_data.get(SHARED_COMPLAINT_LODGED, "no")

    # Derive applicable sections
    applicable_sections = derive_applicable_sections_from_adjudication(
        section_55=section_55,
        section_56=section_56,
        section_58=section_58,
        section_63=section_63,
        section_64=section_64,
    )

    # Render context
    context = form_data.copy()
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # STEP 4: Add canonical derived context fields
    context[DERIVED_APPLICABLE_SECTIONS] = applicable_sections
    context[DERIVED_SECTIONS_DISPLAY] = derive_sections_display(applicable_sections)
    context[DERIVED_CASE_TRACK] = derive_case_track(
        non_license=non_license,
        pre_authorization=pre_authorization,
        complaint_lodged=complaint_lodged,
        is_sample=False,
    )
    context[DERIVED_VIOLATIONS] = derive_violations(form_data)
    context[DERIVED_SAME_ENTITY] = False  # Adjudication doesn't use same_entity

    # Keep backward compatible violations field
    context["violations"] = context[DERIVED_VIOLATIONS]

    # Photo Evidence Integration for regenerate function
    include_flagged = request.args.get("include_flagged", "false").lower() == "true"
    flag_override_reason = request.args.get("flag_override_reason", "").strip()

    # Fetch all photo evidence for this case (linked via case_id or adjudication_id)
    from sqlalchemy import or_

    all_photos = (
        Evidence.query.filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )

    # Split into verified and flagged photos
    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    flagged_photos = [p for p in all_photos if p.verification_status == "FLAG"]

    # Determine final photos list based on include_flagged flag
    if include_flagged:
        if not flag_override_reason:
            return jsonify({"error": "flag_override_reason is required when include_flagged=true"}), 400

        # Combine verified and flagged photos
        final_photos = verified_photos + flagged_photos

        # Log audit for flagged photos inclusion
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

    # Add photos to context
    context["adjudication"] = {
        "photos": final_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in final_photos]),
    }

    # Log adjudication order generation with photo evidence details
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
    if is_pre_authorization:
        templates_to_generate = [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
    else:
        if not form_data.get("authorization_date"):
            return jsonify({"error": "authorization_date is required for non-pre-authorization cases."}), 400
        templates_to_generate = [("adjudication/template_nonsample_petition.html", "Petition")]

    for tpl, prefix in templates_to_generate:
        rendered_html = render_template(tpl, **context)
        # Phase 6: cross-reference pass (list renumbering + annexure enclosures).
        rendered_html = post_process_pdf_html(rendered_html, adjudication_id=case_id)
        pdf_bytes, error = generate_pdf_from_html(rendered_html)
        if pdf_bytes:
            outputs.append((f"{prefix}.pdf", pdf_bytes))
        else:
            current_app.logger.error(f"PDF generation failed for {tpl}: {error}")
            return (
                jsonify(
                    {
                        "error": f"PDF generation failed: {error}. Documents cannot be generated without WeasyPrint.",
                    }
                ),
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


@adjudication_bp.route("/generate_all", methods=["POST"])
def generate_all():
    form_data = request.form.to_dict()

    # Save record to local database - using canonical keys from Step 2
    adj = Adjudication(
        case_number=form_data.get("case_number", ""),
        food_safety_officer=form_data.get("food_safety_officer_name", ""),  # canonical -> DB column
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
        First_inspection_date=parse_date(form_data.get("first_inspection_date", "")),  # canonical
        compliance_deadline=parse_date(form_data.get("compliance_deadline", "")),
        Complaint_date=parse_date(form_data.get("complaint_date", "")),  # canonical
        inspection_date=parse_date(form_data.get("followup_inspection_date", "")),  # canonical -> DB column (follow-up)
        authorization_date=parse_date(form_data.get("authorization_date", "")),
        # Checklist
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
        # Sections
        section_55=form_data.get("section_55", "no"),
        section_56=form_data.get("section_56", "no"),
        section_58=form_data.get("section_58", "no"),
        section_63=form_data.get("section_63", "no"),
        section_64=form_data.get("section_64", "no"),
    )

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
                # Check if compliance_deadline has passed
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

    # Try syncing to Google Sheets (new module-based sync)
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
        success = sync_to_sheets("non_sample", row_dict)
        if not success:
            current_app.logger.warning("Adjudication: Sheets sync returned False - sync failed but not blocking")
    except Exception as e:
        current_app.logger.warning(f"Adjudication: Sheets sync failed: {e}")

    # Generate Adjudication Pack Documents in Memory
    is_pre_authorization = str(form_data.get("pre_authorization", "no")).strip().lower() == "yes"

    # STEP 4: Derive all context fields using shared helpers
    # Get section checkboxes
    section_55 = form_data.get(SECTION_55, "no")
    section_56 = form_data.get(SECTION_56, "no")
    section_58 = form_data.get(SECTION_58, "no")
    section_63 = form_data.get(SECTION_63, "no")
    section_64 = form_data.get(SECTION_64, "no")

    # Get case flags
    non_license = form_data.get(SHARED_NON_LICENSE, "no")
    pre_authorization = form_data.get(SHARED_PRE_AUTHORIZATION, "no")
    complaint_lodged = form_data.get(SHARED_COMPLAINT_LODGED, "no")

    # Derive applicable sections
    applicable_sections = derive_applicable_sections_from_adjudication(
        section_55=section_55,
        section_56=section_56,
        section_58=section_58,
        section_63=section_63,
        section_64=section_64,
    )

    # Render context
    context = form_data.copy()
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # STEP 4: Add canonical derived context fields
    context[DERIVED_APPLICABLE_SECTIONS] = applicable_sections
    context[DERIVED_SECTIONS_DISPLAY] = derive_sections_display(applicable_sections)
    context[DERIVED_CASE_TRACK] = derive_case_track(
        non_license=non_license,
        pre_authorization=pre_authorization,
        complaint_lodged=complaint_lodged,
        is_sample=False,
    )
    context[DERIVED_VIOLATIONS] = derive_violations(form_data)
    context[DERIVED_SAME_ENTITY] = False  # Adjudication doesn't use same_entity

    # Keep backward compatible violations field
    context["violations"] = context[DERIVED_VIOLATIONS]

    # Photo Evidence Integration
    include_flagged = request.form.get("include_flagged", "false").lower() == "true"
    flag_override_reason = request.form.get("flag_override_reason", "").strip()

    # Fetch all photo evidence for this case (linked via case_id or adjudication_id)
    from sqlalchemy import or_

    all_photos = (
        Evidence.query.filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == adj.id, Evidence.adjudication_id == adj.id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )

    # Split into verified and flagged photos
    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    flagged_photos = [p for p in all_photos if p.verification_status == "FLAG"]

    # Determine final photos list based on include_flagged flag
    if include_flagged:
        if not flag_override_reason:
            return jsonify({"error": "flag_override_reason is required when include_flagged=true"}), 400

        # Combine verified and flagged photos
        final_photos = verified_photos + flagged_photos

        # Log audit for flagged photos inclusion
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

    # Add photos to context
    context["adjudication"] = {
        "photos": final_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in final_photos]),
    }

    # Log adjudication order generation with photo evidence details
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
    if is_pre_authorization:
        templates_to_generate = [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
    else:
        if not form_data.get("authorization_date"):
            return jsonify({"error": "authorization_date is required when Pre-Authorization Case is not checked."}), 400
        templates_to_generate = [("adjudication/template_nonsample_petition.html", "Petition")]

    for tpl, prefix in templates_to_generate:
        # Render the template to HTML string
        rendered_html = render_template(tpl, **context)
        # Phase 6: cross-reference pass (list renumbering + annexure enclosures).
        rendered_html = post_process_pdf_html(rendered_html, adjudication_id=adj.id)

        # Compile HTML string to PDF using WeasyPrint in memory
        pdf_bytes, error = generate_pdf_from_html(rendered_html)
        if pdf_bytes:
            outputs.append((f"{prefix}.pdf", pdf_bytes))
        else:
            current_app.logger.error(f"PDF generation failed for {tpl}: {error}")
            return (
                jsonify(
                    {
                        "error": f"PDF generation failed: {error}. Documents cannot be generated without WeasyPrint.",
                    }
                ),
                500,
            )

    # Zip the outputs in memory
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
