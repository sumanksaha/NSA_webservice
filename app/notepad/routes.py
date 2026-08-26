"""Notepad routes — Notes intake queue with AI evaluation."""

from __future__ import annotations

import json

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import Note, NoteEvaluation
from app.shared.config import cfg

notepad_bp = Blueprint("notepad", __name__, template_folder="templates")

#: The seven lens fields every evaluation payload must contain.
_PAYLOAD_KEYS = (
    "summary",
    "implementation_plan",
    "risks",
    "game_theory",
    "talebian",
    "first_principles",
    "feasibility_score",
)


def _ai_service():
    """Factory seam for the AI client (patched in tests)."""
    from app.ai_assistant.service import AIAssistantService

    return AIAssistantService()


@notepad_bp.route("/")
def index():
    """List the current user's notes plus everyone's shared notes."""
    from flask_login import current_user

    notes = (
        Note.query
        .filter((Note.is_shared.is_(True)) | (Note.author_id == current_user.id))
        .order_by(Note.created_at.desc())
        .all()
    )
    return render_template("notepad/list.html", notes=notes)


_MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB upload cap


def _load_document_text(stream, filename: str) -> str:
    """Extract text from an uploaded document via the shared loader (seam for tests)."""
    import tempfile
    from pathlib import Path

    from app.document_loader import DocumentLoaderFactory

    suffix = Path(filename).suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        stream.save(tmp)
        tmp_path = tmp.name
    try:
        return DocumentLoaderFactory.load(tmp_path).text
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@notepad_bp.route("/new", methods=["POST"])
def create():
    """Create a Note from pasted text and/or a PDF upload."""
    from flask_login import current_user

    content_text = (request.form.get("content_text") or "").strip()
    source_type = "pasted"

    pdf = request.files.get("pdf_file")
    if pdf and pdf.filename:
        if (pdf.content_length or 0) > _MAX_PDF_BYTES or len(pdf.read()) > _MAX_PDF_BYTES:
            flash("PDF exceeds the 10 MB limit.", "warning")
            return redirect(url_for("notepad.index"))
        pdf.seek(0)
        try:
            extracted = _load_document_text(pdf.stream, pdf.filename).strip()
        except Exception as exc:
            flash(f"Could not extract PDF text: {exc}", "danger")
            return redirect(url_for("notepad.index"))
        if not content_text:
            content_text = extracted
        elif extracted:
            content_text = f"{content_text}\n\n--- attached: {pdf.filename} ---\n{extracted}"
        source_type = "pdf"

    if not content_text:
        flash("Note content is empty.", "warning")
        return redirect(url_for("notepad.index"))

    note = Note(author_id=current_user.id, content_text=content_text, source_type=source_type)
    db.session.add(note)
    db.session.commit()
    return redirect(url_for("notepad.detail", note_id=note.id))


@notepad_bp.route("/<int:note_id>")
def detail(note_id: int):
    """Show one note. Shared notes are readable by all; private only by the author."""
    from flask_login import current_user

    note = Note.query.get_or_404(note_id)
    if not note.is_shared and note.author_id != current_user.id:
        from flask import abort

        abort(404)  # do not reveal private notes' existence to others

    evaluations = []
    for ev in note.evaluations:
        try:
            payload = json.loads(ev.payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        evaluations.append((ev, payload))
    return render_template("notepad/detail.html", note=note, evaluations=evaluations)


@notepad_bp.route("/<int:note_id>/evaluate", methods=["POST"])
def evaluate(note_id: int):
    """Run one synchronous AI evaluation; appends a NoteEvaluation row."""
    from flask_login import current_user

    if not cfg.notepad_ai_enabled:
        abort(503, description="Notepad AI evaluation is disabled.")

    note = Note.query.get_or_404(note_id)
    if not note.is_shared and note.author_id != current_user.id:
        abort(404)

    service = _ai_service()
    if not service.is_enabled():
        flash("AI assistant is not configured (missing provider/API key).", "warning")
        return redirect(url_for("notepad.detail", note_id=note.id))

    try:
        payload = service.evaluate_note(note.content_text)
    except Exception as exc:  # graceful: never persist a failed verdict
        flash(f"AI evaluation failed: {exc}", "danger")
        return redirect(url_for("notepad.detail", note_id=note.id))

    missing = [k for k in _PAYLOAD_KEYS if k not in payload]
    if missing:
        flash(f"AI returned an incomplete evaluation (missing: {', '.join(missing)}).", "warning")
        return redirect(url_for("notepad.detail", note_id=note.id))

    db.session.add(
        NoteEvaluation(
            note_id=note.id,
            payload=json.dumps(payload),
            provider_model=f"{service.provider}/{service.model}",
        )
    )
    note.status = "evaluated"
    db.session.commit()
    flash("Note evaluated.", "success")
    return redirect(url_for("notepad.detail", note_id=note.id))


@notepad_bp.route("/<int:note_id>/status", methods=["POST"])
def set_status(note_id: int):
    """Transition status. Author-only; 'implemented' requires implemented_note."""
    from flask_login import current_user

    note = Note.query.get_or_404(note_id)
    if note.author_id != current_user.id:
        abort(403)

    new_status = (request.form.get("status") or "").strip()
    if new_status not in ("implemented", "dismissed"):
        flash("Unknown status.", "warning")
        return redirect(url_for("notepad.detail", note_id=note.id))

    trail = (request.form.get("implemented_note") or "").strip()
    if new_status == "implemented" and not trail:
        flash("Recording 'implemented' requires a short note of what was done.", "warning")
        return redirect(url_for("notepad.detail", note_id=note.id))

    note.status = new_status
    note.implemented_note = trail or None
    db.session.commit()
    flash(f"Note marked {new_status}.", "success")
    return redirect(url_for("notepad.detail", note_id=note.id))


@notepad_bp.route("/<int:note_id>/visibility", methods=["POST"])
def set_visibility(note_id: int):
    """Toggle shared/private. Author-only."""
    from flask_login import current_user

    note = Note.query.get_or_404(note_id)
    if note.author_id != current_user.id:
        abort(403)

    note.is_shared = request.form.get("is_shared") != "false"
    db.session.commit()
    flash("Note is now " + ("shared." if note.is_shared else "private."), "success")
    return redirect(url_for("notepad.detail", note_id=note.id))
