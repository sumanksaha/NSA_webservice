"""Evidence management blueprint for NSA Webservice.

Phase 5 of the roadmap: a unified evidence library built on the single
``Evidence`` model (photo, video, report, licence, bill, lab_report) with
drag-and-drop multi-file upload, image compression + thumbnail generation,
categorization (type + tags), and search.
"""

from flask import Blueprint

evidence_bp = Blueprint(
    "evidence",
    __name__,
    template_folder="templates",
    static_folder="static",
)

# Import routes after blueprint is defined so the route decorators register.
from app.evidence import routes  # noqa: F401
