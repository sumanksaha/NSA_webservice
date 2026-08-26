"""Analytics dashboard routes (Phase 15).

Provides:
- ``GET /analytics/`` — renders the interactive dashboard.
- ``GET /analytics/api/metrics`` — JSON API returning aggregate metrics.

Queries cover: case statuses, inspection compliance, sample pipeline,
legal provisions cited, FSO activity, FBO issues, and monthly case trends.
All queries use lightweight aggregate SQL — no heavy ORM hydration.
"""

from __future__ import annotations

import logging

from flask import jsonify, render_template
from sqlalchemy import func

from app.analytics import analytics_bp
from app.extensions import db
from app.models import (
    Adjudication,
    CaseFile,
    Evidence,
    FboIssue,
    Inspection,
    Sample,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Aggregate query helpers
# --------------------------------------------------------------------------- #


def _case_status_counts() -> list[dict]:
    """Count CaseFile + Adjudication records by creation month."""
    case_counts = (
        db.session
        .query(
            func.strftime("%Y-%m", CaseFile.created_at).label("month"),
            func.count(CaseFile.id).label("count"),
        )
        .group_by("month")
        .order_by("month")
        .all()
    )
    adj_counts = (
        db.session
        .query(
            func.strftime("%Y-%m", Adjudication.created_at).label("month"),
            func.count(Adjudication.id).label("count"),
        )
        .group_by("month")
        .order_by("month")
        .all()
    )
    # Merge into a single series
    months: dict[str, dict[str, int]] = {}
    for month, case_n in case_counts:  # type: ignore[misc]
        months.setdefault(month, {"case_files": 0, "adjudications": 0})
        months[month]["case_files"] = int(case_n)
    for month, adj_n in adj_counts:  # type: ignore[misc]
        months.setdefault(month, {"case_files": 0, "adjudications": 0})
        months[month]["adjudications"] = int(adj_n)
    return [
        {"month": m, "case_files": v["case_files"], "adjudications": v["adjudications"]}
        for m, v in sorted(months.items())
    ]


def _inspection_compliance() -> dict:
    """Count inspections by compliance status (dismissed vs active)."""
    rows = (
        db.session
        .query(
            Inspection.is_dismissed,
            func.count(Inspection.id).label("count"),
        )
        .group_by(Inspection.is_dismissed)
        .all()
    )
    result = {"active": 0, "dismissed": 0}
    for row in rows:
        if row.is_dismissed:
            result["dismissed"] = row.count
        else:
            result["active"] = row.count
    result["total"] = result["active"] + result["dismissed"]
    return result


def _sample_pipeline() -> list[dict]:
    """Breakdown of samples by collection month and billed status."""
    rows = (
        db.session
        .query(
            func.strftime("%Y-%m", Sample.collection_date).label("month"),
            Sample.billed,
            func.count(Sample.id).label("count"),
        )
        .group_by("month", Sample.billed)
        .order_by("month")
        .all()
    )
    months: dict[str, dict[str, int]] = {}
    for row in rows:
        months.setdefault(row.month, {"billed": 0, "unbilled": 0})
        if row.billed:
            months[row.month]["billed"] = row.count
        else:
            months[row.month]["unbilled"] = row.count
    return [{"month": m, "billed": v["billed"], "unbilled": v["unbilled"]} for m, v in sorted(months.items())]


def _legal_provisions() -> list[dict]:
    """Count FSSA 2006 sections cited across adjudications."""
    sections = {
        "section_55": "Section 55 (Penalty)",
        "section_56": "Section 56 (Hygiene)",
        "section_58": "Section 58 (Sub-standard)",
        "section_63": "Section 63 (Unlicensed)",
        "section_64": "Section 64 (Repeated)",
    }
    result = []
    for col, label in sections.items():
        count = db.session.query(func.count(Adjudication.id)).filter(getattr(Adjudication, col) == "yes").scalar()
        result.append({"section": label, "count": count})
    return result


def _fso_activity() -> list[dict]:
    """Count inspections per FSO (top 15)."""
    rows = (
        db.session
        .query(
            Inspection.fso_name,
            func.count(Inspection.id).label("count"),
        )
        .group_by(Inspection.fso_name)
        .order_by(func.count(Inspection.id).desc())
        .limit(15)
        .all()
    )
    return [{"fso": row.fso_name, "count": row.count} for row in rows]


def _fbo_issue_summary() -> dict:
    """Count FBO issues by state."""
    rows = (
        db.session
        .query(
            FboIssue.state,
            func.count(FboIssue.id).label("count"),
        )
        .group_by(FboIssue.state)
        .all()
    )
    result = {"total": 0}
    for row in rows:
        result[row.state] = row.count
        result["total"] += row.count
    return result


def _evidence_summary() -> dict:
    """Count evidence records by type."""
    rows = (
        db.session
        .query(
            Evidence.evidence_type,
            func.count(Evidence.id).label("count"),  # pyright: ignore[reportArgumentType]
        )
        .group_by(Evidence.evidence_type)
        .all()
    )
    result = {"total": 0}
    for row in rows:
        result[row.evidence_type] = row.count
        result["total"] += row.count
    return result


def _geo_map_data() -> list[dict]:
    """Select FBO locations with non-null coordinates for the Leaflet map."""
    rows = (
        db.session
        .query(
            FboIssue.fbo_name,
            FboIssue.reg_lat,
            FboIssue.reg_lng,
            FboIssue.state,
            FboIssue.source_type,
        )
        .filter(FboIssue.reg_lat.isnot(None), FboIssue.reg_lng.isnot(None))
        .all()
    )
    return [
        {
            "name": r.fbo_name,
            "lat": r.reg_lat,
            "lng": r.reg_lng,
            "state": r.state,
            "source": r.source_type,
        }
        for r in rows
    ]


def _summary_counts() -> dict:
    """Quick row counts across all major tables."""
    return {
        "case_files": db.session.query(func.count(CaseFile.id)).scalar() or 0,
        "adjudications": db.session.query(func.count(Adjudication.id)).scalar() or 0,
        "inspections": db.session.query(func.count(Inspection.id)).scalar() or 0,
        "samples": db.session.query(func.count(Sample.id)).scalar() or 0,
        "evidence": db.session.query(func.count(Evidence.id)).scalar() or 0,  # pyright: ignore[reportArgumentType]
        "fbo_issues": db.session.query(func.count(FboIssue.id)).scalar() or 0,
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@analytics_bp.route("/")
def dashboard():
    """Render the analytics dashboard page."""
    return render_template("analytics/dashboard.html")


@analytics_bp.route("/api/metrics")
def metrics_api():
    """Return all aggregate metrics as a single JSON payload.

    The dashboard JS fetches this endpoint once on load and renders
    Chart.js charts + a Leaflet map from the response.
    """
    try:
        data = {
            "summary": _summary_counts(),
            "case_trends": _case_status_counts(),
            "inspection_compliance": _inspection_compliance(),
            "sample_pipeline": _sample_pipeline(),
            "legal_provisions": _legal_provisions(),
            "fso_activity": _fso_activity(),
            "fbo_issues": _fbo_issue_summary(),
            "evidence": _evidence_summary(),
            "geo_data": _geo_map_data(),
        }
        return jsonify(data)
    except Exception as exc:
        logger.error("Analytics metrics query failed: %s", exc)
        return jsonify({"error": f"Metrics query failed: {exc}"}), 500


# End of routes.py
