"""Work Diary module.

Accumulates Inspections into a per-FSO work diary. Each diary row shows:
Date, Place of Visit, and Purpose/Activity, where Purpose is always one of
"Routine Inspection" or "Complaint" (derived from whether the source
Inspection records a ``problem``). The diary is rendered from the UI,
filterable by FSO / date range / purpose, previewable as print-ready HTML,
and downloadable as a PDF via the central WeasyPrint pipeline.
"""

from flask import Blueprint

workdiary_bp = Blueprint(
    "workdiary",
    __name__,
    url_prefix="/workdiary",
    template_folder="templates",
)

# Import routes after blueprint is defined so the route decorators register.
from app.workdiary import routes  # noqa: F401
