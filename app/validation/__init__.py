"""Legal Validation Engine blueprint (plan.md Phase 12).

Rule-based validation of case files and adjudications: mandatory section
presence, statutory reference accuracy (FSSA 2006), signature placeholders,
numbering formats, date-sequence consistency, duplicate evidence, and
document completeness.  Zero external dependencies — reuses the canonical
section data (``app.utils.sections_data``) and the rule suggester
(``app.utils.suggester``).
"""

from flask import Blueprint

validation_bp = Blueprint(
    "validation",
    __name__,
    url_prefix="/validation",
)

# Import routes after the blueprint is defined so the route decorators
# register (same pattern as app/timeline/__init__.py).
from app.validation import routes  # noqa: F401
