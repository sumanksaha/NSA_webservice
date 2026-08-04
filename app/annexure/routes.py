"""Routes for annexure management: list, upload, replace, rename, delete, reorder, download."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from flask import (
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)
from flask_login import current_user

from app.annexure import annexure_bp
from app.annexure.metadata import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    allowed_extension,
    compute_sha256,
    extract_image_text,
    extract_page_count,
    extract_text,
    mime_type,
)
from app.extensions import db
from app.services.audit import log_audit

logger = logging.getLogger(__name__)

_SAVED_DIR_NAME = "annexures"

_SINGLE_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _annexure_dir() -> Path:
    """Return (creating if needed) the instance folder where files live."""
    path = Path(current_app.instance_path) / _SAVED_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _actor() -> str:
    """Return the current user's username or 'anonymous'."""
    return current_user.username if current_user.is_authenticated and current_user.is_active else "anonymous"


def _log_audit(annexure_id: str, action: str, **details) -> None:
    """Best-effort audit logging — never fails an annexure operation."""
    try:
        log_audit(
            entity_type="annexure",
            entity_id=annexure_id,
            action=action,
            actor=_actor(),
            details=details,
        )
    except Exception:
        logger.warning("Audit log write failed for annexure %s (%s); continuing.", annexure_id, action)


def _next_annexure_letter(case_id, adjudication_id) -> str | None:
    """Return the next free annexure letter (A, B, C, ...) for a case.

    Letters are scoped per case: the first annexure gets ``A``, the second
    ``B``, and so on. Returns ``None`` when the alphabet is exhausted (26+).
    """
    from app.models import Annexure

    filters = []
    if case_id:
        filters.append(Annexure.case_id == case_id)
    if adjudication_id:
        filters.append(Annexure.adjudication_id == adjudication_id)
    if not filters:
        return None

    used = {a.annexure_letter for a in Annexure.query.filter(db.or_(*filters)).all() if a.annexure_letter}
    for letter in _SINGLE_LETTERS:
        if letter not in used:
            return letter
    return None


@annexure_bp.route("/")
def index():
    """List annexures, optionally filtered by case/adjudication."""
    from app.models import Annexure

    case_id = request.args.get("case_id", type=int)
    adjudication_id = request.args.get("adjudication_id", type=int)

    query = Annexure.query.order_by(Annexure.uploaded_at.desc())
    if case_id:
        query = query.filter(Annexure.case_id == case_id)
    if adjudication_id:
        query = query.filter(Annexure.adjudication_id == adjudication_id)

    annexures = query.all()
    return render_template(
        "annexure/index.html",
        annexures=annexures,
        case_id=case_id,
        adjudication_id=adjudication_id,
    )


@annexure_bp.route("/upload", methods=["POST"])
def upload():
    """Accept a multipart annexure upload and extract metadata.

    Form fields:
        file             — the uploaded document (required)
        caption          — display caption (defaults to file stem)
        tags             — optional comma-separated tags
        case_id          — optional CaseFile id to attach to
        adjudication_id  — optional Adjudication id to attach to

    Returns JSON with the created annexure, or an error status.
    """
    from app.models import Adjudication, Annexure, CaseFile

    upload_file = request.files.get("file")
    if upload_file is None or not upload_file.filename:
        return jsonify({"error": "No file provided"}), 400

    if not allowed_extension(upload_file.filename):
        allowed = ", ".join(sorted(ext.lstrip(".") for ext in ALLOWED_EXTENSIONS))
        return jsonify({"error": f"Unsupported file type. Allowed: {allowed}"}), 400

    case_id = request.form.get("case_id", type=int)
    adjudication_id = request.form.get("adjudication_id", type=int)
    caption = (request.form.get("caption") or "").strip() or Path(upload_file.filename).stem
    tags = (request.form.get("tags") or "").strip()

    if not case_id and not adjudication_id:
        return jsonify({"error": "Attach the annexure to a case file or an adjudication."}), 400

    # Validate the referenced parent record exists.
    if case_id and db.session.get(CaseFile, case_id) is None:
        return jsonify({"error": f"Case file {case_id} not found."}), 404
    if adjudication_id and db.session.get(Adjudication, adjudication_id) is None:
        return jsonify({"error": f"Adjudication {adjudication_id} not found."}), 404

    # Reject oversized uploads BEFORE writing to disk (matches storage.py).
    try:
        upload_file.seek(0, os.SEEK_END)
        file_size = upload_file.tell()
        upload_file.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        return jsonify({"error": f"Unable to determine file size: {exc}"}), 400
    if file_size > MAX_FILE_SIZE:
        return jsonify({"error": "File exceeds the 20 MB size limit."}), 400

    # Persist the uploaded file under a random name to avoid collisions.
    ext = Path(upload_file.filename).suffix.lower()
    stored_name = f"{uuid4().hex}{ext}"
    stored_path = _annexure_dir() / stored_name
    upload_file.save(str(stored_path))

    try:
        file_hash = compute_sha256(stored_path)

        # Duplicate detection by content hash.
        existing = Annexure.query.filter_by(file_hash=file_hash).first()
        if existing is not None:
            stored_path.unlink(missing_ok=True)
            return (
                jsonify(
                    {
                        "error": "Duplicate file — identical to annexure " + existing.caption,
                        "duplicate_of": existing.id,
                    }
                ),
                409,
            )

        page_count = extract_page_count(stored_path)

        if ext in {".pdf", ".docx", ".txt"}:
            ocr_text = extract_text(stored_path)
        else:
            ocr_text = extract_image_text(stored_path) if not os.environ.get("SKIP_ANNEXURE_OCR") else None

        letter = _next_annexure_letter(case_id, adjudication_id)

        annexure = Annexure(
            case_id=case_id,
            adjudication_id=adjudication_id,
            caption=caption,
            date=datetime.now(UTC),
            file_hash=file_hash,
            page_count=page_count,
            ocr_text=ocr_text,
            tags=tags,
            filepath=str(stored_path),
            filename=upload_file.filename,
            file_size=file_size,
            mime_type=mime_type(upload_file.filename),
            annexure_letter=letter,
        )
        db.session.add(annexure)
        db.session.commit()

        _log_audit(
            annexure.id,
            "ANNEXURE_UPLOADED",
            filename=annexure.filename,
            file_hash=file_hash[:16],
            letter=letter,
            page_count=page_count,
        )

        return (
            jsonify(
                {
                    "status": "ok",
                    "annexure_id": annexure.id,
                    "caption": annexure.caption,
                    "annexure_letter": letter,
                    "page_count": page_count,
                }
            ),
            201,
        )
    except Exception as exc:
        # Audit logging is best-effort, so any exception here occurs before
        # the DB commit — the stored file can be safely removed.
        stored_path.unlink(missing_ok=True)
        logger.exception("Annexure upload failed")
        return jsonify({"error": f"Upload failed: {exc}"}), 500


