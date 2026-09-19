"""Deterministic template justifications for the chosen FSO Act.

No LLM — pure string rendering over the selected profile + anchor so the
advisory stays auditable and repeatable. The LLM may later render prose
*around* these bases but can never override the Act.
"""

from __future__ import annotations

from app.rag.advisor.ladder import ActionProfile
from app.rag.advisor.penalties import StatutoryAnchor


def game_theory_basis(profile: ActionProfile, anchor: StatutoryAnchor) -> str:
    """Minimax justification: least-cost admissible move under FBO defection."""
    return (
        "Minimax optimal against FBO defection; meets statutory threshold "
        f"(Deterrence: {profile.deterrence_power:.1f}, Cost: {profile.fso_cost:.1f}) "
        f"anchored on Section {anchor.section} ({anchor.title})."
    )


def talebian_basis(profile: ActionProfile, optionality_score: float) -> str:
    """Convexity justification: reversibility × information yield."""
    return (
        f"Optionality score: {optionality_score:.2f}. Preserves reversibility "
        f"({profile.reversibility:.1f}) with information yield "
        f"({profile.information_yield:.1f})."
    )
