"""sample_utils.py

Utilities for the Sample module, including sample_code generation.
"""

import re
from typing import Any


# Sample code validation patterns (FSO-entered, not auto-generated)
# Enforcement: SL/WB/BRANCHCODE/YEAR/SEQUENTIAL (e.g. SL/WB/110223/2026/25275)
ENFORCEMENT_CODE_PATTERN = re.compile(r"^SL/WB/\d{6}/\d{4}/\d{5}$")
# Surveillance: FSO/Br-XX/ABC/INF/XX/YY-YY (e.g. FSO/Br-02/SS/INF/05/26-27)
SURVEILLANCE_CODE_PATTERN = re.compile(r"^FSO/Br-\d{2}/[A-Z]{2,4}/INF/\d{1,2}/\d{2}-\d{2}$")

# Allowed sample status values captured at inspection time
SAMPLE_STATUS_VALUES = ("ok", "substandard", "misbranded", "unsafe")


def validate_sample_code(code: str | None, sample_type: str | None = None) -> str | None:
    """Validate a sample code against the format for its sample_type.

    Args:
        code: The sample code string, or None/empty.
        sample_type: ``"enforcement"`` or ``"surveillance"``.  When None the
            code is accepted if it matches *either* pattern.

    Returns:
        None on success, or an error message on failure.
    """
    if not code:
        return "sample_code is required"
    if sample_type == "enforcement" and not ENFORCEMENT_CODE_PATTERN.match(code):
        return "sample_code must match format SL/WB/BRANCHCODE/YEAR/SEQUENTIAL (e.g. SL/WB/110223/2026/25275)"
    if sample_type == "surveillance" and not SURVEILLANCE_CODE_PATTERN.match(code):
        return "sample_code must match format FSO/Br-XX/ABC/INF/XX/YY-YY (e.g. FSO/Br-02/SS/INF/05/26-27)"
    if sample_type is None:
        if not ENFORCEMENT_CODE_PATTERN.match(code) and not SURVEILLANCE_CODE_PATTERN.match(code):
            return "sample_code must match enforcement (SL/WB/XXXXXX/XXXX/XXXXX) or surveillance (FSO/Br-XX/ABC/INF/XX/YY-YY) format"
    return None


def validate_sample_status(status: str | None) -> str | None:
    """Validate a sample_status value.

    Returns None on success, or an error message on failure.
    """
    if not status:
        return "sample_status is required"
    if status not in SAMPLE_STATUS_VALUES:
        return f"sample_status must be one of {', '.join(SAMPLE_STATUS_VALUES)}"
    return None


def generate_sample_code() -> str:
    """Sample codes are manually entered by the FSO — no auto-generation.

    The ``sample_code`` field must be provided by the Food Safety Officer
    in the format ``SL/WB/XXXXXX/XXXX/XXXXX`` (enforcement) or
    ``FSO/Br-XX/ABC/INF/XX/YY-YY`` (surveillance).  This function exists
    only to surface the contract; calling it raises ``RuntimeError``.

    Raises:
        RuntimeError: Always — sample codes are FSO-entered, never generated.

    """
    raise RuntimeError("Sample codes are manually entered by the FSO. Use validate_sample_code() to check the format.")


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
