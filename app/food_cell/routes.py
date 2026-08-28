"""Food Cell routes — DO Intimation download / HTML view / regenerate.

Also serves Improvement Notice documents (u/s 32 of the FSS Act), which are
always keyed to an *Inspection* (never a Sample). The first render/download
freezes the inspection record via ``Inspection.notice_issued_at``.
"""

from __future__ import annotations

import io
import json
import os
from datetime import UTC, datetime

from flask import (
    abort,
    jsonify,
    render_template,
    send_file,
)
from flask_login import login_required

from app.extensions import db
from app.food_cell import food_cell_bp
from app.food_cell.renderer import DODocumentRenderer
from app.food_cell.word_converter import ImprovementNoticeWordConverter
from app.food_cell.services import generate_and_forward_do_intimation
from app.models.billing import Sample
from app.models.food_cell import DoIntimation
from app.models.inspection import Inspection
from app.shared.context_derivers import derive_actions, derive_violations


@food_cell_bp.route("/do-intimation/<int:sample_id>/pdf")
@login_required
def download_do_intimation_pdf(sample_id: int):
    """Download the DO intimation PDF for *sample_id*."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None or intimation.pdf_url is None:
        abort(404, description="DO intimation PDF not found. Generate it first.")
    pdf_path = intimation.pdf_url
    if not os.path.isfile(pdf_path):
        abort(404, description="DO intimation PDF file not found on disk.")
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"DO_Intimation_{sample_id}.pdf",
        mimetype="application/pdf",
    )


@food_cell_bp.route("/do-intimation/<int:sample_id>/html")
@login_required
def view_do_intimation_html(sample_id: int):
    """View the DO intimation HTML inline in the browser."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None or intimation.html_path is None:
        abort(404, description="DO intimation HTML not found. Generate it first.")
    html_path = intimation.html_path
    if not os.path.isfile(html_path):
        abort(404, description="DO intimation HTML file not found on disk.")
    with open(html_path, encoding="utf-8") as fh:
        html_content = fh.read()
    sample = db.session.get(Sample, sample_id)
    return render_template(
        "food_cell/do_intimation_inline.html",
        html_content=html_content,
        sample=sample,
        intimation=intimation,
    )


@food_cell_bp.route("/do-intimation/<int:sample_id>/regenerate", methods=["POST"])
@login_required
def regenerate_do_intimation(sample_id: int):
    """Force-regenerate the DO intimation for *sample_id* (manual re-render)."""
    sample = db.session.get(Sample, sample_id)
    if sample is None:
        abort(404, description="Sample not found.")
    intimation = generate_and_forward_do_intimation(sample_id, sample=sample, force=True)
    if intimation is None:
        return jsonify({"error": "Failed to generate DO intimation"}), 500
    return jsonify({
        "intimation_id": intimation.id,
        "do_reference_no": intimation.do_reference_no,
        "status": intimation.status,
        "pdf_url": intimation.pdf_url,
        "sync_status": intimation.sync_status,
        "food_cell_forwarded": (intimation.food_cell_forwarded.isoformat() if intimation.food_cell_forwarded else None),
    }), 200


@food_cell_bp.route("/do-intimation/<int:sample_id>/status")
@login_required
def do_intimation_status(sample_id: int):
    """Return JSON status of the DO intimation for *sample_id*."""
    intimation = DoIntimation.query.filter_by(sample_id=sample_id).first()
    if intimation is None:
        return jsonify({"exists": False}), 200
    return (
        jsonify({
            "exists": True,
            "intimation_id": intimation.id,
            "do_reference_no": intimation.do_reference_no,
            "status": intimation.status,
            "food_cell_forwarded": (
                intimation.food_cell_forwarded.isoformat() if intimation.food_cell_forwarded else None
            ),
            "sync_status": intimation.sync_status,
            "has_html": bool(intimation.html_path),
            "has_pdf": bool(intimation.pdf_url),
        }),
        200,
    )


# ------------------------------------------------------------------------- #
# Improvement Notice routes (u/s 32 FSS Act) — inspection-keyed
# ------------------------------------------------------------------------- #


def _inspection_violations(inspection: Inspection) -> list[dict[str, str]]:
    """Derive violations from the inspection's stored checklist."""
    try:
        checklist = json.loads(inspection.checklist_json) if inspection.checklist_json else {}
    except (ValueError, TypeError):
        checklist = {}
    return derive_violations(checklist)


# ponytail: stateless class, one instance per request is fine; share a module
# singleton only if profiling ever says so.
_notice_renderer = DODocumentRenderer()


def _freeze_inspection(inspection: Inspection) -> None:
    """Stamp first-issue time; non-null ``notice_issued_at`` freezes edits."""
    if inspection.notice_issued_at is None:
        inspection.notice_issued_at = datetime.now(UTC)
        db.session.commit()


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/html")
@login_required
def view_improvement_notice_html(inspection_id: int):
    """View the Improvement Notice HTML for an *inspection*."""
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")
    violations = _inspection_violations(inspection)
    if not violations:
        return jsonify({"error": "No violations recorded for this inspection; no notice to issue."}), 400

    actions = derive_actions(violations)
    deadline = inspection.compliance_deadline.strftime("%d/%m/%Y") if inspection.compliance_deadline else None
    html = _notice_renderer.render_improvement_notice_html(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )
    _freeze_inspection(inspection)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/pdf")
