from datetime import datetime
from app.extensions import db
from app.models import AuditLog
import json
import hashlib


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
    Serializes `details` dict to JSON string for details_json column.
    Commits the insert.
    """
    # Serialize details to JSON
    details_json = json.dumps(details)
    
    # Get the current timestamp as a string
    timestamp = datetime.utcnow()
    timestamp_str = timestamp.isoformat()
    
    # Query the most recent AuditLog row for the same entity_id to get its curr_hash
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