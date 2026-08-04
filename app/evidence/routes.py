"""Routes for evidence management: list, upload, download, thumbnail, update, delete.

Phase 5: unified evidence library on the single ``Evidence`` model with
drag-and-drop multi-file upload, image compression + thumbnails, and
categorization via evidence type + tags.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from uuid import uuid4

from flask import (
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
)
from flask_login import current_user

from app.annexure.metadata import (
    compute_sha256,
    extract_image_text,
    extract_text,
    mime_type,
)
from app.evidence import evidence_bp
from app.evidence.media import (
    THUMB_DIR_NAME,
    compress_image,
    generate_thumbnail,
    is_image_path,
)
from app.extensions import db
from app.services.audit import log_audit

logger = logging.getLogger(__name__)

_SAVED_DIR_NAME = "evidence"
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

_ALLOWED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".tiff",
        ".tif",
        ".docx",
        ".doc",
        ".txt",
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
    }
)

# Extension -> suggested evidence type (overridable via the upload form).
_EXT_TYPE_MAP = {
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
    ".webp": "photo",
    ".gif": "photo",
    ".bmp": "photo",
    ".tiff": "photo",
    ".tif": "photo",
    ".mp4": "video",
    ".mov": "video",
    ".avi": "video",
    ".mkv": "video",
    ".pdf": "report",
    ".docx": "report",
    ".doc": "report",
    ".txt": "report",
}


def _evidence_dir() -> Path:
    """Return (creating if needed) the instance folder where files live."""
    path = Path(current_app.instance_path) / _SAVED_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thumb_dir() -> Path:
    return Path(current_app.instance_path) / THUMB_DIR_NAME


def _actor() -> str:
    """Return the current user's username or 'anonymous'."""
    return current_user.username if current_user.is_authenticated and current_user.is_active else "anonymous"


def _log_audit(evidence_id: str, action: str, **details) -> None:
    """Best-effort audit logging — never fails an evidence operation."""
    try:
        log_audit(
            entity_type="evidence",
            entity_id=evidence_id,
            action=action,
            actor=_actor(),
            details=details,
        )
    except Exception:
        logger.warning("Audit log write failed for evidence %s (%s); continuing.", evidence_id, action)


def _allowed_extension(filename: str) -> bool:
    return Path(filename).suffix.lower() in _ALLOWED_EXTENSIONS


def _suggest_type(filename: str) -> str:
    """Pick a default evidence type from the file extension."""
    return _EXT_TYPE_MAP.get(Path(filename).suffix.lower(), "report")


def _validate_parent_ids(form) -> tuple:
    """Validate optional parent references; raises ValueError on unknown ids."""
    from app.models import Adjudication, CaseFile, Inspection

    case_id = form.get("case_id", type=int)
    adjudication_id = form.get("adjudication_id", type=int)
    inspection_id = form.get("inspection_id", type=int)

    if case_id and db.session.get(CaseFile, case_id) is None:
        raise ValueError(f"Case file {case_id} not found.")
    if adjudication_id and db.session.get(Adjudication, adjudication_id) is None:
        raise ValueError(f"Adjudication {adjudication_id} not found.")
    if inspection_id and db.session.get(Inspection, inspection_id) is None:
        raise ValueError(f"Inspection {inspection_id} not found.")
    return case_id, adjudication_id, inspection_id


@evidence_bp.route("/")
def index():
    """List evidence with optional type / parent / tag / keyword filters."""
    from app.models import Evidence

    evidence_type = request.args.get("evidence_type", "").strip()
    case_id = request.args.get("case_id", type=int)
    adjudication_id = request.args.get("adjudication_id", type=int)
    inspection_id = request.args.get("inspection_id", type=int)
    tag = request.args.get("tag", "").strip()
    q = request.args.get("q", "").strip()

    query = Evidence.query.order_by(Evidence.uploaded_at.desc())
    if evidence_type:
        query = query.filter(Evidence.evidence_type == evidence_type)
    if case_id:
        query = query.filter(Evidence.case_id == case_id)
    if adjudication_id:
        query = query.filter(Evidence.adjudication_id == adjudication_id)
    if inspection_id:
        query = query.filter(Evidence.inspection_id == inspection_id)
    if tag:
        query = query.filter(Evidence.tags.ilike(f"%{tag}%"))
    if q:
        like = f"%{q}%"
        from sqlalchemy import or_

        query = query.filter(
            or_(
                Evidence.caption.ilike(like),
                Evidence.tags.ilike(like),
                Evidence.ocr_text.ilike(like),
                Evidence.filename.ilike(like),
            )
        )

    items = query.all()

    # Tag cloud across all evidence (for categorization).
    tag_counts: dict = {}
    for row in Evidence.query.with_entities(Evidence.tags).all():
        for raw in (row.tags or "").split(","):
            cleaned = raw.strip()
            if cleaned:
                tag_counts[cleaned] = tag_counts.get(cleaned, 0) + 1

    return render_template(
        "evidence/index.html",
        evidence=items,
        evidence_types=Evidence.EVIDENCE_TYPES,
        tag_counts=sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0])),
        active_type=evidence_type,
        active_tag=tag,
        query=q,
        case_id=case_id,
        adjudication_id=adjudication_id,
        inspection_id=inspection_id,
    )


