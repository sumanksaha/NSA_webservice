"""Settings module for NSA_webservice.

Provides administrative and settings functionality.
"""

from flask import Blueprint

settings_bp = Blueprint("settings", __name__, template_folder="templates", static_folder="static")

from app.settings import routes  # noqa: F401
