"""Deterministic template justifications for the chosen FSO Act.

No LLM — pure string rendering over the computed game facts + anchor so
the advisory stays auditable and repeatable. Every claim in
``game_theory_basis`` is a *computed* quantity from
:mod:`app.rag.advisor.game` / :mod:`app.rag.advisor.selector` (ADR-0006),
not a narrative assertion. The LLM may later render prose *around* these
bases but can never override the Act.
"""

from __future__ import annotations

from app.rag.advisor.ladder import ActionProfile, EscalationLevel
from app.rag.advisor.penalties import StatutoryAnchor


def game_theory_basis(
    anchor: StatutoryAnchor,
    *,
    maximin_value: float,
    fbo_best_response: str,
    floor: EscalationLevel,
    floor_reason: str,
    margin: float | None,
) -> str:
    """Render the computed minimax facts for the selected Act.

    ``margin`` is the robust-score gap to the next admissible act, or
    ``None`` when the floor leaves no alternative (the floor alone decides).
    """
    basis = (
        f"Zero-sum minimax vs FBO: {fbo_best_response} best response holds the Act's "
        f"robust value to {maximin_value:+.2f}; statutory floor {floor.name} binds "
        f"admissibility ({floor_reason}); "
    )
    if margin is None:
        basis += "no admissible alternative — the floor decides. "
    else:
        basis += f"margin over next admissible act {margin:+.2f}. "
    basis += f"Anchored on Section {anchor.section} ({anchor.title})."
    return basis


def talebian_basis(profile: ActionProfile, optionality_score: float) -> str:
    """Convexity justification: reversibility × information yield."""
    return (
        f"Optionality score: {optionality_score:.2f}. Preserves reversibility "
        f"({profile.reversibility:.1f}) with information yield "
        f"({profile.information_yield:.1f})."
    )
