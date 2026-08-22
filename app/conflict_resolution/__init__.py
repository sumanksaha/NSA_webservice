"""Conflict-resolution queue blueprint (Phase B).

Surfaces unresolved :class:`ConflictLog` entries (competing values for the
same field from different sources) and lets a reviewer pick the authoritative
value. Resolution writes the chosen value back through the review workflow
(:func:`app.ocr_extraction.service.resolve_conflict`) so it lands in
``extracted_json`` and is logged as an OCRCorrection.
"""

from flask import Blueprint

conflict_resolution_bp = Blueprint(
    "conflict_resolution",
    __name__,
    template_folder="templates",
)

from app.conflict_resolution import routes  # noqa: F401
