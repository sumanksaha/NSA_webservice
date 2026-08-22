"""Analytics dashboard blueprint (Phase 15).

Provides aggregate SQL queries over CaseFile, Adjudication, Inspection,
Sample, FboIssue, and Evidence records, rendered as an interactive dashboard
with Chart.js charts and a Leaflet.js map.
"""

from flask import Blueprint

analytics_bp = Blueprint(
    "analytics",
    __name__,
    template_folder="templates",
)

from app.analytics import routes  # noqa: F401
