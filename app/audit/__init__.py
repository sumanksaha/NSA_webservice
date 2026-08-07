from flask import Blueprint

audit_bp = Blueprint(
    "audit",
    __name__,
    template_folder="templates",
)

from app.audit import routes  # noqa: F401 — register audit routes on the blueprint