@evidence_bp.route("/upload", methods=["POST"])
def upload():
    """Accept a multi-file multipart evidence upload.

    Form fields:
        files           — one or more uploaded files (required)
        evidence_type   — optional explicit type (defaults from extension)
        caption         — display caption applied to all files (optional)
        tags            — comma-separated tags applied to all files
        case_id / adjudication_id / inspection_id — optional parent records

    Returns JSON: ``{status, results: [{filename, status, evidence_id, caption,
    duplicate_of?, error?}]}``.
    """
    from app.models import Evidence

    upload_files = request.files.getlist("files")
    if not upload_files or all(not f.filename for f in upload_files):
        return jsonify({"error": "No files provided"}), 400

    try:
        case_id, adjudication_id, inspection_id = _validate_parent_ids(request.form)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    explicit_type = (request.form.get("evidence_type") or "").strip()
    if explicit_type and explicit_type not in Evidence.EVIDENCE_TYPES:
        return jsonify({"error": f"Unknown evidence type: {explicit_type}"}), 400

    caption = (request.form.get("caption") or "").strip() or None
    tags = (request.form.get("tags") or "").strip()

    results = []
    for upload_file in upload_files:
        results.append(
            _upload_one(
                upload_file,
                case_id=case_id,
                adjudication_id=adjudication_id,
                inspection_id=inspection_id,
                explicit_type=explicit_type,
                caption=caption,
                tags=tags,
            )
        )

    errors = [r for r in results if r.get("status") == "error"]
    status_code = 201 if not errors else (400 if all(r["status"] == "error" for r in results) else 207)
    return jsonify({"status": "ok" if not errors else "partial", "results": results}), status_code


def _upload_one(upload_file, *, case_id, adjudication_id, inspection_id, explicit_type, caption, tags) -> dict:
    """Persist a single uploaded file as an Evidence row."""
    from app.models import Evidence

    if not upload_file.filename:
        return {"filename": "", "status": "error", "error": "No file selected"}

    filename = upload_file.filename
    if not _allowed_extension(filename):
        return {
            "filename": filename,
            "status": "error",
            "error": "Unsupported file type",
        }

    # Reject oversized uploads BEFORE writing to disk.
    upload_file.stream.seek(0, 2)
    file_size = upload_file.stream.tell()
    upload_file.stream.seek(0)
    if file_size > _MAX_FILE_SIZE:
        return {"filename": filename, "status": "error", "error": "File exceeds the 50 MB size limit"}

    ext = Path(filename).suffix.lower()
    evidence_id = str(uuid4())
    stored_name = f"{evidence_id}{ext}"
    stored_path = _evidence_dir() / stored_name
    upload_file.save(str(stored_path))

    try:
        file_hash = compute_sha256(stored_path)

        # Duplicate detection by content hash.
        existing = Evidence.query.filter_by(file_hash=file_hash).first()
        if existing is not None:
            stored_path.unlink(missing_ok=True)
            return {
                "filename": filename,
                "status": "error",
                "error": "Duplicate file — identical to " + (existing.caption or existing.filename),
                "duplicate_of": existing.id,
            }

        ocr_text = None
        if ext in {".pdf", ".docx", ".doc", ".txt"}:
            ocr_text = extract_text(stored_path)
        elif ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}:
            ocr_text = extract_image_text(stored_path) if not _skip_ocr() else None

        # Compress photos; thumbnails generated below (or on first view).
        stored_path = compress_image(stored_path) or stored_path

        evidence_type = explicit_type or _suggest_type(filename)
        # Compression may rewrite the file to JPEG — serve the on-disk MIME
        # type so downloads carry the correct Content-Type.
        on_disk_mime = mime_type(stored_path.name) if stored_path.exists() else mime_type(filename)
        evidence = Evidence(
            id=evidence_id,
            case_id=case_id,
            adjudication_id=adjudication_id,
            inspection_id=inspection_id,
            evidence_type=evidence_type,
            filepath=str(stored_path),
            filename=filename,
            file_size=stored_path.stat().st_size if stored_path.exists() else file_size,
            mime_type=on_disk_mime,
            file_hash=file_hash,
            ocr_text=ocr_text,
            tags=tags or None,
            caption=caption,
        )

        db.session.add(evidence)
        db.session.commit()

        # Thumbnails only for image evidence (kept on disk, served lazily).
        if evidence_type == "photo" and is_image_path(stored_path):
            generate_thumbnail(stored_path, _thumb_dir(), evidence_id)

        _log_audit(evidence_id, "EVIDENCE_UPLOADED", filename=filename, evidence_type=evidence_type)

        return {
            "filename": filename,
            "status": "ok",
            "evidence_id": evidence_id,
            "caption": caption,
            "evidence_type": evidence_type,
            "file_size": evidence.file_size,
        }
    except Exception as exc:
        db.session.rollback()
        stored_path.unlink(missing_ok=True)
        logger.exception("Evidence upload failed for %s", filename)
        return {"filename": filename, "status": "error", "error": f"Upload failed: {exc}"}


