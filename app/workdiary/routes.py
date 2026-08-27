"""Work Diary routes.

- ``GET /workdiary``         — filterable diary table (per FSO)
- ``GET /workdiary/preview`` — official FSO Work Diary report, print-ready
- ``GET /workdiary/pdf``     — PDF download of the same report

Preview and PDF share the official template (``report.html``, modelled on
``FSO_Work_Diary_Template.html``) and one query-string filter contract so
the UI can pass identical filters between list → preview → download.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import io
import re

from flask import render_template, request, send_file

from app.utils.fso_data import get_all_fso_names
from app.utils.pdf_utils import generate_pdf_from_html
from app.workdiary import workdiary_bp
from app.workdiary.engine import PURPOSE_COMPLAINT, PURPOSE_ROUTINE, WorkDiaryEngine

engine = WorkDiaryEngine()

MIN_REPORT_ROWS = 15  # blank rows kept in the printed form, per the template


def _visual_row_count(entries: list[dict]) -> int:
    """Count visual rows after date-merging (for padding calculation)."""
    return len([e for e in entries if e.get("is_first_in_date", True)])

_PURPOSE_CHOICES = (
    ("", "All"),
    ("routine", PURPOSE_ROUTINE),
    ("complaint", PURPOSE_COMPLAINT),
)


def _filters_from_request() -> dict[str, str | None]:
    """Extract the shared filter contract from the query string."""
    return {
        "fso_name": (request.args.get("fso_name") or "").strip() or None,
        "date_from": (request.args.get("date_from") or "").strip() or None,
        "date_to": (request.args.get("date_to") or "").strip() or None,
        "purpose": (request.args.get("purpose") or "").strip() or None,
    }


def _enforce_scope(filters: dict[str, str | None], *, strict: bool = False):
    """Lock diary filters to the bound FSO for non-admin users.

    ``strict`` (preview/PDF) bounces explicit cross-officer requests to the
    landing page with a flash; the interactive index just silently locks to
    the bound officer.
    """
    from flask import flash, redirect, url_for
    from flask_login import current_user

    from app.shared.rbac import scoped_officer_name

    scope = scoped_officer_name(current_user)
    if scope is None:
        return None
    if strict and filters.get("fso_name") not in (None, scope):
        flash("You can only view your own work diary.", "error")
        return redirect(url_for("case_file_generator.index"))
    filters["fso_name"] = scope
    return None


def _url_for_filters(endpoint: str, filters: dict[str, str | None]) -> str:
    """Build ``url_for(endpoint, **filters)``, dropping empty filter values."""
    from flask import url_for

    clean = {k: v for k, v in filters.items() if v}
    return url_for(endpoint, **clean)


def _pdf_filename(filters: dict[str, str | None]) -> str:
    parts = ["workdiary"]
    if filters.get("fso_name"):
        parts.append(re.sub(r"[^A-Za-z0-9_-]+", "_", filters["fso_name"]))
    if filters.get("date_from"):
        parts.append(filters["date_from"])
    if filters.get("date_to"):
        parts.append(filters["date_to"])
    return "_".join(parts) + ".pdf"


def _period_labels(filters: dict[str, str | None]) -> dict[str, str]:
    """Month/Year labels for the report header.

    Prefers the ``date_from`` filter; falls back to today. ``date_to``
    overrides the month when the range spans a boundary.
    """
    source = filters.get("date_to") or filters.get("date_from")
    when = None
    for candidate in (source,):
        if candidate:
            try:
                when = _dt.date.fromisoformat(candidate)
            except ValueError:
                when = None
    if when is None:
        when = _dt.date.today()
    return {
        "month_label": calendar.month_name[when.month],
        "year_label": str(when.year),
    }


@workdiary_bp.route("/")
def index():
    """Work Diary landing page: filters + accumulated inspection rows."""
    filters = _filters_from_request()
    blocked = _enforce_scope(filters)
    if blocked is not None:
        return blocked
    entries = engine.build_entries(**filters)
    return render_template(
        "workdiary/index.html",
        entries=entries,
        fso_names=get_all_fso_names(),
        purpose_choices=_PURPOSE_CHOICES,
        filters=filters,
        preview_url=_url_for_filters("workdiary.preview", filters),
        pdf_url=_url_for_filters("workdiary.pdf", filters),
    )


@workdiary_bp.route("/preview")
def preview():
    """Official Work Diary report, print-ready (opens in a new tab)."""
    filters = _filters_from_request()
    blocked = _enforce_scope(filters, strict=True)
    if blocked is not None:
        return blocked
    entries = engine.build_entries(**filters)
    visual = _visual_row_count(entries)
    return render_template(
        "workdiary/report.html",
        entries=entries,
        pad_rows=max(MIN_REPORT_ROWS - visual, 0),
        fso_label=filters.get("fso_name") or "\u00a0",
        **_period_labels(filters),
    )


@workdiary_bp.route("/save", methods=["POST"])
def save_edited():
    """Save an edited Work Diary HTML and generate PDF.

    Accepts a ``html`` form field with the full edited HTML document,
    saves it to disk, and returns the PDF bytes for download.
    """
    from flask import current_app, jsonify, request

    filters = _filters_from_request()
    blocked = _enforce_scope(filters, strict=True)
    if blocked is not None:
        return blocked

    html_content = request.form.get("html", "")
    if not html_content.strip():
        return jsonify({"error": "No HTML content provided."}), 400

    # Save the edited HTML to disk
    from datetime import UTC, datetime
    from pathlib import Path

    html_dir = Path(current_app.instance_path) / "workdiary" / "html"
    html_dir.mkdir(parents=True, exist_ok=True)
    ts = int(datetime.now(UTC).timestamp())
    fso_part = re.sub(r"[^A-Za-z0-9_-]+", "_", filters.get("fso_name") or "all")
    html_filename = f"workdiary_{fso_part}_{ts}.html"
    html_path = str(html_dir / html_filename)
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_content)

    # Generate PDF from the edited HTML
    pdf_bytes, pdf_error = generate_pdf_from_html(html_content)
    if pdf_bytes is None:
        return jsonify({"error": f"PDF generation failed: {pdf_error}"}), 503

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=_pdf_filename(filters),
    )


@workdiary_bp.route("/pdf")
def pdf():
    """Download the current diary report as a PDF."""
    filters = _filters_from_request()
    blocked = _enforce_scope(filters, strict=True)
    if blocked is not None:
        return blocked
    entries = engine.build_entries(**filters)
    visual = _visual_row_count(entries)
    html = render_template(
        "workdiary/report.html",
        entries=entries,
        pad_rows=max(MIN_REPORT_ROWS - visual, 0),
        fso_label=filters.get("fso_name") or "\u00a0",
        **_period_labels(filters),
    )
    pdf_bytes, pdf_error = generate_pdf_from_html(html)
    if pdf_bytes is None:
        return {"error": f"PDF generation failed: {pdf_error}"}, 503

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=_pdf_filename(filters),
    )
