"""Annexure management blueprint for NSA Webservice.

Phase 4 of the roadmap: upload supporting documents (PDF, JPG, PNG, DOCX)
attached to a case file or adjudication, with automatic metadata extraction
(SHA-256 hash, page count, OCR text) and A/B/C annexure-letter assignment.
"""

from flask import Blueprint

annexure_bp = Blueprint(
    "annexure",
    __name__,
    template_folder="templates",
)

# Import routes after blueprint is defined so the route decorators register.
from app.annexure import routes  # noqa: F401
