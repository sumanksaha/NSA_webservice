"""
Billing routes module.

Provides billing summary and export functionality for Sample data.
"""

from flask import render_template, request, send_file

# Import the blueprint from __init__.py
from app.billing import billing_bp
from app.billing.billing_utils import compute_summary, generate_excel_report
from app.models import Sample
from app.utils.filters import parse_date
from app.utils.fso_data import get_all_fso_names

# Sample types: enforcement or surveillance only
SAMPLE_TYPES = ["enforcement", "surveillance"]


@billing_bp.route("/")
def index():
    """Billing summary view with filters and export."""
    # Get filter parameters
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    fso_name = request.args.get("fso_name", "")
    sample_type = request.args.get("sample_type", "")

    # Build query
    query = Sample.query

    # Apply date filters
    if start_date:
        parsed_start = parse_date(start_date)
        if parsed_start:
            query = query.filter(Sample.collection_date >= parsed_start)
    if end_date:
        parsed_end = parse_date(end_date)
        if parsed_end:
            query = query.filter(Sample.collection_date <= parsed_end)

    # Apply optional filters
    if fso_name:
        query = query.filter(Sample.fso_name == fso_name)
    if sample_type:
        query = query.filter(Sample.sample_type == sample_type)

    # Order by collection_date descending
    samples = query.order_by(Sample.collection_date.desc()).all()

    # Compute summary
    summary = compute_summary(samples)

    # Get filter options
    all_fso_names = get_all_fso_names()

    return render_template(
        "billing/index.html",
        samples=samples,
        summary=summary,
        start_date=start_date,
        end_date=end_date,
        fso_name=fso_name,
        sample_type=sample_type,
        fso_names=all_fso_names,
        sample_types=SAMPLE_TYPES,
    )


@billing_bp.route("/export", methods=["GET"])
def export_excel():
    """Export current filtered view to Excel."""
    # Get the same filter parameters as the index view
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    fso_name = request.args.get("fso_name", "")
    sample_type = request.args.get("sample_type", "")

    # Build query (same as index view)
    query = Sample.query

    if start_date:
        parsed_start = parse_date(start_date)
        if parsed_start:
            query = query.filter(Sample.collection_date >= parsed_start)
    if end_date:
        parsed_end = parse_date(end_date)
        if parsed_end:
            query = query.filter(Sample.collection_date <= parsed_end)
    if fso_name:
        query = query.filter(Sample.fso_name == fso_name)
    if sample_type:
        query = query.filter(Sample.sample_type == sample_type)

    samples = query.order_by(Sample.collection_date.desc()).all()

    # Compute summary
    summary = compute_summary(samples)

    # Generate Excel report
    excel_file, filename = generate_excel_report(samples, summary, start_date, end_date)

    return send_file(
        excel_file,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
