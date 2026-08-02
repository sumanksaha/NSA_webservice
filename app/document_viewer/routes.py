"""Routes for the document viewer: POST save-to-PDF endpoint.

Phase 3 adds the POST ``/save/<case_id>`` endpoint that accepts edited HTML
from the Quill editor, validates the case exists, writes the HTML to the
instance folder, generates a PDF via WeasyPrint, and returns the PDF as a
download.  CSRF protection is enforced globally by Flask-WTF.

The GET editor page routes remain in ``case_file_generator`` and
``adjudication`` blueprints (at ``/case_file_generator/<id>/editor``
and ``/adjudication/<id>/editor`` respectively), since they depend on
blueprint-specific context.  The save endpoint is shared.
"""

import io
from datetime import datetime
from pathlib import Path

from flask import (
    Response,
    current_app,
    jsonify,
    request,
    send_file,
)
from flask_login import current_user

from app.document_viewer import document_viewer_bp
from app.services.audit import log_audit
from app.utils.pdf_utils import generate_pdf_from_html


@document_viewer_bp.route("/save/<int:case_id>", methods=["POST"])
def save_document(case_id: int):
    """Accept edited HTML from the Quill editor, save it, and return a PDF.

    Body (JSON): ``{"html": "<edited HTML>", "doc_type": "petition"|"permission"}``

    - Validates the case exists (CaseFile or Adjudication).
    - Writes the HTML to ``instance/saved/<case_id>_<doc_type>_<timestamp>.html``.
    - Converts HTML to PDF via the existing ``generate_pdf_from_html`` pipeline.
    - Logs an audit event.
    - Returns the PDF as a file download, or a JSON error on failure.

    CSRF protection is enforced automatically by Flask-WTF on all POST routes.
    The CSRF token is provided by the ``<meta name="csrf-token">`` tag in
    ``base.html`` and is read by the global fetch wrapper in ``base.html``.
    """
    from app.models import Adjudication, CaseFile

    # --- Validate request body ---
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    html_content = payload.get("html", "").strip()
    if not html_content:
        return jsonify({"error": "No HTML content provided"}), 400

    doc_type = payload.get("doc_type", "permission")
    if doc_type not in ("petition", "permission"):
        return jsonify({"error": "doc_type must be 'petition' or 'permission'"}), 400

    # --- Determine case type (CaseFile vs Adjudication) ---
    case_record = CaseFile.query.get(case_id)
    if case_record is not None:
        case_type = "case_file"
        case_label = case_record.case_number
    else:
        case_record = Adjudication.query.get(case_id)
        if case_record is not None:
            case_type = "adjudication"
            case_label = case_record.case_number
        else:
            return jsonify({"error": f"Case with ID {case_id} not found"}), 404

    # --- Save edited HTML to instance folder ---
    timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    html_filename = f"{case_id}_{doc_type}_{timestamp_str}.html"
    saved_dir = Path(current_app.instance_path) / "saved"
    saved_dir.mkdir(parents=True, exist_ok=True)
    html_path = saved_dir / html_filename

    try:
        html_path.write_text(html_content, encoding="utf-8")
    except OSError as exc:
        current_app.logger.error("Failed to save HTML for case %s: %s", case_id, exc)
        return jsonify({"error": "Could not save edited document"}), 500

    # --- Generate PDF from edited HTML ---
    pdf_bytes, pdf_error = generate_pdf_from_html(html_content)
    if pdf_bytes is None:
        current_app.logger.error("PDF generation failed for case %s: %s", case_id, pdf_error)
        return jsonify({"error": f"PDF generation failed: {pdf_error}"}), 500

    # --- Audit log ---
    actor = current_user.username if current_user.is_authenticated and current_user.is_active else "anonymous"
    try:
        log_audit(
            entity_type=case_type,
            entity_id=str(case_id),
            action=f"DOCUMENT_EDITED_{doc_type.upper()}",
            actor=actor,
            details={"html_filename": html_filename, "pdf_bytes": len(pdf_bytes)},
        )
    except Exception:
        current_app.logger.warning("Audit log write failed for case %s; continuing.", case_id)

    # --- Return PDF as file download ---
    pdf_filename = f"{case_label}_{doc_type}_{timestamp_str}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_filename,
    )


@document_viewer_bp.route("/saved/<int:case_id>/<doc_type>", methods=["GET"])
def get_saved_document(case_id: int, doc_type: str):
    """Return the most recently saved HTML for a given case and doc type.

    Used by the editor page to implement session restore: when the user
    reopens the editor, saved edits (if any) are loaded instead of the
    fresh template render.

    Returns 200 with the HTML text, or 404 if no saved version exists.
    """
    if doc_type not in ("petition", "permission"):
        return jsonify({"error": "doc_type must be 'petition' or 'permission'"}), 400

    saved_dir = Path(current_app.instance_path) / "saved"
    if not saved_dir.is_dir():
        return jsonify({"error": "No saved version found"}), 404

    # Find the most recent file matching the pattern <case_id>_<doc_type>_<timestamp>.html
    pattern = f"{case_id}_{doc_type}_*.html"
    saved_files = sorted(saved_dir.glob(pattern), reverse=True)
    if not saved_files:
        return jsonify({"error": "No saved version found"}), 404

    latest_path = saved_files[0]
    try:
        html_content = latest_path.read_text(encoding="utf-8")
    except OSError as exc:
        current_app.logger.error("Failed to read saved HTML for case %s: %s", case_id, exc)
        return jsonify({"error": "Could not read saved document"}), 500

    return Response(html_content, mimetype="text/html")
