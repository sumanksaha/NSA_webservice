"""Billing and sample-related models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from app.extensions import db


class Bill(db.Model):
    __tablename__ = "bills"

    id = db.Column(db.Integer, primary_key=True)
    version_id = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {
        "version_id_col": version_id,
    }
    Name = db.Column(db.String(100), nullable=False)
    EMP_ID = db.Column(db.String(50), nullable=False)
    Designation = db.Column(db.String(100), nullable=False, default="Food Safety Officer")
    Enf_samp_No = db.Column(db.Integer, nullable=False, default=0)
    Surv_samp_No = db.Column(db.Integer, nullable=False, default=0)
    enforcement_price = db.Column(db.Numeric(precision=10, scale=2), nullable=False, default=0.00)
    surveillance_price = db.Column(db.Numeric(precision=10, scale=2), nullable=False, default=0.00)
    Total_bill = db.Column(db.Float, nullable=False, default=0.0)
    No_of_enfbills = db.Column(db.Integer, nullable=False, default=0)
    No_of_survbills = db.Column(db.Integer, nullable=False, default=0)
    TR_Value = db.Column(db.String(100), nullable=False)
    TR_date = db.Column(db.DateTime, nullable=False)
    Submission_date = db.Column(db.DateTime, nullable=False)
    start_date = db.Column(db.DateTime)
    end_date = db.Column(db.DateTime)

    # Audit & Sync fields
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    synced_at = db.Column(db.DateTime, nullable=True)

    # PDF generation tracking (populated by Celery task)
    pdf_task_id = db.Column(db.String(100), nullable=True)
    pdf_generated_at = db.Column(db.DateTime, nullable=True)

    # Relationship to samples — ``selectin`` eager loading so that any
    # ``bill.samples`` / ``sample.bills`` access inside a loop issues a single
    # additional query instead of one per row (Perf Quick Win #5, N+1 fix).
    samples = db.relationship(
        "Sample",
        secondary="bill_sample",
        backref=db.backref("bills", lazy="selectin"),
        lazy="selectin",
    )


class Sample(db.Model):
    __tablename__ = "sample"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    version_id = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {
        "version_id_col": version_id,
    }
    sample_code = db.Column(db.String(50), nullable=False, unique=True)
    sample_name = db.Column(db.String(200), nullable=False)
    sample_type = db.Column(db.String(100), nullable=False)
    fso_name = db.Column(db.String(100), db.ForeignKey("fso.fso_name"), nullable=False)
    collection_date = db.Column(db.DateTime, nullable=False)
    submission_date = db.Column(db.DateTime, nullable=True)
    retailer_fssai = db.Column(db.String(50), nullable=True)
    retailer_name = db.Column(db.String(200), nullable=True)
    price = db.Column(db.String(50), nullable=True)
    # --- OCR autopopulation fields (Phase A) ---
    nature_of_food = db.Column(db.String(200), nullable=True)
    batch_no = db.Column(db.String(100), nullable=True)
    mfd = db.Column(db.String(50), nullable=True)  # manufacturing date (free-form, OCR may vary)
    exp = db.Column(db.String(50), nullable=True)  # expiry date (free-form)
    manufacturer_details = db.Column(db.Text, nullable=True)
    billed = db.Column(db.Boolean, default=False)
    food_cell_forwarded = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    synced_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index("idx_sample_code", "sample_code"),
        db.Index("idx_sample_collection_date", "collection_date"),
        db.Index("idx_sample_fso_name", "fso_name"),
        db.Index("idx_sample_billed", "billed"),
    )


class BillSample(db.Model):
    __tablename__ = "bill_sample"

    bill_id = db.Column(db.Integer, db.ForeignKey("bills.id"), primary_key=True)
    sample_id = db.Column(db.Integer, db.ForeignKey("sample.id"), primary_key=True)


class CodeSequence(db.Model):
    """Dedicated sequence table for race-safe code generation across multiple
    processes (Gunicorn/uWSGI workers). Each row holds a monotonically
    increasing counter keyed by a string (e.g. 'sample:2026').

    The counter is incremented atomically inside a transaction so that
    concurrent workers never obtain the same value.  On PostgreSQL an
    advisory lock provides additional cross-process serialisation; on
    SQLite the database-level write lock plus a retry loop handles it.
    """

    __tablename__ = "code_sequence"

    key = db.Column(db.String(50), primary_key=True)
    last_value = db.Column(db.Integer, nullable=False, default=0)
