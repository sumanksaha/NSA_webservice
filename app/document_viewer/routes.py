"""Routes for the document viewer: auto-save, save-to-PDF, image upload, Markdown export.

Phase 3: ``/save/<case_id>`` accepts edited HTML (and optional Quill Delta),
validates the case exists, writes the HTML (+ Delta) to the instance folder,
generates a PDF via WeasyPrint, and returns the PDF as a download.
CSRF protection is enforced globally by Flask-WTF.

Phase 1 (auto-save): ``/autosave/<case_id>`` accepts HTML + Delta (JSON),
saves both to disk WITHOUT generating a PDF, and returns a lightweight JSON
acknowledgement. Triggered by the debounced ``text-change`` listener in
``editor.js``.

Delta storage: Both ``/save`` and ``/autosave`` persist the Quill Delta as a
``.delta`` file alongside the ``.html`` file. The ``/saved`` endpoint returns
JSON ``{"html": "...", "delta": {...}|null}`` for lossless round-trip restore.

Phase 2: ``/upload_image`` accepts a multipart image and stores it under
``instance/editor_images/``; ``/image/<filename>`` serves it back (path-
traversal safe); ``/export_markdown`` converts the editor Delta (or HTML)
to Markdown for download.

The GET editor page routes remain in ``case_file_generator`` and
``adjudication`` blueprints (at ``/case_file_generator/<id>/editor``
and ``/adjudication/<id>/editor`` respectively), since they depend on
blueprint-specific context.  The save endpoint is shared.
"""

import io
import json
import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from flask import (
    current_app,
    jsonify,
    request,
    send_file,
    url_for,
)
from flask_login import current_user

from app.document_viewer import document_viewer_bp
from app.document_viewer.markdown_export import delta_to_markdown, html_to_markdown
from app.services.audit import log_audit
from app.utils.pdf_utils import generate_pdf_from_html

# ---------------------------------------------------------------------------
# Editor image upload (Phase 2) — stored under instance/editor_images/
# ---------------------------------------------------------------------------
_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp"})
_MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
_IMAGE_DIR_NAME = "editor_images"

# --- Shared helpers ---

_VALID_DOC_TYPES = ("petition", "permission")


def _resolve_case(case_id: int):
    """Look up a CaseFile or Adjudication by primary key."""
    from app.models import Adjudication, CaseFile

    case_record = CaseFile.query.get(case_id)
    if case_record is not None:
        return case_record, "case_file", case_record.case_number

    case_record = Adjudication.query.get(case_id)
    if case_record is not None:
        return case_record, "adjudication", case_record.case_number

    return None, None, None


def _save_document_content(case_id, html_content, delta_content, doc_type):
    """Persist HTML (+ optional Delta) to instance/saved/."""
    timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    saved_dir = Path(current_app.instance_path) / "saved"
    saved_dir.mkdir(parents=True, exist_ok=True)

    html_filename = f"{case_id}_{doc_type}_{timestamp_str}.html"
    html_path = saved_dir / html_filename
    html_path.write_text(html_content, encoding="utf-8")

    if delta_content is not None:
        delta_filename = f"{case_id}_{doc_type}_{timestamp_str}.delta"
        delta_path = saved_dir / delta_filename
        try:
            delta_text = delta_content if isinstance(delta_content, str) else json.dumps(delta_content)
            delta_path.write_text(delta_text, encoding="utf-8")
        except (TypeError, ValueError, OSError) as exc:
            current_app.logger.warning("Failed to save delta for case %s: %s", case_id, exc)

    return timestamp_str


def _log_audit(case_type, case_id, action, actor, **details):
    """Best-effort audit logging."""
    try:
        log_audit(entity_type=case_type, entity_id=str(case_id), action=action, actor=actor, details=details)
    except Exception:
        current_app.logger.warning("Audit log write failed for case %s; continuing.", case_id)


def _actor():
    """Return the current user's username or 'anonymous'."""
    return current_user.username if current_user.is_authenticated and current_user.is_active else "anonymous"


