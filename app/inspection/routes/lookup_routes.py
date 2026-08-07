"""Lookup routes for FSSAI and CE license prefill/autocomplete."""

from flask import jsonify, request

from app.extensions import csrf
from app.inspection import inspection_bp
from app.utils.lookup import lookup_ce, lookup_fssai


@csrf.exempt
@inspection_bp.route("/lookup_fssai", methods=["POST"])
def lookup_fssai_route():
    """Lookup FSSAI license information."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    fssai_number = data.get("fssai_license", "").strip()
    if not fssai_number:
        return jsonify({"error": "FSSAI license number is required"}), 400

    result, error = lookup_fssai(fssai_number)

    if error:
        return jsonify({"error": error, "source": "fssai"}), 404

    if result:
        return jsonify(
            {
                "fbo_name": result.get("companyName"),
                "fbo_address": result.get("fullAddress"),
                "expiry_date": result.get("expiryDate"),
                "source": result.get("source"),
            }
        )

    return jsonify({"error": "FSSAI license not found"}), 404


@csrf.exempt
@inspection_bp.route("/lookup_ce", methods=["POST"])
def lookup_ce_route():
    """Lookup CE (KMC Trade) license information."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    ce_number = data.get("ce_license_no", "").strip()
    if not ce_number:
        return jsonify({"error": "CE license number is required"}), 400

    try:
        result = lookup_ce(ce_number)
    except Exception as e:
        return jsonify({"error": f"KMC lookup failed: {e!s}"}), 502

    if not result:
        return jsonify({"error": "CE license not found"}), 404

    return jsonify(result)
