"""Phase 3 step 5 — evidence-confidence controller.

Maps sufficiency/verification signals to an explicit HIGH / MEDIUM / LOW
confidence tier consumed by synthesis, abstention, and audit. Deterministic
and testable: no LLM, just thresholds over measured signals.
"""

from __future__ import annotations

from typing import Any

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"


def evidence_confidence(
    coverage: float = 0.0,
    authority_score: float = 0.0,
    temporal_valid: bool = True,
    has_conflicts: bool = False,
    claim_groundedness: float = 0.0,
) -> str:
    """Return HIGH / MEDIUM / LOW for the given evidence signals."""
    if not temporal_valid or has_conflicts:
        # Contradicted or temporally invalid evidence can never be HIGH.
        if coverage < 0.5 or claim_groundedness < 0.5:
            return CONFIDENCE_LOW
        return CONFIDENCE_MEDIUM
    if coverage >= 0.75 and authority_score >= 0.6 and claim_groundedness >= 0.7:
        return CONFIDENCE_HIGH
    if coverage >= 0.4 and claim_groundedness >= 0.4:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def confidence_from_state(state: dict[str, Any]) -> str:
    """Convenience: derive the tier from an agent-graph state dict."""
    return evidence_confidence(
        coverage=float(state.get("evidence_coverage", 0.0) or 0.0),
        authority_score=float(state.get("authority_score", 0.0) or 0.0),
        temporal_valid=not bool(state.get("temporal_conflict", False)),
        has_conflicts=bool(state.get("has_conflicts", False)),
        claim_groundedness=float(state.get("claim_groundedness", state.get("groundedness", 0.0)) or 0.0),
    )