@document_viewer_bp.route("/autosave/<int:case_id>", methods=["POST"])
def autosave_document(case_id: int):
    """Lightweight auto-save: store HTML + Quill Delta without PDF generation.

    Body (JSON): {"html": "...", "delta": {...}, "doc_type": "petition"|"permission"}
    Returns 200 with JSON {"status": "ok", "timestamp": "...", "has_delta": bool}.
    """
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    html_content = payload.get("html", "").strip()
    if not html_content:
        return jsonify({"error": "No HTML content provided"}), 400

    doc_type = payload.get("doc_type", "permission")
    if doc_type not in _VALID_DOC_TYPES:
        return jsonify({"error": "doc_type must be 'petition' or 'permission'"}), 400

    delta_content = payload.get("delta")

    case_record, case_type, _case_label = _resolve_case(case_id)
    if case_record is None:
        return jsonify({"error": f"Case with ID {case_id} not found"}), 404

    try:
        timestamp_str = _save_document_content(case_id, html_content, delta_content, doc_type)
    except OSError as exc:
        current_app.logger.error("Failed to auto-save for case %s: %s", case_id, exc)
        return jsonify({"error": "Could not auto-save document"}), 500

    _log_audit(
        case_type,
        case_id,
        action=f"DOCUMENT_AUTOSAVED_{doc_type.upper()}",
        actor=_actor(),
        has_delta=delta_content is not None,
        timestamp=timestamp_str,
    )

    return jsonify({"status": "ok", "timestamp": timestamp_str, "has_delta": delta_content is not None})


@document_viewer_bp.route("/save/<int:case_id>", methods=["POST"])
def save_document(case_id: int):
    """Accept edited HTML from the Quill editor, save it, and return a PDF.

    Body (JSON): ``{"html": "<edited HTML>", "doc_type": "petition"|"permission"}``

    - Validates the case exists (CaseFile or Adjudication).
        - Writes the HTML (+ optional Delta) to ``instance/saved/``.
    - Converts HTML to PDF via the existing ``generate_pdf_from_html`` pipeline.
    - Logs an audit event.
    - Returns the PDF as a file download, or a JSON error on failure.

    CSRF protection is enforced automatically by Flask-WTF on all POST routes.
    The CSRF token is provided by the ``<meta name="csrf-token">`` tag in
    ``base.html`` and is read by the global fetch wrapper in ``base.html``.
    """
    # --- Validate request body ---
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    html_content = payload.get("html", "").strip()
    if not html_content:
        return jsonify({"error": "No HTML content provided"}), 400

    doc_type = payload.get("doc_type", "permission")
    if doc_type not in _VALID_DOC_TYPES:
        return jsonify({"error": "doc_type must be 'petition' or 'permission'"}), 400

    delta_content = payload.get("delta")

    # --- Determine case type (CaseFile vs Adjudication) ---
    case_record, case_type, case_label = _resolve_case(case_id)
    if case_record is None:
        return jsonify({"error": f"Case with ID {case_id} not found"}), 404

    # --- Save edited HTML (+ optional Delta) to instance folder ---
    try:
        timestamp_str = _save_document_content(case_id, html_content, delta_content, doc_type)
    except OSError as exc:
        current_app.logger.error("Failed to save HTML for case %s: %s", case_id, exc)
        return jsonify({"error": "Could not save edited document"}), 500

    # --- Generate PDF from edited HTML ---
    pdf_bytes, pdf_error = generate_pdf_from_html(html_content)
    if pdf_bytes is None:
        current_app.logger.error("PDF generation failed for case %s: %s", case_id, pdf_error)
        return jsonify({"error": f"PDF generation failed: {pdf_error}"}), 500

        # --- Audit log ---
    _log_audit(
        case_type,
        case_id,
        action=f"DOCUMENT_EDITED_{doc_type.upper()}",
        actor=_actor(),
        has_delta=delta_content is not None,
    )

    # --- Return PDF as file download ---
    pdf_filename = f"{case_label}_{doc_type}_{timestamp_str}.pdf"
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_filename,
    )


