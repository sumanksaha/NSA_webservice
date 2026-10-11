"""Notepad module models.

Supports the Notes intake queue:

- ``Note`` — one pasted/PDF-extracted item: an idea, a to-do, a proposal.
  Shared with all FSOs by default; the author may make it private. Not a
  legal record — no hash-chained audit, no optimistic concurrency.
- ``NoteEvaluation`` — one AI-generated structured verdict on a Note.
  Append-only: re-evaluations add rows and never overwrite earlier ones.
- ``NoteAnswer`` — one grounded legal-RAG answer to one question in a
  Note's Q&A thread. Append-only; carries the exact question, the
  statement snapshot at ask time, citations, and verification signals.

See ``docs/NOTEPAD_LEGAL_RAG_PLAN.md`` / ``docs/NOTEPAD_LEGAL_RAG_SPEC.md``
and the **Note** entry in ``CONTEXT.md``.
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
    answers = db.relationship(
        "NoteAnswer",
        backref="note",
        cascade="all, delete-orphan",
        order_by="NoteAnswer.created_at",
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


class NoteAnswer(db.Model):
    """One append-only grounded RAG answer to one question in a Note's thread.

    Produced by ``app/notepad/thread.py`` via the legal RAG pipeline
    (``run_generation_pipeline``). ``question`` is the exact fragment that
    was asked; ``note_snapshot`` is the statement at ask time so history
    stays interpretable after the Note is edited (INV-3). ``degraded`` marks
    a circuit-open stub fallback (no retrieval happened).
    """

    __tablename__ = "note_answer"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    note_id = db.Column(
        db.Integer,
        db.ForeignKey("note.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    citations = db.Column(db.Text, nullable=True)  # JSON list of pipeline citation dicts
    note_snapshot = db.Column(db.Text, nullable=True)
    groundedness_score = db.Column(db.Float, nullable=True)
    hallucination_detected = db.Column(db.Boolean, nullable=True)
    degraded = db.Column(db.Boolean, nullable=False, default=False)
    provider_model = db.Column(db.String(200), nullable=True)
    token_usage = db.Column(db.Integer, nullable=True)
    rag_log_id = db.Column(db.String(64), nullable=True)  # rag_query_log.id join-back
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (db.Index("idx_note_answer_note_id", "note_id"),)

    def __repr__(self) -> str:
        return f"<NoteAnswer id={self.id} note={self.note_id} degraded={self.degraded}>"


class DailyPlan(db.Model):
    """One append-only AI-generated daily battle plan for a user's own notes.

    ``payload`` is JSON with exactly three fields: ``items`` (top 3-5:
    note_id / effort_bucket quick|medium|long / why), ``ranking`` (full
    ordered list of open own-notes), and ``portfolio_rationale`` (markdown
    prose from the three lenses applied to the sequence).
    """

    __tablename__ = "daily_plan"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    author_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payload = db.Column(db.Text, nullable=False)  # JSON blob: items/ranking/portfolio_rationale
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (db.Index("idx_daily_plan_author_id", "author_id"),)

    def __repr__(self) -> str:
        return f"<DailyPlan id={self.id} author={self.author_id}>"
