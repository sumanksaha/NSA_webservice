"""SQLAlchemy event listeners that log INSERT / UPDATE / DELETE on the
three business-record models to the ``RecordAudit`` table.

Models instrumented:
  - Adjudication
  - Bill
  - CaseFile

Design notes (performance):
  - Uses session-level ``after_flush`` (not mapper events) so that adding
    ``RecordAudit`` entries to the session is safe — the flush is already
    complete at that point.
  - For updates, only changed columns are captured via
    ``inspect(obj).attrs`` history — no full-record dump, no N+1 queries.
  - The current user ID is read from ``db.session.info["audit_user_id"]``
    which is set by a ``before_request`` handler in the app factory.
    In Celery / non-request contexts, this defaults to ``None``.
  - The audit entries are committed automatically with the session's next
    commit — no explicit commit inside the hook.
"""

import json
from datetime import datetime

from flask import request
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.event import listen
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import RecordAudit

# ---------------------------------------------------------------------------
# Sensitive / internal columns to exclude from change diffs
# ---------------------------------------------------------------------------
_EXCLUDED_COLUMNS = frozenset({
    "synced_at",  # set automatically by sync, not user-driven
    "pdf_task_id",  # Celery task tracking, not a meaningful record change
    "pdf_generated_at",  # same as above
})


def _get_user_id():
    """Return the current user's ID from ``session.info``, or ``None``."""
    try:
        return db.session.info.get("audit_user_id")
    except (RuntimeError, AttributeError):
        return None


def _get_ip():
    """Extract client IP from request, respecting proxy headers."""
    try:
        return request.remote_addr or None
    except RuntimeError:
        return None


def _get_user_agent():
    """Extract User-Agent string, truncated to 500 characters."""
    try:
        ua = request.headers.get("User-Agent", "")
        return ua[:500] if ua else None
    except RuntimeError:
        return None


def _record_audit(action, record_type, record_id, changes=None):
    """Insert a single ``RecordAudit`` row in the current session."""
    entry = RecordAudit(
        user_id=_get_user_id(),
        action=action,
        record_type=record_type,
        record_id=str(record_id),
        changes=json.dumps(changes) if changes else None,
        timestamp=datetime.utcnow(),
        ip_address=_get_ip(),
        user_agent=_get_user_agent(),
    )
    db.session.add(entry)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _changed_column_names(target):
    """Yield column names that are not in the exclusion set."""
    for col in target.__table__.columns:
        name = col.name
        if name == "id":
            continue
        if name in _EXCLUDED_COLUMNS:
            continue
        yield name


def _safe_value(val):
    """Convert a value to a JSON-safe representation."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


# ---------------------------------------------------------------------------
# Change-capture helpers  (called per target inside after_flush)
# ---------------------------------------------------------------------------


def _capture_insert(target):
    """Build a changes dict for a newly inserted record."""
    changes = {}
    for attr_name in _changed_column_names(target):
        val = _safe_value(getattr(target, attr_name))
        # Only include non-null fields for creates
        if val is not None:
            changes[attr_name] = {"new": val}
    return changes


def _capture_update(target):
    """Build a changes dict with only the changed columns (old → new)."""
    insp = sa_inspect(target)
    changes = {}
    for attr_name in _changed_column_names(target):
        history = getattr(insp.attrs, attr_name).history
        if history.has_changes():
            old = history.deleted[0] if history.deleted else history.unchanged[0] if history.unchanged else None
            new = history.added[0] if history.added else None
            if old is None and new is None:
                continue
            changes[attr_name] = {
                "old": _safe_value(old),
                "new": _safe_value(new),
            }
    return changes


# ---------------------------------------------------------------------------
# Session-level after_flush handler
# ---------------------------------------------------------------------------


def _after_flush(session: Session, _flush_context):
    """Inspect flushed objects and emit audit entries for audited models.

    This fires *after* the flush is complete, so that ``session.add()``
    for the audit entry is safe (no recursive flush).
    """
    # Quick check: skip if no objects were flushed at all
    if not (session.new or session.dirty or session.deleted):
        return

    from app.models import Adjudication, Bill, CaseFile

    audited_model_types = (Adjudication, Bill, CaseFile)

    # Inserts
    for target in session.new:
        if isinstance(target, audited_model_types):
            changes = _capture_insert(target)
            _record_audit("create", type(target).__name__, target.id, changes)

    # Updates
    for target in session.dirty:
        if isinstance(target, audited_model_types):
            changes = _capture_update(target)
            if changes:  # only log if something actually changed
                _record_audit("update", type(target).__name__, target.id, changes)

    # Deletes
    for target in session.deleted:
        if isinstance(target, audited_model_types):
            _record_audit("delete", type(target).__name__, target.id)


# ---------------------------------------------------------------------------
# Public registration API
# ---------------------------------------------------------------------------

_registered = False


def register_audit_hooks():
    """Wire the ``after_flush`` event listener on ``db.session``.

    Must be called after ``db.init_app(app)`` and within an app context.
    Idempotent — safe to call multiple times.
    """
    global _registered
    if _registered:
        return
    listen(Session, "after_flush", _after_flush)
    _registered = True
