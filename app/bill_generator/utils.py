"""Bill Generator Utilities

Shared query helpers for bill generation and preview.
"""

from app.extensions import db
from app.models import BillSample, Sample
from app.utils.filters import parse_date


def get_billable_samples(start_date, end_date):
    """Get billable samples for a date range, split by type.

    Args:
        start_date: ISO date string (YYYY-MM-DD)
        end_date: ISO date string (YYYY-MM-DD)

    Returns:
        dict with:
        - enforcement_no: int
        - enforcement_price: float
        - surveillance_no: int
        - surveillance_price: float
        - samples: list of dicts with si_no, sample_code, sample_name, retailer_name, price, type

    """
    # Parse date strings to datetime objects for proper range comparison
    parsed_start = parse_date(start_date)
    parsed_end = parse_date(end_date)

    # Query unbilled samples in date range with normalized types
    query = Sample.query.filter(
        Sample.collection_date >= parsed_start,
        Sample.collection_date <= parsed_end,
        ~Sample.billed,
        Sample.sample_type.in_(["enforcement", "surveillance"]),
    ).order_by(Sample.collection_date)

    samples = query.all()

    # Split and aggregate
    enforcement_samples = [s for s in samples if s.sample_type == "enforcement"]
    surveillance_samples = [s for s in samples if s.sample_type == "surveillance"]

    def safe_price(p):
        try:
            return float(p) if p else 0.0
        except (ValueError, TypeError):
            return 0.0

    enforcement_no = len(enforcement_samples)
    enforcement_price = sum(safe_price(s.price) for s in enforcement_samples)

    surveillance_no = len(surveillance_samples)
    surveillance_price = sum(safe_price(s.price) for s in surveillance_samples)

    # Build sample list with 1-based index
    sample_list = []
    for i, s in enumerate(samples, 1):
        sample_list.append({
            "si_no": i,
            "sample_id": s.id,
            "sample_code": s.sample_code or "",
            "sample_name": s.sample_name or "",
            "retailer_name": s.retailer_name or "",
            "price": safe_price(s.price),
            "type": s.sample_type or "",
        })

    return {
        "enforcement_no": enforcement_no,
        "enforcement_price": enforcement_price,
        "surveillance_no": surveillance_no,
        "surveillance_price": surveillance_price,
        "samples": sample_list,
    }


def mark_samples_as_billed(sample_ids, bill_id):
    """Stage samples as billed and link them to the bill.

    Does NOT commit — the caller owns the transaction so the Bill row, the
    ``billed`` flags, and the ``BillSample`` links land in ONE atomic commit
    (ADR-0001: no Bill exists unless its Samples are marked billed).

    Args:
        sample_ids: list of sample IDs to mark
        bill_id: the bill ID to link samples to

    """
    if not sample_ids or not bill_id:
        return

    # Mark as billed
    Sample.query.filter(Sample.id.in_(sample_ids)).update({"billed": True})

    # Create junction table entries
    for sample_id in sample_ids:
        # Check if already linked
        exists = BillSample.query.filter_by(bill_id=bill_id, sample_id=sample_id).first()
        if not exists:
            db.session.add(BillSample(bill_id=bill_id, sample_id=sample_id))
