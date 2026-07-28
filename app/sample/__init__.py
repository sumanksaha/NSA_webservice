"""
Sample module for NSA_webservice.

This module handles sample collection, tracking, and management.
"""

from flask import Blueprint

sample_bp = Blueprint("sample", __name__, template_folder="templates", static_folder="static")

from app.sample import routes
