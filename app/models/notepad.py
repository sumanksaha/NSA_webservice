"""Notepad module models.

Supports the Notes intake queue:

- ``Note`` — one pasted/PDF-extracted item: an idea, a to-do, a proposal.
  Shared with all FSOs by default; the author may make it private. Not a
  legal record — no hash-chained audit, no optimistic concurrency.
- ``NoteEvaluation`` — one AI-generated structured verdict on a Note.
  Append-only: re-evaluations add rows and never overwrite earlier ones.

See ``docs/NOTEPAD_IMPLEMENTATION_PLAN.md`` and the **Note** entry in
``CONTEXT.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db


class Note(db.Model):
    """One item in the Notepad intake queue."""

    __tablename__ = "note"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    author_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_text = db.Column(db.Text, nullable=False)
    source_type = db.Column(db.String(16), nullable=False, default="pasted")  # pasted | pdf
    is_shared = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(32), nullable=False, default="new")  # new | evaluated | implemented | dismissed
    implemented_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    author = db.relationship("User", backref=db.backref("notes", lazy="selectin"))
    evaluations = db.relationship(
        "NoteEvaluation",
        backref="note",
        cascade="all, delete-orphan",
        order_by="NoteEvaluation.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        db.Index("idx_note_author_id", "author_id"),
        db.Index("idx_note_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Note id={self.id} author={self.author_id} status={self.status}>"


class NoteEvaluation(db.Model):
    """One append-only AI evaluation of a :class:`Note`.

    ``payload`` is JSON with exactly seven fields: ``summary``,
    ``implementation_plan``, ``risks``, ``game_theory``, ``talebian``,
    ``first_principles``, ``feasibility_score``.
    """

    __tablename__ = "note_evaluation"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    note_id = db.Column(
        db.Integer,
        db.ForeignKey("note.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payload = db.Column(db.Text, nullable=False)  # JSON blob of the seven lens fields
    provider_model = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (db.Index("idx_note_evaluation_note_id", "note_id"),)

    def __repr__(self) -> str:
        return f"<NoteEvaluation id={self.id} note={self.note_id}>"
