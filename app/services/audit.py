"""Shared audit-log service with hash-chaining.

Extracted from ``app/inspection/audit.py`` so that multiple blueprints
(inspection, adjudication) can import ``log_audit`` without creating a
blueprint-to-blueprint coupling.
"""

import hashlib
import json
from datetime import UTC, datetime

from sqlalchemy import text

from app.extensions import db
from app.models import AuditLog


def _get_db_dialect() -> str:
    """Return the current database dialect name."""
    engine = db.session.get_bind()
    return engine.dialect.name


def _acquire_audit_lock(entity_id: str) -> None:
    """Acquire a lock that serialises audit-log writes for the same
    ``entity_id`` across all processes.

    On PostgreSQL a ``pg_advisory_xact_lock`` is used so that concurrent
    transactions for the same entity are fully serialised.

    On SQLite the database-level write lock already serialises writers,
    but we additionally wrap the read-compute-insert in a single
    transaction to minimise the race window.
    """
    if _get_db_dialect() == "postgresql":
        lock_key = hash(entity_id) & 0x7FFFFFFF
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_key},
        )


def compute_hash(prev_hash: str | None, entity_id: str, action: str, timestamp: str, details_json: str) -> str:
    """Returns sha256 hex digest of:
    (prev_hash or "") + entity_id + action + timestamp + details_json
    """
    input_str = (prev_hash or "") + entity_id + action + timestamp + details_json
    return hashlib.sha256(input_str.encode("utf-8")).hexdigest()


def verify_audit_chain(entity_id: str) -> bool:
    """Verify the hash chain integrity for all ``AuditLog`` rows with
    ``entity_id``.  Re-computes each row's ``curr_hash`` from its fields
    and the previous row's hash.

    Returns ``True`` if every hash matches, ``False`` on any mismatch.
    Returns ``True`` for an empty chain.
    """
    audit_logs = AuditLog.query.filter_by(entity_id=entity_id).order_by(AuditLog.id.asc()).all()

    if not audit_logs:
        return True

    for i, log in enumerate(audit_logs):
        expected_prev = audit_logs[i - 1].curr_hash if i > 0 else None
        # Normalize timestamp: SQLite (DateTime column) strips tzinfo on
        # round-trip, so we may need to re-attach UTC to match the
        # ``timestamp.isoformat()`` used during ``log_audit``.
        ts = log.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        recomputed = compute_hash(
            expected_prev,
            log.entity_id,
            log.action,
            ts.isoformat(),
            log.details_json,
        )
        if log.curr_hash != recomputed:
            return False

    return True


def log_audit(entity_type: str, entity_id: str, action: str, actor: str, details: dict) -> None:
    """Insert a row into the ``AuditLog`` table with hash chaining.

    The entire read-compute-insert sequence is wrapped in a transaction
    protected by a PostgreSQL advisory lock (when available) so that
    concurrent requests for the same ``entity_id`` cannot read the same
    ``prev_hash`` and produce divergent chains.

    Serializes ``details`` dict to JSON for the ``details_json`` column.
    """
    details_json = json.dumps(details)
    timestamp = datetime.now(UTC)
    timestamp_str = timestamp.isoformat()

    try:
        _acquire_audit_lock(entity_id)

        prev = AuditLog.query.filter_by(entity_id=entity_id).order_by(AuditLog.id.desc()).first()
        prev_hash = prev.curr_hash if prev else None

        curr_hash = compute_hash(prev_hash, entity_id, action, timestamp_str, details_json)

        entry = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            timestamp=timestamp,
            prev_hash=prev_hash,
            curr_hash=curr_hash,
            details_json=details_json,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