@document_viewer_bp.route("/upload_image", methods=["POST"])
def upload_image():
    """Accept an image upload from the Quill editor and return a serveable URL.

    Multipart form field: ``image`` (the file).  Validates the extension and
    size before writing, stores the file under ``instance/editor_images/``
    with a random name, and returns ``{"url": ...}`` for insertion into the
    document via ``quill.insertEmbed``.

    Returns 201 with JSON on success, or 400 with an error message.
    """
    upload_file = request.files.get("image") or request.files.get("file")
    if upload_file is None or not upload_file.filename:
        return jsonify({"error": "No image file provided"}), 400

    ext = Path(upload_file.filename).suffix.lower()
    if ext not in _IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(e.lstrip(".") for e in _IMAGE_EXTENSIONS))
        return jsonify({"error": f"Unsupported image type. Allowed: {allowed}"}), 400

    # Reject oversized uploads BEFORE writing to disk.
    try:
        upload_file.seek(0, os.SEEK_END)
        file_size = upload_file.tell()
        upload_file.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        return jsonify({"error": f"Unable to determine file size: {exc}"}), 400
    if file_size > _MAX_IMAGE_SIZE:
        return jsonify({"error": "Image exceeds the 5 MB size limit."}), 400

    # Validate the payload is a decodable image (rejects polyglot/HTML files
    # renamed with an image extension). PIL is already a project dependency.
    try:
        from PIL import Image as PILImage

        upload_file.seek(0)
        with PILImage.open(upload_file) as pil_img:
            pil_img.verify()
        upload_file.seek(0)
    except Exception as exc:
        return jsonify({"error": f"File is not a valid image: {exc}"}), 400

    images_dir = Path(current_app.instance_path) / _IMAGE_DIR_NAME
    images_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid4().hex}{ext}"
    try:
        upload_file.save(str(images_dir / stored_name))
    except OSError as exc:
        return jsonify({"error": f"Could not store image: {exc}"}), 500

    url = url_for("document_viewer.editor_image", filename=stored_name)
    return jsonify({"status": "ok", "url": url}), 201


@document_viewer_bp.route("/image/<path:filename>")
def editor_image(filename: str):
    """Serve an uploaded editor image (path-traversal safe).

    Only files stored in ``instance/editor_images/`` with our generated
    (hex-name) format are served; the basename is used so ``../`` segments
    can never escape the directory.
    """
    images_dir = Path(current_app.instance_path) / _IMAGE_DIR_NAME
    safe_name = Path(filename).name
    stem = safe_name.rsplit(".", 1)[0] if "." in safe_name else safe_name
    # Reject anything that is not a generated hex name + allowed extension.
    if not (re.fullmatch(r"[0-9a-f]{32}", stem) and Path(safe_name).suffix.lower() in _IMAGE_EXTENSIONS):
        return jsonify({"error": "Image not found"}), 404

    path = images_dir / safe_name
    if not path.is_file():
        return jsonify({"error": "Image not found"}), 404

    mimetype = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return send_file(path, mimetype=mimetype)


@document_viewer_bp.route("/export_markdown", methods=["POST"])
def export_markdown():
    """Convert the current editor content to Markdown (Phase 2).

    Body (JSON): ``{"delta": {...}, "html": "..."}`` — Delta is preferred
    (lossless); HTML is used as a fallback when Delta is absent.

    Returns 200 with JSON ``{"markdown": "...", "filename": "..."}``.
    """
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"error": "Request body must be JSON"}), 400

    delta = payload.get("delta")
    html = payload.get("html") or ""

    if delta:
        markdown = delta_to_markdown(delta)
    elif html.strip():
        markdown = html_to_markdown(html)
    else:
        return jsonify({"error": "No document content provided"}), 400

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return jsonify({
        "status": "ok",
        "markdown": markdown,
        "filename": f"document_{timestamp}.md",
    })


@document_viewer_bp.route("/saved/<int:case_id>/<doc_type>", methods=["GET"])
def get_saved_document(case_id: int, doc_type: str):
    """Return the most recently saved HTML for a given case and doc type.

    Used by the editor page to implement session restore: when the user
    reopens the editor, saved edits (if any) are loaded instead of the
    fresh template render.

        Returns 200 with JSON, or 404 if no saved version exists.
    """
    if doc_type not in _VALID_DOC_TYPES:
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

    # Look for a corresponding .delta file (same timestamp in filename)
    delta_content = None
    delta_path = latest_path.with_suffix(".delta")
    if delta_path.exists():
        try:
            delta_content = json.loads(delta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            current_app.logger.warning("Failed to read delta for case %s: %s", case_id, exc)
            delta_content = None

    return jsonify({"html": html_content, "delta": delta_content})