@annexure_bp.route("/<annexure_id>/replace", methods=["POST"])
def replace(annexure_id: str):
    """Replace the stored file of an existing annexure with a new upload.

    Form fields (multipart):
        file    — the replacement document (required)
        caption — optional new caption (defaults to keeping the current one)
        tags    — optional new tags (defaults to keeping the current ones)

    Metadata (hash, page count, OCR text, size, MIME) is re-extracted from the
    new file and the annexure record is updated in place — its id and assigned
    letter stay the same so references in documents remain valid. The old
    stored file is removed best-effort after the DB update succeeds.
    """
    from app.models import Annexure

    annexure = db.session.get(Annexure, annexure_id)
    if annexure is None:
        return jsonify({"error": "Annexure not found"}), 404

    upload_file = request.files.get("file")
    if upload_file is None or not upload_file.filename:
        return jsonify({"error": "No file provided"}), 400

    if not allowed_extension(upload_file.filename):
        allowed = ", ".join(sorted(ext.lstrip(".") for ext in ALLOWED_EXTENSIONS))
        return jsonify({"error": f"Unsupported file type. Allowed: {allowed}"}), 400

    caption = (request.form.get("caption") or "").strip() or annexure.caption
    tags = (request.form.get("tags") or "").strip() or (annexure.tags or "")

    # Reject oversized uploads BEFORE writing to disk (matches storage.py).
    try:
        upload_file.seek(0, os.SEEK_END)
        file_size = upload_file.tell()
        upload_file.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        return jsonify({"error": f"Unable to determine file size: {exc}"}), 400
    if file_size > MAX_FILE_SIZE:
        return jsonify({"error": "File exceeds the 20 MB size limit."}), 400

    # Persist the replacement file under a fresh random name.
    ext = Path(upload_file.filename).suffix.lower()
    stored_name = f"{uuid4().hex}{ext}"
    stored_path = _annexure_dir() / stored_name
    upload_file.save(str(stored_path))

    old_filepath = annexure.filepath
    committed = False
    try:
        file_hash = compute_sha256(stored_path)

        # Duplicate detection by content hash — but the same content as the
        # annexure being replaced (a re-upload) is not a duplicate.
        existing = Annexure.query.filter(
            Annexure.id != annexure.id,
            Annexure.file_hash == file_hash,
        ).first()
        if existing is not None:
            stored_path.unlink(missing_ok=True)
            return (
                jsonify(
                    {
                        "error": "Duplicate file — identical to annexure " + existing.caption,
                        "duplicate_of": existing.id,
                    }
                ),
                409,
            )

        page_count = extract_page_count(stored_path)

        if ext in {".pdf", ".docx", ".txt"}:
            ocr_text = extract_text(stored_path)
        else:
            ocr_text = extract_image_text(stored_path) if not os.environ.get("SKIP_ANNEXURE_OCR") else None

        annexure.caption = caption
        annexure.tags = tags
        annexure.date = datetime.now(UTC)
        annexure.file_hash = file_hash
        annexure.page_count = page_count
        annexure.ocr_text = ocr_text
        annexure.filepath = str(stored_path)
        annexure.filename = upload_file.filename
        annexure.file_size = file_size
        annexure.mime_type = mime_type(upload_file.filename)
        db.session.commit()
        committed = True

        # Remove the old file best-effort after the DB update is committed.
        try:
            if old_filepath:
                Path(old_filepath).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete replaced annexure file %s: %s", old_filepath, exc)

        _log_audit(
            annexure.id,
            "ANNEXURE_REPLACED",
            filename=annexure.filename,
            file_hash=file_hash[:16],
            letter=annexure.annexure_letter,
            page_count=page_count,
        )

        return (
            jsonify(
                {
                    "status": "ok",
                    "annexure_id": annexure.id,
                    "caption": annexure.caption,
                    "annexure_letter": annexure.annexure_letter,
                    "page_count": page_count,
                }
            ),
            200,
        )
    except Exception as exc:
        # If the DB update did not commit, the new file is orphaned — remove it.
        # After a successful commit the new path is authoritative, so keep it.
        if not committed:
            stored_path.unlink(missing_ok=True)
        logger.exception("Annexure replace failed")
        return jsonify({"error": f"Replace failed: {exc}"}), 500


