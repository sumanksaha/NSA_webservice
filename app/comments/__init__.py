"""Comments API package — Phase 18 RBAC completion."""

from flask import Blueprint

comments_bp = Blueprint("comments", __name__, url_prefix="/comments")

# Import routes after blueprint is defined so the route decorators register.
from app.comments import routes  # noqa: F401
