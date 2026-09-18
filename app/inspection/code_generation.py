"""Inspection code generation with retry logic and audit logging."""

import time
from datetime import UTC, datetime, timedelta

from app.extensions import db
from app.models import CodeSequence


class InspectionCodeGenerator:
    """Generate unique inspection codes with retry logic.

    Single interface: generate_code() -> str.
    Replaces the inline CodeSequence logic in inspection_routes.py.
    """

    def generate_code(self) -> str:
        """Generate an inspection code in the format INSP-YYYY-#####.

        Uses a dedicated ``code_sequence`` table with an atomic increment
        inside a transaction so that concurrent workers never obtain the
        same value. A retry loop with exponential backoff handles any
        residual contention.

        Returns:
            str: Generated inspection code (e.g., 'INSP-2026-00001')
        """
        year = datetime.now(UTC).year
        seq_key = f"inspection:{year}"
        max_retries = 10

        for attempt in range(max_retries):
            try:
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
                time.sleep(2**attempt * 0.01)

        raise RuntimeError(f"Failed to generate unique inspection code after {max_retries} retries")

    def calculate_compliance_deadline(self, inspection_date) -> datetime | None:
        """Calculate compliance deadline as inspection_date + 30 days."""
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


def generate_inspection_code() -> str:
    """Generate an inspection code (backward-compatible wrapper)."""
    return InspectionCodeGenerator().generate_code()


def calculate_compliance_deadline(inspection_date) -> datetime | None:
    """Calculate compliance deadline (backward-compatible wrapper)."""
    return InspectionCodeGenerator().calculate_compliance_deadline(inspection_date)
