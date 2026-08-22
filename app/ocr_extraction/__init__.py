"""OCR review workflow (Phase B).

Blueprint at ``/ocr``: lists extracted documents, renders an editable review
form per document, and accepts field/lab-parameter corrections. Every manual
edit writes an :class:`OCRCorrection` row (Phase D consumes these for the
feedback loop); a correction that disagrees with a lab-report value for the
same field opens a :class:`ConflictLog` entry for the resolution queue.
"""

from flask import Blueprint

ocr_extraction_bp = Blueprint(
    "ocr_extraction",
    __name__,
    template_folder="templates",
)

from app.ocr_extraction import routes  # noqa: F401