@annexure_bp.route("/<annexure_id>/rename", methods=["POST"])
def rename(annexure_id: str):
    """Rename an annexure caption (JSON: ``{"caption": "..."}``)."""
    from app.models import Annexure

    payload = request.get_json(silent=True) or {}
    caption = (payload.get("caption") or "").strip()
    if not caption:
        return jsonify({"error": "Caption is required"}), 400

    annexure = db.session.get(Annexure, annexure_id)
    if annexure is None:
        return jsonify({"error": "Annexure not found"}), 404

    annexure.caption = caption
    db.session.commit()

    _log_audit(annexure.id, "ANNEXURE_RENAMED", caption=caption)
    return jsonify({"status": "ok", "caption": caption})


@annexure_bp.route("/<annexure_id>/reorder", methods=["POST"])
def reorder(annexure_id: str):
    """Set an annexure letter explicitly (JSON: ``{"annexure_letter": "B"}``)."""
    from app.models import Annexure

    payload = request.get_json(silent=True) or {}
    letter = (payload.get("annexure_letter") or "").strip().upper()
    if letter not in _SINGLE_LETTERS:
        return jsonify({"error": "annexure_letter must be a single letter A-Z"}), 400

    annexure = db.session.get(Annexure, annexure_id)
    if annexure is None:
        return jsonify({"error": "Annexure not found"}), 404

    # Reject letter collisions with sibling annexures on the same case.
    sibling = Annexure.query.filter(
        Annexure.id != annexure.id,
        Annexure.annexure_letter == letter,
        db.or_(
            (Annexure.case_id == annexure.case_id) if annexure.case_id else db.false(),
            (Annexure.adjudication_id == annexure.adjudication_id) if annexure.adjudication_id else db.false(),
        ),
    ).first()
    if sibling is not None:
        return (
            jsonify(
                {
                    "error": f"Annexure letter '{letter}' is already used by '{sibling.caption}' on this case.",
                }
            ),
            409,
        )

    annexure.annexure_letter = letter
    db.session.commit()

    _log_audit(annexure.id, "ANNEXURE_REORDERED", annexure_letter=letter)
    return jsonify({"status": "ok", "annexure_letter": letter})


@annexure_bp.route("/<annexure_id>/delete", methods=["POST"])
def delete(annexure_id: str):
    """Delete an annexure record and its stored file."""
    from app.models import Annexure

    annexure = db.session.get(Annexure, annexure_id)
    if annexure is None:
        return jsonify({"error": "Annexure not found"}), 404

    # Remove the stored file best-effort; the DB record is authoritative.
    try:
        Path(annexure.filepath).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not delete annexure file %s: %s", annexure.filepath, exc)

    db.session.delete(annexure)
    db.session.commit()
    _log_audit(annexure.id, "ANNEXURE_DELETED", filename=annexure.filename)
    return jsonify({"status": "ok"})


@annexure_bp.route("/<annexure_id>/download")
def download(annexure_id: str):
    """Stream the original stored annexure file."""
    from app.models import Annexure

    annexure = db.session.get(Annexure, annexure_id)
    if annexure is None:
        return jsonify({"error": "Annexure not found"}), 404

    path = Path(annexure.filepath)
    if not path.is_file():
        return jsonify({"error": "Annexure file is missing from storage"}), 404

    return send_file(
        path,
        mimetype=annexure.mime_type or "application/octet-stream",
        as_attachment=True,
        download_name=annexure.filename,
    )
