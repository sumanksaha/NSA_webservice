"""sample_utils.py

Utilities for the Sample module, including sample_code generation.
"""

from typing import Any


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
