"""OCR review-workflow routes (Phase B) + bulk upload (Phase E) — blueprint at ``/ocr``."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import flash, jsonify, redirect, render_template, request, url_for

from app.extensions import db
from app.models import LabTestParameter, OCRDocument
from app.ocr_extraction import ocr_extraction_bp
from app.ocr_extraction.service import apply_field_corrections, correct_lab_parameter
from app.ocr_pipeline.persistence import run_ocr_pipeline

logger = logging.getLogger(__name__)

# Lazy-imported Celery task (the tasks module degrades gracefully when Celery
# is absent — matching the QStash sync-fallback philosophy).
try:
    from app.ocr_pipeline.tasks import process_ocr_document_async
except ImportError:  # pragma: no cover - celery absent
    process_ocr_document_async = None


@ocr_extraction_bp.route("/documents")
def document_list():
    """Review queue: all extracted documents, newest first."""
    docs = db.session.query(OCRDocument).order_by(OCRDocument.created_at.desc()).limit(200).all()
    return render_template("ocr_extraction/documents.html", docs=docs)


@ocr_extraction_bp.route("/documents/<doc_id>/review")
def review(doc_id: str):
    """Editable review form for one extraction."""
    ocr_doc = db.session.get(OCRDocument, doc_id)
    if ocr_doc is None:
        flash("OCR document not found.", "error")
        return redirect(url_for("ocr_extraction.document_list"))

    try:
        payload = json.loads(ocr_doc.extracted_json or "{}")
    except (TypeError, ValueError):
        payload = {}

    params = db.session.query(LabTestParameter).filter_by(ocr_document_id=doc_id).all()
    return render_template(
        "ocr_extraction/review.html",
        doc=ocr_doc,
        fields=payload.get("fields", {}),
        confidence=payload.get("confidence_scores", {}),
        extracted_text=payload.get("extracted_text", ""),
        lab_params=params,
    )


@ocr_extraction_bp.route("/documents/<doc_id>/corrections", methods=["POST"])
def submit_corrections(doc_id: str):
    """Apply ``{field_name: new_value}`` corrections (JSON or form-encoded).

    Writes OCRCorrection rows for every changed field and opens ConflictLog
    entries where a correction disagrees with a lab-report value.
    """
    if request.is_json:
        corrections = request.get_json(silent=True) or {}
    else:
        corrections = {k: v for k, v in request.form.items() if k.startswith("field:")}
        corrections = {k.removeprefix("field:"): v for k, v in corrections.items()}

    try:
        result = apply_field_corrections(doc_id, corrections)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    if request.is_json:
        return jsonify({
            "applied": result.applied,
            "applied_count": result.applied_count,
            "skipped": result.skipped,
            "conflicts_opened": result.conflicts_opened,
        }), 200

    flash(f"Applied {result.applied_count} correction(s); opened {result.conflicts_opened} conflict(s).", "success")
    return redirect(url_for("ocr_extraction.review", doc_id=doc_id))


@ocr_extraction_bp.route("/lab-parameters/<int:param_id>/correct", methods=["POST"])
def correct_param(param_id: int):
    """Correct a single lab-test parameter's observed value."""
    new_value = (request.get_json(silent=True) or {}).get("observed_value") or request.form.get("observed_value", "")
    try:
        param = correct_lab_parameter(param_id, str(new_value))
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"id": param.id, "observed_value": param.observed_value, "source_authority": param.source_authority})


# --------------------------------------------------------------------------- #
# Phase E: Operational modes — historical bulk upload + real-time processing
# --------------------------------------------------------------------------- #


@ocr_extraction_bp.route("/bulk-upload", methods=["POST"])
def bulk_upload():
    """Process an uploaded ZIP of PDFs through the full OCR pipeline.

    Each ``*.pdf`` member is split → extracted → persisted as its own
    ``OCRDocument`` (Phase E acceptance criterion). Behaviour:

    - Celery configured → one ``process_ocr_document_async`` task dispatched
      per PDF; responds 202 with the queued file names.
    - No Celery (dev/free tier) → processed inline, same as the QStash sync
      fallback; responds 200 with per-file results.

    Duplicate files (same SHA-256 already extracted) are skipped and reported.
    """
    import tempfile
    import zipfile

    upload = request.files.get("file") or request.files.get("zip")
    if upload is None or not upload.filename:
        return jsonify({"error": "Attach a ZIP archive in the 'file' field"}), 400
    if not upload.filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip archives are accepted"}), 400

    sample_id_raw = request.form.get("sample_id")
    sample_id = int(sample_id_raw) if sample_id_raw else None

    from app.extensions import db

    queued: list[str] = []
    processed: list[dict] = []
    duplicates: list[str] = []

    # Processing stays INSIDE the temp-dir lifetime: extracted member PDFs
    # must exist on disk while the pipeline reads them.
    with tempfile.TemporaryDirectory(prefix="ocr_bulk_", ignore_cleanup_errors=True) as tmp:
        zip_path = Path(tmp) / "bundle.zip"
        upload.save(str(zip_path))
        try:
            members = _pdf_members(zip_path, tmp)
        except zipfile.BadZipFile:
            return jsonify({"error": "Corrupt or invalid ZIP archive"}), 400

        if not members:
            return jsonify({"error": "No PDF files found in the archive"}), 400

        for pdf_path, member_name in members:
            file_hash = _sha256(pdf_path)
            if _hash_already_extracted(file_hash):
                duplicates.append(member_name)
                continue

            # Guard on the task object, not just celery presence.
            if process_ocr_document_async is not None:
                process_ocr_document_async.delay(str(pdf_path), sample_id=sample_id)
                queued.append(member_name)
            else:
                try:
                    ocr_doc = run_ocr_pipeline(pdf_path, sample_id=sample_id)
                    processed.append({"file": member_name, "document_id": ocr_doc.id})
                except Exception as exc:
                    logger.error("bulk_upload: failed for %s — %s", member_name, exc)
                    processed.append({"file": member_name, "error": str(exc)})
                finally:
                    db.session.remove()

    payload = {
        "total_pdfs": len(members),
        "queued": queued,
        "processed": processed,
        "duplicates_skipped": duplicates,
    }
    if queued and process_ocr_document_async is not None:
        return jsonify({**payload, "status": "queued"}), 202
    return jsonify({**payload, "status": "completed"}), 200


def _pdf_members(zip_path: Path, extract_dir: str) -> list[tuple[Path, str]]:
    """Extract *.pdf members (defence-in-depth: no path traversal) → [(path, name)]."""
    import zipfile

    members: list[tuple[Path, str]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                continue
            safe_name = Path(info.filename).name  # strip any directory components
            target = Path(extract_dir) / safe_name
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            members.append((target, info.filename))
    return members


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_already_extracted(file_hash: str) -> bool:
    from sqlalchemy import exists

    from app.models import OCRDocument

    return db.session.query(exists().where(OCRDocument.file_hash == file_hash)).scalar()
