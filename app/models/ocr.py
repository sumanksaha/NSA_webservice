"""OCR pipeline models.

Supports the OCR extraction -> review -> conflict resolution -> autopopulation
pipeline (plan.md Phase A, task.md Phase A):

- ``OCRDocument``       raw extracted JSON, status, content hash
- ``LabTestParameter``   standard vs observed values
- ``OCRCorrection``      field corrections log (manual review edits)
- ``FieldAuthority``     source authority weights for conflict resolution
- ``ConflictLog``        conflicting field values surfaced for review

These tables are written by the Celery task ``process_ocr_document_async``
(task.md Phase A, Step 1) and consumed by the conflict-resolution UI
(task.md Phase B).

Index layout mirrors ``migrations/versions/add_ocr_pipeline_models.py``
exactly (explicit ``idx_*``/``ix_*`` names in ``__table_args__``) so that
``flask db migrate`` reports zero drift for these tables.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.extensions import db


class OCRDocument(db.Model):
    """Raw OCR extraction result for a single PDF (sample report or photo).

    One row per processed document.  ``extracted_json`` holds the full zone/field
    extraction payload produced by the Vision-LLM + zonal OCR engine.
    """

    __tablename__ = "ocr_document"

    id = db.Column(db.String(36), primary_key=True)
    sample_id = db.Column(db.Integer, db.ForeignKey("sample.id", ondelete="SET NULL"), nullable=True)

    file_name = db.Column(db.String(255), nullable=False)
    file_hash = db.Column(db.String(64), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)

    # Extraction payload + status
    extracted_json = db.Column(db.Text, nullable=False)  # JSON blob
    status = db.Column(
        db.String(32),
        nullable=False,
        default="completed",
    )  # pending | completed | failed
    page_count = db.Column(db.Integer, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("ix_ocr_document_file_hash", "file_hash"),
        db.Index("ix_ocr_document_sample_id", "sample_id"),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.id:
            self.id = str(uuid.uuid4())

    def __repr__(self) -> str:
        return f"<OCRDocument {self.file_name[:24]} ({self.status})>"


class LabTestParameter(db.Model):
    """A standard test parameter with its observed value extracted from OCR.

    One row per extracted parameter per ``OCRDocument``.  The ``source_authority``
    field records which source (Vision-LLM, zonal OCR, manual correction)
    produced the value, so the conflict resolver can weigh competing values.
    """

    __tablename__ = "lab_test_parameter"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ocr_document_id = db.Column(db.String(36), db.ForeignKey("ocr_document.id", ondelete="CASCADE"), nullable=False)
    sample_id = db.Column(db.Integer, db.ForeignKey("sample.id", ondelete="SET NULL"), nullable=True)

    parameter_name = db.Column(db.String(128), nullable=False)
    standard_value = db.Column(db.String(256), nullable=True)
    observed_value = db.Column(db.String(256), nullable=True)
    unit = db.Column(db.String(32), nullable=True)

    # Source authority weight at the time of extraction
    source_authority = db.Column(db.String(32), nullable=True)
    confidence = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_labtest_ocr_doc", "ocr_document_id"),
        db.Index("ix_lab_test_parameter_sample_id", "sample_id"),
    )

    def __repr__(self) -> str:
        return f"<LabTestParameter {self.parameter_name}={self.observed_value}>"


class FieldAuthority(db.Model):
    """Authority weight table for conflict resolution.

    Higher ``weight`` means the source is more trusted.  Used by the conflict
    resolver to rank competing values for the same field (Phase B/C).
    """

    __tablename__ = "field_authority"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    source = db.Column(db.String(32), nullable=False, unique=True)  # vision_llm | zonal_ocr | manual
    weight = db.Column(db.Float, nullable=False, default=1.0)

    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"<FieldAuthority {self.source}={self.weight}>"


class OCRCorrection(db.Model):
    """Log of manual corrections applied during the review workflow (Phase B).

    Each row records a single field correction so the autopopulation engine
    (Phase C) and feedback dashboard (Phase D) can track per-field accuracy.
    """

    __tablename__ = "ocr_correction"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ocr_document_id = db.Column(db.String(36), db.ForeignKey("ocr_document.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)

    field_name = db.Column(db.String(128), nullable=False)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_correction_ocr_doc", "ocr_document_id"),
        db.Index("ix_ocr_correction_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<OCRCorrection {self.field_name}>"


class ConflictLog(db.Model):
    """Conflicting field values surfaced to the conflict-resolution queue (Phase B).

    When two or more sources provide different values for the same field, a
    row is inserted here so reviewers can pick the authoritative value.
    """

    __tablename__ = "conflict_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ocr_document_id = db.Column(db.String(36), db.ForeignKey("ocr_document.id", ondelete="CASCADE"), nullable=False)
    sample_id = db.Column(db.Integer, db.ForeignKey("sample.id", ondelete="SET NULL"), nullable=True)

    field_name = db.Column(db.String(128), nullable=False)
    # JSON list of competing values, each annotated with its source + weight
    values_json = db.Column(db.Text, nullable=False)

    resolved = db.Column(db.Boolean, nullable=False, default=False)
    resolved_value = db.Column(db.Text, nullable=True)
    resolved_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_conflict_ocr_doc", "ocr_document_id"),
        db.Index("ix_conflict_log_sample_id", "sample_id"),
    )

    def __repr__(self) -> str:
        return f"<ConflictLog {self.field_name} resolved={self.resolved}>"
