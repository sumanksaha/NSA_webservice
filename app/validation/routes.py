"""HTTP endpoints for the Phase 12 Legal Validation Engine.

- ``POST /validation/validate`` — JSON ``{"case_id": int, "case_type":
  "case_file"|"adjudication"}`` → structured validation report.
- ``GET /validation/case/<case_id>`` — validation report for a case,
  disambiguated via the ``?kind=`` query parameter (same contract as the
  timeline API).
"""

from __future__ import annotations

from flask import jsonify, request

from app.validation import validation_bp
from app.validation.engine import ValidationEngine

engine = ValidationEngine()

_VALID_CASE_TYPES = ("case_file", "adjudication")


@validation_bp.route("/validate", methods=["POST"])
def validate():
    """Run the validation engine and return the structured report."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    case_id = payload.get("case_id")
    case_type = payload.get("case_type")

    if not isinstance(case_id, int) or case_type not in _VALID_CASE_TYPES:
        return jsonify(
            {
                "error": "case_id (int) and case_type "
                "('case_file' | 'adjudication') are required.",
            }
        ), 400

    result = engine.validate_case(case_id, case_type)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@validation_bp.route("/case/<int:case_id>")
def case_summary(case_id):
    """Validation report for a case (``?kind=case_file|adjudication``)."""
    kind = request.args.get("kind")
    if kind is not None and kind not in _VALID_CASE_TYPES:
        return jsonify(
            {"error": "kind must be 'case_file' or 'adjudication'."}
        ), 400

    result = engine.validate_case(case_id, kind)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)
