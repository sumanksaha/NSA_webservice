"""AI Case Intelligence blueprint (plan.md Phase 19).

Calculates evidence strength, traceability, and readiness score for case files and adjudications.
Uses AI-powered analysis to assess how strong/complete/ready a case is.
"""

from flask import Blueprint

intelligence_bp = Blueprint(
    "intelligence",
    __name__,
    url_prefix="/case-intelligence",
)

# Import routes after the blueprint is defined so the route decorators
# register (same pattern as app/validation/__init__.py).
from app.case_intelligence import routes  # noqa: F401
