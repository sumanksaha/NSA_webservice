"""Legal document analysis module.

Provides a browser-facing workbench for the standalone legal paragraph
detection engine (T-46 integration): paste text, get a structured breakdown
of sections, clauses, sub-clauses, citations and confidence scores.
"""

from flask import Blueprint

legal_analysis_bp = Blueprint(
    "legal_analysis",
    __name__,
    template_folder="templates",
)

from app.legal_analysis import routes  # noqa: F401
