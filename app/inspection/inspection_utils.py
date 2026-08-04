"""inspection_utils.py

Utilities for the Inspection module, including inspection_code generation.
"""

import random
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.extensions import db
from app.models import CodeSequence


def _get_db_dialect() -> str:
    """Return the current database dialect name (e.g. 'postgresql', 'sqlite')."""
    engine = db.session.get_bind()
    return engine.dialect.name


def _acquire_advisory_lock(lock_key: int) -> None:
    """Acquire a PostgreSQL advisory transaction lock.

    On non-PostgreSQL databases this is a no-op (the retry loop in
    ``generate_inspection_code`` handles concurrency via the sequence table).
    """
    if _get_db_dialect() == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_key},
        )


def generate_inspection_code() -> str:
    """Generate an inspection code in the format INSP-YYYY-##### where #####
    is zero-padded sequence per year.

    Uses a dedicated ``code_sequence`` table with an atomic increment
    inside a transaction so that concurrent workers (Gunicorn/uWSGI) never
    obtain the same value.  On PostgreSQL an advisory lock provides
    additional cross-process serialisation.  A retry loop with random
    backoff handles any residual contention.

    Returns:
        str: Generated inspection code (e.g., 'INSP-2026-00001')

    """
    year = datetime.now(UTC).year
    seq_key = f"inspection:{year}"
    # Stable hash for advisory lock (PostgreSQL only)
    lock_key = hash(seq_key) & 0x7FFFFFFF

    max_retries = 10
    for attempt in range(max_retries):
        try:
            # Acquire advisory lock (PostgreSQL only — no-op on SQLite)
            _acquire_advisory_lock(lock_key)

            # Atomically get-or-create the sequence row and increment it.
            seq = db.session.get(CodeSequence, seq_key)
            if seq is None:
                seq = CodeSequence(key=seq_key, last_value=0)
                db.session.add(seq)
                db.session.flush()

            next_value = seq.last_value + 1
            seq.last_value = next_value

            db.session.commit()

            inspection_code = f"INSP-{year}-{next_value:05d}"
            return inspection_code

        except Exception:
            db.session.rollback()
            time.sleep(random.uniform(0.001, 0.01) * (attempt + 1))
            continue

    raise RuntimeError(f"Failed to generate unique inspection code after {max_retries} retries")


def calculate_compliance_deadline(inspection_date) -> datetime | None:
    """Calculate compliance deadline as inspection_date + 30 days.

    Args:
        inspection_date: A datetime object, a date object, or an ISO
            date string (YYYY-MM-DD).

    Returns:
        datetime: The deadline as a datetime object.

    """
    if isinstance(inspection_date, datetime):
        base = inspection_date
    elif hasattr(inspection_date, "year"):  # date-like object
        base = datetime.combine(inspection_date, datetime.min.time())
    else:
        # Try parsing as ISO string
        try:
            base = datetime.strptime(str(inspection_date), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    deadline = base + timedelta(days=30)
    return deadline