@login_required
def download_improvement_notice_pdf(inspection_id: int):
    """Download the Improvement Notice PDF for an *inspection*."""
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")
    violations = _inspection_violations(inspection)
    if not violations:
        return jsonify({"error": "No violations recorded for this inspection; no notice to issue."}), 400

    actions = derive_actions(violations)
    deadline = inspection.compliance_deadline.strftime("%d/%m/%Y") if inspection.compliance_deadline else None
    html = _notice_renderer.render_improvement_notice_html(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )
    pdf_path = _notice_renderer.render_improvement_notice_pdf(html, inspection)
    _freeze_inspection(inspection)
    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=f"Improvement_Notice_{inspection_id}.pdf",
        mimetype="application/pdf",
    )


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/docx")
@login_required
def download_improvement_notice_docx(inspection_id: int):
    """Download the Improvement Notice as a Word (.docx) document."""
    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")
    violations = _inspection_violations(inspection)
    if not violations:
        return jsonify({"error": "No violations recorded for this inspection; no notice to issue."}), 400

    actions = derive_actions(violations)
    deadline = (
        inspection.compliance_deadline.strftime("%d/%m/%Y")
        if inspection.compliance_deadline
        else None
    )
    context = _notice_renderer.build_improvement_notice_context(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )
    converter = ImprovementNoticeWordConverter()
    docx_bytes = converter.build(context)
    _freeze_inspection(inspection)

    buf = io.BytesIO(docx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"Improvement_Notice_{inspection_id}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/save", methods=["POST"])
@login_required
def save_edited_improvement_notice(inspection_id: int):
    """Save an edited Improvement Notice HTML and generate PDF.

    Accepts a ``html`` form field with the full edited HTML document,
    saves it to disk, and generates a PDF from it.
    """
    from flask import request

    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")

    html_content = request.form.get("html", "")
    if not html_content.strip():
        return jsonify({"error": "No HTML content provided."}), 400

    # Save the edited HTML to disk
    html_dir = Path(current_app.instance_path) / "food_cell" / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    html_filename = f"improvement_notice_{inspection_id}_edited_{int(datetime.now(UTC).timestamp())}.html"
    html_path = str(html_dir / html_filename)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    # Generate PDF from the edited HTML
    pdf_path = _notice_renderer.render_improvement_notice_pdf(html_content, inspection)
    _freeze_inspection(inspection)

    return jsonify({
        "message": "Edited notice saved successfully.",
        "html_path": html_path,
        "pdf_path": pdf_path,
    }), 200


@food_cell_bp.route("/improvement-notice/inspection/<int:inspection_id>/email", methods=["POST"])
@login_required
def send_improvement_notice_email_route(inspection_id: int):
    """Send the Improvement Notice via email with .docx attachment."""
    from flask import request as req

    from app.food_cell.email_sender import send_improvement_notice_email
    from app.food_cell.word_converter import ImprovementNoticeWordConverter

    inspection = db.session.get(Inspection, inspection_id)
    if inspection is None:
        abort(404, description="Inspection not found.")

    violations = _inspection_violations(inspection)
    if not violations:
        return jsonify({"error": "No violations recorded for this inspection; no notice to issue."}), 400

    # Parse form fields
    recipient_email = (req.form.get("recipient_email") or "").strip()
    subject = (req.form.get("subject") or "").strip()

    if not recipient_email:
        return jsonify({"error": "Recipient email is required."}), 400
    if not subject:
        subject = f"Improvement Notice — {inspection.fbo_name or 'FBO'} ({inspection.inspection_code})"

    # Build context and generate .docx
    actions = derive_actions(violations)
    deadline = (
        inspection.compliance_deadline.strftime("%d/%m/%Y")
        if inspection.compliance_deadline
        else None
    )
    context = _notice_renderer.build_improvement_notice_context(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )
    converter = ImprovementNoticeWordConverter()
    docx_bytes = converter.build(context)
    docx_filename = f"Improvement_Notice_{inspection_id}.docx"

    # Build HTML email body from the template
    html_body = _notice_renderer.render_improvement_notice_html(
        inspection, violations=violations, actions=actions, compliance_deadline=deadline
    )

    # Embed signature as base64 data URI (file:/// doesn't work in emails)
    from app.food_cell.signature_resolver import get_signature_data_uri

    data_uri = get_signature_data_uri(inspection.fso_name)
    if data_uri:
        import re

        html_body = re.sub(
            r'src="file:///([^"]+)"',
            f'src="{data_uri}"',
            html_body,
        )

    # Build plain-text fallback
    text_body = (
        f"Improvement Notice — {inspection.fbo_name or 'FBO'}\n"
        f"Inspection Code: {inspection.inspection_code}\n"
        f"Date: {context.get('notice_date', '')}\n\n"
        f"Please find the attached Improvement Notice document.\n"
    )

    # Determine FSO name for SMTP config
    fso_name = inspection.fso_name
    if not fso_name:
        return jsonify({"error": "No FSO assigned to this inspection."}), 400

    _freeze_inspection(inspection)

    result = send_improvement_notice_email(
        fso_name=fso_name,
        recipient_email=recipient_email,
        subject=subject,
        html_body=html_body,
        docx_bytes=docx_bytes,
        docx_filename=docx_filename,
        text_body=text_body,
    )

    if result.success:
        return jsonify(result.details | {"message": result.message}), 200
    else:
        return jsonify({"error": result.error}), 400
