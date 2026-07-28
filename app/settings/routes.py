"""
Settings routes module.

Provides administrative routes including FSO sync.
"""

from flask import jsonify, render_template

# Import the blueprint from __init__.py
from app.settings import settings_bp
from app.utils.fso_data import get_all_fso_names, sync_fso_from_markdown


@settings_bp.route("/")
def index():
    """Settings dashboard."""
    fso_names = get_all_fso_names()
    return render_template("settings/index.html", fso_names=fso_names)


@settings_bp.route("/sync-fso", methods=["POST"])
def sync_fso():
    """Manual FSO sync trigger."""
    result = sync_fso_from_markdown()

    # Return result as JSON
    return jsonify({"status": "success" if not result.get("errors") else "partial", "result": result})
