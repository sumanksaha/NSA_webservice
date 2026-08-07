"""Authentication and user models."""
from __future__ import annotations

from datetime import UTC, datetime

from flask_login import UserMixin

from app.extensions import db


class User(db.Model, UserMixin):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))

    def get_id(self):
        return str(self.id)

    def __repr__(self):
        return f"<User {self.username}>"


class RecordAudit(db.Model):
    """Record-changes and login-event audit log.

    This is a separate table from the hash-chained `AuditLog` used by
    the photo-evidence system.  This one tracks:
      - INSERT / UPDATE / DELETE on Adjudication, Bill, CaseFile
      - login_success / login_failed events
    """

    __tablename__ = "record_audit"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(20), nullable=False)  # create|update|delete|login_success|login_failed
    record_type = db.Column(db.String(50), nullable=False, index=True)
    record_id = db.Column(db.String(50), nullable=False, index=True)
    changes = db.Column(db.Text, nullable=True)  # JSON string: {"field": {"old": ..., "new": ...}}
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)

    # Relationship
    user = db.relationship("User", backref="audit_logs", lazy="joined")

    def __repr__(self):
        return f"<RecordAudit {self.action} {self.record_type}#{self.record_id}>"


class Role(db.Model):
    """RBAC role (Phase 18 multi-user access control).

    Canonical names: ``admin``, ``inspector``, ``adjudicator``, ``viewer``.
    Assigned to users via the ``user_roles`` association table.
    """

    __tablename__ = "role"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(64), unique=True, nullable=False)  # admin | inspector | adjudicator | viewer
    description = db.Column(db.String(256), nullable=True)

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


user_roles = db.Table(
    "user_roles",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    db.Column("role_id", db.Integer, db.ForeignKey("role.id", ondelete="CASCADE"), primary_key=True),
    db.Index("idx_user_roles_user_id", "user_id"),
    db.Index("idx_user_roles_role_id", "role_id"),
)


class Comment(db.Model):
    """Document comment tied to a case and (optionally) an anchored section (Phase 18)."""

    __tablename__ = "comment"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    case_id = db.Column(db.Integer, nullable=False)
    case_type = db.Column(db.String(32), nullable=False, default="case_file")  # case_file | adjudication
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    section_id = db.Column(db.String(128), nullable=True)  # Anchored document section

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("ix_comment_case_id", "case_id"),
        db.Index("ix_comment_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<Comment #{self.id} case={self.case_id}>"


class AirtableBaseMap(db.Model):
    """Tracks which local DB record maps to which Airtable record + base.

    Part of the Multi-Target Sheets Redundancy (Priority 7) architecture.
    Enables restore chaining: when PostgreSQL fails, the system can recover
    from R2 CSV exports of each sync target (Sheets / Airtable / Excel).

    The ``module`` field identifies which business domain the record belongs
    to (e.g. 'food_cell_do_intimations').  ``airtable_base_id`` tracks which
    Airtable base the record lives in — when the primary base nears capacity,
    the sync service creates a new base and routes subsequent records there.
    """

    __tablename__ = "airtable_base_map"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_id = db.Column(db.Integer, nullable=False, index=True)
    module = db.Column(db.String(64), nullable=False, index=True)
    airtable_record_id = db.Column(db.String(256), nullable=True)
    airtable_base_id = db.Column(db.String(256), nullable=True)
    airtable_table_name = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        db.Index("idx_airtable_map_record_id", "record_id"),
        db.Index("idx_airtable_map_module", "module"),
        db.Index("idx_airtable_map_base_id", "airtable_base_id"),
    )

    def __repr__(self) -> str:
        return f"<AirtableBaseMap record={self.record_id} module={self.module}>"


# Convenience relationship so ``user.roles`` is queryable once the table exists.
# Deliberately NOT lazy="joined": the RBAC tables (role/user_roles) can lag
# behind in some deployments (see migrations/versions/fix_rbac_tables.py), and
# eager-joining them would make EVERY User query — login, user_loader on each
# request — crash with `relation "user_roles" does not exist`. Lazy loading
# keeps auth resilient and avoids a join on every request; Phase 18 RBAC is not
# wired up yet, so nothing accesses user.roles today.
User.roles = db.relationship(  # type: ignore[attr-defined]
    "Role",
    secondary="user_roles",
    backref="users",
    lazy="select",
)
