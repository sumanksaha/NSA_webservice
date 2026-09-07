"""API routes for the AI Case Intelligence blueprint (plan.md Phase 19).

Endpoints:
- GET /case-intelligence/<int:case_id>/scores - returns evidence strength,
  traceability, and readiness scores for the specified case.
- GET /case-intelligence/<int:case_id>/summary - returns a concise summary
  of the case intelligence assessment.
"""

from flask import jsonify, request

from app.case_intelligence import intelligence_bp
from app.case_intelligence.engine import (
    calculate_intelligence_scores,
    EvidenceStrengthScore,
    ReadinessScore,
)


@intelligence_bp.route("/<int:case_id>/scores", methods=["GET"])
def get_case_intelligence_scores(case_id: int):
    """Get evidence strength, traceability, and readiness scores for a case.

    Args:
        case_id: The case file or adjudication primary key.

    Returns:
        JSON payload with keys:
        - evidence_strength: one of [strong, moderate, weak, none]
        - traceability: float (0.0 to 1.0)
        - readiness: one of [ready, needs_attention, not_ready]
        - scores: dict with numeric representations
    """
    case_type = request.args.get("case_type")
    result = calculate_intelligence_scores(case_id, case_type)
    # Convert enums to their values for JSON serialization
    result["evidence_strength"] = result["evidence_strength"].value
    result["readiness"] = result["readiness"].value
    return jsonify(result)


@intelligence_bp.route("/<int:case_id>/summary", methods=["GET"])
def get_intelligence_summary(case_id: int):
    """Get a concise intelligence summary for a case.

    Args:
        case_id: The case file or adjudication primary key.

    Returns:
        JSON payload with a human-readable summary of the case intelligence.
    """
    case_type = request.args.get("case_type")
    result = calculate_intelligence_scores(case_id, case_type)

    evidence_strength = result["evidence_strength"].value
    traceability = result["traceability"]
    readiness = result["readiness"]

    summary = {
        "case_id": case_id,
        "evidence_strength": evidence_strength,
        "traceability_score": round(traceability, 2),
        "readiness": readiness.value,
        "readiness_label": readiness.value.replace("_", " ").title(),
        "assessment": _build_assessment(evidence_strength, traceability, readiness.value),
    }
    return jsonify(summary)


def _build_assessment(
    evidence_strength: str, traceability: float, readiness: str
) -> dict:
    """Build a human-readable assessment narrative."""
    assessment_parts = []

    # Evidence strength contribution
    if evidence_strength == "strong":
        assessment_parts.append("Strong evidence base with multiple supporting records.")
    elif evidence_strength == "moderate":
        assessment_parts.append("Moderate evidence coverage with some gaps.")
    elif evidence_strength == "weak":
        assessment_parts.append("Weak evidence coverage — additional records recommended.")
    else:
        assessment_parts.append("No evidence records found.")

    # Traceability contribution
    traceability_label = f"Traceability: {round(traceability * 100, 1)}% well-connected evidence."
    assessment_parts.append(traceability_label)

    # Readiness contribution
    readiness_label = f"Readiness: {readiness.replace('_', ' ').title()}."
    assessment_parts.append(readiness_label)

    return {"narrative": " ".join(assessment_parts)}