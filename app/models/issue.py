"""FBO issue tracking models."""
from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db


class FboIssue(db.Model):
    __tablename__ = "fbo_issue"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    fbo_id = db.Column(db.String, nullable=False)
    manufacturer_fbo_id = db.Column(db.String, nullable=True)
    fbo_name = db.Column(db.String, nullable=False)
    source_type = db.Column(db.String, nullable=False)
    state = db.Column(db.String, nullable=False, default="open")
    fso_name = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    detail_json = db.Column(db.Text, nullable=True)
    reg_lat = db.Column(db.Float, nullable=True)
    reg_lng = db.Column(db.Float, nullable=True)
    geocoded_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint("source_type IN ('inspection','sample')", name="ck_source_type"),
        db.CheckConstraint(
            "state IN ('open','permission_pending','permission_granted','closed','dismissed')",
            name="ck_state",
        ),
        db.CheckConstraint("NOT (source_type = 'sample' AND state = 'dismissed')", name="ck_sample_not_dismissed"),
        db.CheckConstraint("source_type = 'sample' OR manufacturer_fbo_id IS NULL", name="ck_sample_or_null_mfg"),
        db.Index("idx_fbo_issue_fbo_id", "fbo_id"),
        db.Index("idx_fbo_issue_state", "state"),
    )


class FboIssueAudit(db.Model):
    __tablename__ = "fbo_issue_audit"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    issue_id = db.Column(db.Integer, db.ForeignKey("fbo_issue.id"), nullable=False)
    from_state = db.Column(db.String, nullable=True)
    to_state = db.Column(db.String, nullable=False)
    asserted_by = db.Column(db.String, nullable=False)
    asserted_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    note = db.Column(db.Text, nullable=True)

    __table_args__ = (db.Index("idx_fbo_issue_audit_issue_id", "issue_id"),)
