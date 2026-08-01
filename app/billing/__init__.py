"""Billing module for NSA_webservice.

Provides billing summary and export functionality for Sample data.
"""

from flask import Blueprint

billing_bp = Blueprint("billing", __name__, template_folder="templates", static_folder="static")

from app.billing import routes  # noqa: F401
