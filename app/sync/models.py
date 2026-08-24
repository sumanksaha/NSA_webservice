"""Phase 17: Supabase sync models.

Tracks per-record sync state, pending conflicts, and an audit trail of
sync operations.  These tables are the ONLY sync-specific tables — the
business models (CaseFile, Adjudication, Bill, Sample, Inspection) are
left untouched, avoiding schema migrations on existing production tables.

``SyncState.sync_version`` is an optimistic-concurrency counter: it
increments on each successful push or pull, and is compared against the
Supabase-side ``sync_version`` to detect conflicts.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime

from sqlalchemy import Index, UniqueConstraint

from app.extensions import db


class SyncState(db.Model):
    """Per-record sync state in the bridge table.

    One row per synced record, keyed by ``(table_name, local_id)``.
    """

    __tablename__ = "sync_state"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    table_name = db.Column(db.String(100), nullable=False)
    local_id = db.Column(db.Integer, nullable=False)
    sync_version = db.Column(db.Integer, default=0, nullable=False)
    synced_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint("table_name", "local_id", name="uq_sync_state_table_local"),
        Index("idx_sync_state_synced", "synced_at"),
        Index("idx_sync_state_table_local", "table_name", "local_id"),
    )

    def __repr__(self) -> str:
        return f"<SyncState {self.table_name}#{self.local_id} v{self.sync_version}>"


class SyncConflict(db.Model):
    """A pending push / pull conflict awaiting user resolution.

    When the local ``sync_version`` and the remote Supabase ``sync_version``
    diverge, the row is recorded here so the UI can present a resolve dialog.
    """

    __tablename__ = "sync_conflicts"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    table_name = db.Column(db.String(100), nullable=False)
    local_id = db.Column(db.Integer, nullable=False)

    local_version = db.Column(db.Integer, nullable=False)
    remote_version = db.Column(db.Integer, nullable=False)

    direction = db.Column(db.String(10), nullable=False)  # "push" | "pull"
    remote_snapshot = db.Column(db.Text, nullable=True)  # JSON blob of the remote row

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("idx_sync_conflict_table_local", "table_name", "local_id"),
        Index("idx_sync_conflict_direction", "direction"),
        Index("idx_sync_conflict_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SyncConflict {self.table_name}#{self.local_id} {self.direction}>"


class SyncLog(db.Model):
    """Audit trail of sync push / pull / resolve operations."""

    __tablename__ = "sync_log"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    operation = db.Column(db.String(10), nullable=False)  # "push" | "pull" | "resolve"
    status = db.Column(db.String(20), nullable=False)  # ok | partial | error | disabled
    pushed = db.Column(db.Integer, default=0)
    pulled = db.Column(db.Integer, default=0)
    conflicts = db.Column(db.Integer, default=0)
    errors_json = db.Column(db.Text, nullable=True)  # JSON-encoded list of error strings

    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("idx_sync_log_op", "operation"),
        Index("idx_sync_log_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SyncLog {self.operation} {self.status}>"

    @property
    def errors(self) -> list[str]:
        if self.errors_json:
            try:
                return _json.loads(self.errors_json)
            except (ValueError, TypeError):
                return []
        return []
