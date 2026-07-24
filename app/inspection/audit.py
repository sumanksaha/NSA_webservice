from datetime import datetime
from sqlalchemy import text
from app.extensions import db
from app.models import AuditLog
import json
import hashlib


def _get_db_dialect() -> str:
    """Return the current database dialect name."""
    engine = db.session.get_bind()
    return engine.dialect.name


def _acquire_audit_lock(entity_id: str) -> None:
    """
    Acquire a lock that serialises audit-log writes for the same
    ``entity_id`` across all processes.

    On PostgreSQL a ``pg_advisory_xact_lock`` is used so that concurrent
    transactions for the same entity are fully serialised.

    On SQLite the database-level write lock already serialises writers,
    but we additionally wrap the read-compute-insert in a single
    transaction to minimise the race window.
    """
    if _get_db_dialect() == 'postgresql':
        lock_key = hash(entity_id) & 0x7FFFFFFF
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_key},
        )


def compute_hash(prev_hash: str, entity_id: str, action: str, timestamp: str, details_json: str) -> str:
    """
    Returns sha256 hex digest of:
    (prev_hash or "") + entity_id + action + timestamp + details_json
    """
    input_str = (prev_hash or "") + entity_id + action + timestamp + details_json
    return hashlib.sha256(input_str.encode('utf-8')).hexdigest()


def log_audit(entity_type: str, entity_id: str, action: str, actor: str, details: dict) -> None:
    """
    Inserts a row into AuditLog table with hash chaining.

    The entire read-compute-insert sequence is wrapped in a transaction
    protected by a PostgreSQL advisory lock (when available) so that
    concurrent requests for the same ``entity_id`` cannot read the same
    ``prev_hash`` and produce divergent chains.

    Serializes ``details`` dict to JSON string for details_json column.
    Commits the insert.
    """
    # Serialize details to JSON
    details_json = json.dumps(details)

    # Get the current timestamp
    timestamp = datetime.utcnow()
    timestamp_str = timestamp.isoformat()

    try:
        # Acquire cross-process lock for this entity (PostgreSQL only).
        # On SQLite the write lock inside the transaction provides safety.
        _acquire_audit_lock(entity_id)

        # Query the most recent AuditLog row for the same entity_id
        # to get its curr_hash — done inside the locked transaction so
        # no other writer can interleave.
        prev_audit = AuditLog.query.filter_by(entity_id=entity_id).order_by(AuditLog.id.desc()).first()
        prev_hash = prev_audit.curr_hash if prev_audit else None

        # Compute curr_hash
        curr_hash = compute_hash(prev_hash, entity_id, action, timestamp_str, details_json)

        # Create the audit log entry
        audit_log = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor=actor,
            timestamp=timestamp,
            prev_hash=prev_hash,
            curr_hash=curr_hash,
            details_json=details_json
        )

        db.session.add(audit_log)
        db.session.commit()

    except Exception:
        db.session.rollback()
        raise


def verify_audit_chain(entity_id: str) -> bool:
    """
    Fetches all AuditLog rows for entity_id, ordered by id ASC.
    Recomputes each row's hash from its own fields and prev_hash from the previous row's curr_hash.
    Returns True if every row's stored curr_hash matches the recomputed hash, False on any mismatch.
    Returns True for empty chain (nothing to violate).
    """
    audit_logs = AuditLog.query.filter_by(entity_id=entity_id).order_by(AuditLog.id.asc()).all()

    if not audit_logs:
        return True

    for i, audit_log in enumerate(audit_logs):
        # For the first row, prev_hash should be None
        if i == 0:
            expected_prev_hash = None
        else:
            expected_prev_hash = audit_logs[i-1].curr_hash

        # Recompute the hash
        recomputed_hash = compute_hash(
            expected_prev_hash,
            audit_log.entity_id,
            audit_log.action,
            audit_log.timestamp.isoformat(),
            audit_log.details_json
        )

        # Check if the stored curr_hash matches the recomputed hash
        if audit_log.curr_hash != recomputed_hash:
            return False

    return True
