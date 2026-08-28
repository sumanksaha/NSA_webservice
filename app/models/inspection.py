"""Inspection and FSO related models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from app.extensions import db


class FSO(db.Model):
    __tablename__ = "fso"

    fso_name = db.Column(db.String(100), primary_key=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    # Per-FSO email / SMTP configuration (nullable — email not yet configured)
    email = db.Column(db.String(200), nullable=True)  # sender email address
    smtp_host = db.Column(db.String(200), nullable=True)
    smtp_port = db.Column(db.Integer, nullable=True, default=587)
    smtp_user = db.Column(db.String(200), nullable=True)  # login username (defaults to email)
    smtp_password = db.Column(db.String(500), nullable=True)  # stored encrypted in production
    smtp_use_tls = db.Column(db.Boolean, nullable=True, default=True)

    __table_args__ = (db.Index("idx_fso_name", "fso_name"),)


class Inspection(db.Model):
    __tablename__ = "inspection"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    version_id = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__: ClassVar[dict] = {
        "version_id_col": version_id,
    }
    inspection_code = db.Column(db.String(50), nullable=False, unique=True)
    fso_name = db.Column(db.String(100), db.ForeignKey("fso.fso_name"), nullable=False)
    fssai_license = db.Column(db.String(50), nullable=True)
    ce_license_no = db.Column(db.String(100), nullable=True)
    fbo_name = db.Column(db.String(200), nullable=True)
    fbo_address = db.Column(db.Text, nullable=True)
    concerned_food = db.Column(db.String(200), nullable=True)
    problem = db.Column(db.Text, nullable=True)
    # Explicit diary purpose picked by the FSO at entry time:
    # "routine" | "complaint" | NULL (legacy rows fall back to the
    # problem-presence heuristic in WorkDiaryEngine.derive_purpose).
    visit_purpose = db.Column(db.String(20), nullable=True)
    checklist_json = db.Column(db.Text, nullable=True)  # JSON: {field_name: "yes"/"no"} for the 12-item checklist
    notice_issued_at = db.Column(db.DateTime, nullable=True)  # first Improvement Notice render freezes the record
    inspection_date = db.Column(db.DateTime, nullable=False)
    compliance_deadline = db.Column(db.DateTime, nullable=False)
    is_dismissed = db.Column(db.Boolean, default=False)
    dismissed_by = db.Column(db.String(100), nullable=True)
    dismissed_at = db.Column(db.DateTime, nullable=True)
    adjudication_id = db.Column(db.Integer, db.ForeignKey("adjudications.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
    synced_at = db.Column(db.DateTime, nullable=True)
    # New fields for sample collection
    sample_collected = db.Column(db.Boolean, nullable=True)
    sample_code = db.Column(db.String(100), nullable=True)

    __table_args__ = (
        db.Index("idx_inspection_code", "inspection_code"),
        db.Index("idx_inspection_date", "inspection_date"),
        db.Index("idx_inspection_compliance_deadline", "compliance_deadline"),
        db.Index("idx_inspection_fso_name", "fso_name"),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    entity_type = db.Column(db.String, nullable=False)
    entity_id = db.Column(db.String, nullable=False)
    action = db.Column(db.String, nullable=False)
    actor = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    prev_hash = db.Column(db.String, nullable=True)
    curr_hash = db.Column(db.String, nullable=True)
    details_json = db.Column(db.Text, nullable=True)