def _skip_ocr() -> bool:
    return os.environ.get("SKIP_ANNEXURE_OCR") == "1"


@evidence_bp.route("/<evidence_id>/download")
def download(evidence_id: str):
    """Download the evidence file (URL-backed files redirect)."""
    from app.models import Evidence

    evidence = db.session.get(Evidence, evidence_id)
    if evidence is None:
        abort(404)

    filepath = str(evidence.filepath)
    if filepath.startswith(("http://", "https://")):
        return redirect(filepath)

    path = Path(filepath)
    if not path.exists():
        abort(404)
    return send_file(
        str(path),
        as_attachment=True,
        download_name=evidence.filename or path.name,
        mimetype=evidence.mime_type,
    )


@evidence_bp.route("/<evidence_id>/thumbnail")
def thumbnail(evidence_id: str):
    """Serve the cached thumbnail for image evidence (generates on demand)."""
    from app.models import Evidence

    evidence = db.session.get(Evidence, evidence_id)
    if evidence is None or evidence.evidence_type != "photo":
        abort(404)

    thumb_path = _thumb_dir() / f"{evidence.id}.jpg"
    if not thumb_path.exists():
        src = Path(evidence.filepath)
        if not src.exists() or not is_image_path(src):
            abort(404)
        generate_thumbnail(src, _thumb_dir(), evidence.id)

    if not thumb_path.exists():
        abort(404)
    return send_file(str(thumb_path), mimetype="image/jpeg")


@evidence_bp.route("/<evidence_id>/update", methods=["POST"])
def update(evidence_id: str):
    """Update caption / tags / evidence type (JSON body)."""
    from app.models import Evidence

    evidence = db.session.get(Evidence, evidence_id)
    if evidence is None:
        return jsonify({"error": f"Evidence {evidence_id} not found"}), 404

    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    caption = data.get("caption")
    tags = data.get("tags")
    evidence_type = data.get("evidence_type")

    if caption is not None:
        evidence.caption = str(caption).strip() or None
    if tags is not None:
        evidence.tags = str(tags).strip() or None
    if evidence_type is not None:
        if evidence_type not in Evidence.EVIDENCE_TYPES:
            return jsonify({"error": f"Unknown evidence type: {evidence_type}"}), 400
        evidence.evidence_type = evidence_type

    db.session.commit()
    _log_audit(evidence_id, "EVIDENCE_UPDATED", caption=evidence.caption, tags=evidence.tags)
    return jsonify({"status": "ok", "evidence_id": evidence.id})


@evidence_bp.route("/<evidence_id>/delete", methods=["POST"])
def delete(evidence_id: str):
    """Delete the evidence row and its files (original + thumbnail)."""
    from app.models import Evidence

    evidence = db.session.get(Evidence, evidence_id)
    if evidence is None:
        return jsonify({"error": f"Evidence {evidence_id} not found"}), 404

    for path in (Path(evidence.filepath), _thumb_dir() / f"{evidence.id}.jpg"):
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            logger.warning("Could not remove evidence file %s", path)

    db.session.delete(evidence)
    db.session.commit()
    _log_audit(evidence_id, "EVIDENCE_DELETED", filename=evidence.filename)
    return jsonify({"status": "ok"})
