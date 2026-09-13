"""Document generation / regeneration pipelines (split from DocumentCaseManager).

Each function takes the model, ``case_type``, and the model-specific
callbacks explicitly — no manager instance required.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any

from flask import current_app, jsonify, render_template, request, send_file
from sqlalchemy import or_
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import Evidence
from app.services.audit_context import audit_logger
from app.services.sync_orchestrator import sync_row
from app.shared import document_lookup as lookup
from app.utils.pdf_utils import embed_photos_as_base64, generate_pdf_from_html, post_process_pdf_html
from app.utils.qstash_client import make_dedup_key, publish_task

logger = logging.getLogger(__name__)


def build_photos_context(case_id: int, context: dict) -> dict:
    """Fetch photos and embed as base64 for template rendering."""
    all_photos = (
        Evidence.query
        .filter(
            Evidence.evidence_type == "photo",
            or_(Evidence.case_id == case_id, Evidence.adjudication_id == case_id),
        )
        .order_by(Evidence.captured_at.asc())
        .all()
    )

    include_flagged = request.args.get("include_flagged", "false").lower() == "true"
    flag_override_reason = request.args.get("flag_override_reason", "").strip()

    verified_photos = [p for p in all_photos if p.verification_status == "PASS"]
    flagged_photos = [p for p in all_photos if p.verification_status == "FLAG"]

    if include_flagged:
        if not flag_override_reason:
            return jsonify({"error": "flag_override_reason is required when include_flagged=true"}), 400
        final_photos = verified_photos + flagged_photos
        flagged_image_ids = [p.id for p in flagged_photos]
        if flagged_image_ids:
            audit_logger("photo").log(
                ",".join(flagged_image_ids),
                "FLAGGED_PHOTO_INCLUDED",
                actor=context.get("food_safety_officer_name", "unknown"),
                reason=flag_override_reason,
            )
    else:
        final_photos = verified_photos

    return {
        "photos": final_photos,
        "photo_embeds": embed_photos_as_base64([p.filepath for p in final_photos]),
    }


def templates_to_generate(case_type: str, context: dict):
    """Return list of (template, prefix) tuples, or a JSON error tuple.

    For case_file_generator the templates are fixed. Adjudication uses
    pre-auth vs non-pre-auth selection.
    """
    if case_type == "case_file":
        return [
            ("case_file_generator/petition.html", "Petition"),
            ("case_file_generator/permission_letter.html", "Permission_Letter"),
        ]
    # Adjudication
    is_pre_auth = str(context.get("pre_authorization", "no")).strip().lower() == "yes"
    if is_pre_auth:
        return [("adjudication/Legal_NonsampleAdjudication_Template.html", "Permission_Letter")]
    if not context.get("authorization_date"):
        return jsonify({"error": "authorization_date is required for non-pre-authorization cases."}), 400
    return [("adjudication/template_nonsample_petition.html", "Petition")]


def build_zip_response(outputs: list[tuple[str, bytes]], case_id: int, case_type: str) -> Any:
    """Build an in-memory ZIP response from generated PDFs."""
    zip_prefix = "Case" if case_type == "case_file" else "Petition"
    if case_type != "case_file":
        is_pre_auth = str(request.form.get("pre_authorization", "no")).strip().lower() == "yes"
        zip_prefix = "PermissionLetter" if is_pre_auth else "Petition"

    case_number = outputs[0][0].replace(".pdf", "") if outputs else str(case_id)
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, data in outputs:
            z.writestr(fname, data)
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        as_attachment=True,
        download_name=f"{zip_prefix}_Case_{case_number}_Regenerated.zip",
        mimetype="application/zip",
    )


def log_generation(case_type: str, case_id: int, form_data: dict) -> None:
    """Log audit entry for document generation."""
    image_ids = form_data.get("_photo_image_ids", [])
    statuses = form_data.get("_photo_statuses", [])
    if image_ids:
        audit_logger("adjudication_order" if case_type == "adjudication" else "case_file").log(
            str(case_id),
            "ADJUDICATION_ORDER_REGENERATED" if case_type == "adjudication" else "CASE_FILE_REGENERATED",
            actor=form_data.get("food_safety_officer_name", "unknown"),
            image_ids=image_ids,
            statuses=statuses,
        )


def regenerate(
    model: type,
    case_type: str,
    model_to_dict_fn,
    process_form_fn,
    prepare_context_fn,
    case_id: int,
    context_overrides: dict | None = None,
) -> Any:
    """Regenerate documents from an existing case.

    Returns a Flask ``send_file`` response (ZIP in memory) or a JSON
    error response.
    """
    case = lookup.get_case(model, case_id)
    if case is None:
        return jsonify({"error": f"Case with id {case_id} not found"}), 404

    form_data = model_to_dict_fn(case)
    case_data = process_form_fn(form_data) if process_form_fn else form_data

    context = prepare_context_fn(case_data) if prepare_context_fn else case_data
    if context_overrides:
        context.update(context_overrides)

    context["compilation_date"] = datetime.today().strftime("%d %B %Y")

    # --- Photo evidence integration ---
    context["adjudication"] = build_photos_context(case_id, context)
    log_generation(case_type, case_id, form_data)

    templates = templates_to_generate(case_type, context)
    if isinstance(templates, tuple):
        return templates

    outputs: list[tuple[str, bytes]] = []
    for tpl, prefix in templates:
        rendered_html = render_template(tpl, **context)
        rendered_html = post_process_pdf_html(
            rendered_html,
            case_id=case_id if case_type == "case_file" else None,
            adjudication_id=None if case_type == "case_file" else case_id,
        )
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

    return build_zip_response(outputs, case_id, case_type)


def sync_to_sheets(case_type: str, form_data: dict, record: Any) -> None:
    """Best-effort multi-target sync (Sheets + Airtable + Excel)."""
    allowed = lookup.sheets_columns(case_type)
    try:
        row_dict = {k: v for k, v in form_data.items() if k in allowed}
        row_dict["created_at"] = record.created_at.isoformat() if record.created_at else ""
        sync_row(case_type, row_dict, entity_id=record.id)
    except Exception as exc:
        current_app.logger.warning(f"{case_type}: sync failed: {exc}")


def link_inspection(adj: Any, from_inspection: str) -> None:
    """Link adjudication back to an inspection (adjudication only)."""
    from app.models import Inspection

    try:
        inspection = db.session.get(Inspection, int(from_inspection))
        if inspection and not inspection.adjudication_id and not inspection.is_dismissed:
            today = datetime.now(UTC)
            if inspection.compliance_deadline and inspection.compliance_deadline < today:
                inspection.adjudication_id = adj.id
            try:
                db.session.commit()
            except StaleDataError:
                db.session.rollback()
                current_app.logger.warning(f"Adjudication {adj.id}: StaleDataError linking inspection {from_inspection}")
    except Exception as exc:
        current_app.logger.warning(f"Adjudication: Failed to link inspection {from_inspection}: {exc}")
        db.session.rollback()


def dispatch_case_file_pdf(record: Any, form_data: dict) -> tuple[dict, int]:
    """Dispatch PDF generation via QStash (case_file only)."""
    case_data = record.__dict__ if hasattr(record, "__dict__") else form_data
    payload = {"case_file_id": record.id, "case_data": case_data}
    try:
        dispatched = publish_task(
            "generate_case_file_pdf",
            payload=payload,
            dedup_key=make_dedup_key("generate_case_file_pdf", record.id, payload),
        )
    except Exception as exc:
        current_app.logger.error("Case file PDF dispatch failed: %s", exc)
        return {"error": f"Case file PDF generation failed: {exc}"}, 500

    if dispatched["mode"] == "async":
        return (
            {
                "message": "Case file created; PDF generation queued",
                "case_file_id": record.id,
                "task_id": dispatched["message_id"],
            },
            202,
        )

    result = dispatched["result"]
    if result.get("status") == "error":
        error_msg = result.get("error", "PDF generation failed")
        current_app.logger.error("Case file PDF generation returned error: %s", error_msg)
        return {"error": error_msg}, 500

    return (
        {"message": "Case file created; PDF generated", "case_file_id": record.id, "pdf_result": result},
        200,
    )


def generate_adjudication_pdfs(adj: Any, form_data: dict, prepare_context_fn) -> tuple[dict, int]:
    """Generate adjudication PDFs in-memory synchronously."""
    context = prepare_context_fn(form_data) if prepare_context_fn else form_data
    context["compilation_date"] = datetime.today().strftime("%d %B %Y")
    context["adjudication"] = build_photos_context(adj.id, context)
    log_generation("adjudication", adj.id, form_data)

    templates = templates_to_generate("adjudication", context)
    if isinstance(templates, tuple):
        return templates[0], templates[1]

    outputs: list[tuple[str, bytes]] = []
    for tpl, prefix in templates:
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

    return build_zip_response(outputs, adj.id, "adjudication")


def generate_case(
    model: type,
    case_type: str,
    validate_form_fn,
    process_form_fn,
    prepare_context_fn,
    form_data: dict,
) -> tuple[dict, int]:
    """Create a new case record from form data and dispatch PDF generation.

    For ``case_file`` type: uses QStash async PDF dispatch.
    For ``adjudication`` type: generates PDFs synchronously in-memory.

    Returns ``(metadata_dict, status_code)``.
    """
    if validate_form_fn:
        errors = validate_form_fn(form_data)
        if errors:
            return (
                {"error": "Please correct the highlighted fields below.", "errors": errors},
                400,
            )

    try:
        record = process_form_fn(form_data)
        db.session.add(record)
        db.session.commit()
    except StaleDataError:
        db.session.rollback()
        return (
            jsonify({"error": "This case was modified by another user. Please reload and try again."}),
            409,
        )

    # --- Sheets sync (best-effort) ---
    sync_to_sheets(case_type, form_data, record)

    # --- Link to inspection (adjudication only) ---
    if case_type == "adjudication":
        from_inspection = form_data.get("from_inspection")
        if from_inspection:
            link_inspection(record, from_inspection)

    # --- PDF generation ---
    if case_type == "case_file":
        return dispatch_case_file_pdf(record, form_data)
    return generate_adjudication_pdfs(record, form_data, prepare_context_fn)
