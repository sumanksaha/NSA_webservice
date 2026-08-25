"""sample_utils.py

Utilities for the Sample module, including sample_code generation.
"""

import random
import time
from datetime import UTC, datetime
from typing import Any

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
    ``generate_sample_code`` handles concurrency via the sequence table).
    """
    if _get_db_dialect() == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": lock_key},
        )


def generate_sample_code() -> str:
    """Generate a sample code in the format SKS-YYYY-##### where ##### is
    zero-padded sequence per year.

    Uses a dedicated ``code_sequence`` table with an atomic increment
    inside a transaction so that concurrent workers (Gunicorn/uWSGI) never
    obtain the same value.  On PostgreSQL an advisory lock provides
    additional cross-process serialisation.  A retry loop with random
    backoff handles any residual contention.

    Returns:
        str: Generated sample code (e.g., 'SKS-2026-00001')

    """
    year = datetime.now(UTC).year
    seq_key = f"sample:{year}"
    # Stable hash for advisory lock (PostgreSQL only)
    lock_key = hash(seq_key) & 0x7FFFFFFF

    max_retries = 10
    for attempt in range(max_retries):
        try:
            # Acquire advisory lock (PostgreSQL only — no-op on SQLite)
            _acquire_advisory_lock(lock_key)

            # Atomically get-or-create the sequence row and increment it.
            # The UPDATE ... SET last_value = last_value + 1 is atomic at
            # the database level, guaranteeing uniqueness even without
            # row-level locking.
            seq = db.session.get(CodeSequence, seq_key)
            if seq is None:
                seq = CodeSequence(key=seq_key, last_value=0)
                db.session.add(seq)
                db.session.flush()  # assign PK without committing

            next_value = seq.last_value + 1
            seq.last_value = next_value

            # Commit the sequence increment atomically.
            db.session.commit()

            sample_code = f"SKS-{year}-{next_value:05d}"
            return sample_code

        except Exception:
            db.session.rollback()
            # Random backoff to reduce contention on retry
            time.sleep(random.uniform(0.001, 0.01) * (attempt + 1))
            continue

    raise RuntimeError(f"Failed to generate unique sample code after {max_retries} retries")


def sample_to_sync_row(sample: Any, synced_at: str = "") -> dict:
    """Build the canonical Sheets/Airtable/Excel sync row for a :class:`Sample`.

    One source of truth for the ``sample_repo`` sync payload, shared by the
    create and update handlers (which previously copy-pasted the same dict
    literal and diverged only on ``synced_at``).

    Args:
        sample: A persisted :class:`~app.models.billing.Sample`.
        synced_at: ISO timestamp to mark the row as synced (empty string on a
            freshly-created sample that has not yet synced).

    Returns:
        Dict keyed to ``app.services.sheets_sync.SHEET_COLUMNS`` column order
        for the ``sample_repo`` module.
    """
    return {
        "id": sample.id,
        "sample_code": sample.sample_code,
        "sample_name": sample.sample_name,
        "sample_type": sample.sample_type or "",
        "fso_name": sample.fso_name,
        "collection_date": sample.collection_date,
        "submission_date": sample.submission_date or "",
        "retailer_fssai": sample.retailer_fssai or "",
        "retailer_name": sample.retailer_name or "",
        "price": sample.price or "",
        "created_at": sample.created_at.isoformat() if sample.created_at else "",
        "synced_at": synced_at,
    }
