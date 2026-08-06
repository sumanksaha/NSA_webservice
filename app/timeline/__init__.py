"""Timeline engine module (plan.md Phase 13).

Auto-generates milestone events for a case from date fields across CaseFile,
Adjudication, Inspection, Sample, Annexure, and Evidence records, and renders
them as a vertical timeline + Gantt chart.
"""

from flask import Blueprint

timeline_bp = Blueprint(
    "timeline",
    __name__,
    url_prefix="/timeline",
    template_folder="templates",
)

# Import routes after blueprint is defined so the route decorators register.
from app.timeline import routes  # noqa: F401
