"""
Inspection module for NSA_webservice.

Provides inspection entry, tracking, and management.
"""

from flask import Blueprint

inspection_bp = Blueprint("inspection", __name__, template_folder="templates", static_folder="static")

from app.inspection import routes
