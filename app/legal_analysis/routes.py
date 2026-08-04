"""Legal analysis routes (T-46 engine integration).

Exposes a GET workbench page and a POST JSON endpoint backed by the
:mod:`app.services.legal_engine` service layer.
"""

from __future__ import annotations

from typing import Any

from flask import jsonify, render_template, request

from app.legal_analysis import legal_analysis_bp
from app.services.legal_engine import analyze_legal_text


@legal_analysis_bp.route("/")
def index():
    """Render the analysis workbench page."""
    return render_template("legal_analysis/index.html")


@legal_analysis_bp.route("/analyze", methods=["POST"])
def analyze():
    """Analyze pasted legal text and return structured JSON."""
    payload: Any = request.get_json(silent=True)
    text = (payload or {}).get("text") if isinstance(payload, dict) else None

    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Please provide some legal text to analyze."}), 400

    try:
        result = analyze_legal_text(text.strip())
    except ImportError as exc:
        return jsonify({"error": str(exc)}), 503
    except RuntimeError as exc:
        return jsonify({"error": f"Engine failure: {exc}"}), 500

    return jsonify(result)
