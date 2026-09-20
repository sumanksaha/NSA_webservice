"""Zero-sum enforcement game between the FSO and the FBO (ADR-0006).

The FSO picks one rung of the escalation ladder; the FBO responds with one
of three strategies. The FSO's payoff for act ``a`` under FBO response ``s``
is::

    U(a, s) = retention(a, s) · deterrence(a) − cost(a) − response_cost(a, s)

- ``retention(a, s)`` — fraction of the act's statutory effect that survives
  the FBO's response: compliance preserves it fully; contest attenuates it
  through appellate exposure; defiance leaves only follow-on enforcement.
- ``deterrence(a)`` / ``cost(a)`` — the ``ActionProfile`` parameters from
  :mod:`app.rag.advisor.ladder`.
- ``response_cost(a, s)`` — the FSO's own incremental enforcement burden
  that the response triggers (litigation when contested, follow-on
  enforcement when defied).

The game is **zero-sum** (FBO payoff = −U), so the FBO best response to act
``a`` is ``argmin_s U(a, s)`` — *computed*, never assumed — and the FSO's
robust (maximin) value of ``a`` is that minimum. The selector combines the
maximin value with the Talebian optionality score (ADR-0006); the invariant
test ``test_floor_act_maximizes_robust_score`` fails loudly if retuning ever
breaks the documented ordering.

Tuning lives here (single point), alongside ``ladder.py``.
"""

from __future__ import annotations

from enum import Enum

from app.rag.advisor.ladder import ACTION_PROFILES, ActionProfile, EscalationLevel

__all__ = [
    "FSO_RESPONSE_COST",
    "RETENTION",
    "FboStrategy",
    "fbo_best_response",
    "fso_payoff",
    "fso_payoff_matrix",
]


class FboStrategy(Enum):
    """FBO response strategies (blueprint §2.A, player 2)."""

    COMPLY = "COMPLY"
    CONTEST = "CONTEST"
    DEFECT = "DEFECT"


#: Fraction of the act's statutory effect that survives each FBO response.
#: COMPLY is always 1.0 (compliance *is* the statutory outcome). CONTEST
#: attenuates by appellate/tribunal exposure (lab findings are the hardest
#: to challenge, a verbal warn the easiest). DEFECT is the enforcement gap:
#: a warn is ignored almost freely, a notice is the classic defied act, a
#: cognizable prosecution cannot be ignored at all (arrest/attachment).
RETENTION: dict[EscalationLevel, dict[FboStrategy, float]] = {
    EscalationLevel.INSPECT_WARN: {FboStrategy.COMPLY: 1.0, FboStrategy.CONTEST: 0.6, FboStrategy.DEFECT: 0.1},
    EscalationLevel.SAMPLE_LAB_TEST: {FboStrategy.COMPLY: 1.0, FboStrategy.CONTEST: 0.9, FboStrategy.DEFECT: 0.6},
    EscalationLevel.IMPROVEMENT_NOTICE: {FboStrategy.COMPLY: 1.0, FboStrategy.CONTEST: 0.6, FboStrategy.DEFECT: 0.3},
    EscalationLevel.PENALTY_DIRECTION: {FboStrategy.COMPLY: 1.0, FboStrategy.CONTEST: 0.7, FboStrategy.DEFECT: 0.6},
    EscalationLevel.PROSECUTION: {FboStrategy.COMPLY: 1.0, FboStrategy.CONTEST: 0.8, FboStrategy.DEFECT: 1.0},
}

#: FSO's incremental enforcement burden triggered by each FBO response
#: (on top of the act's base ``fso_cost``). COMPLY adds nothing. CONTEST
#: shifts litigation/appellate work onto the FSO (heaviest at trial).
#: DEFECT forces follow-on enforcement — re-visit and escalate after a
#: defied notice, prosecute a defied penalty direction — but executing an
#: already-ordered cognizable prosecution is comparatively cheap.
FSO_RESPONSE_COST: dict[EscalationLevel, dict[FboStrategy, float]] = {
    EscalationLevel.INSPECT_WARN: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: 0.5, FboStrategy.DEFECT: 0.5},
    EscalationLevel.SAMPLE_LAB_TEST: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: 1.0, FboStrategy.DEFECT: 0.5},
    EscalationLevel.IMPROVEMENT_NOTICE: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: 1.0, FboStrategy.DEFECT: 1.5},
    EscalationLevel.PENALTY_DIRECTION: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: 2.0, FboStrategy.DEFECT: 2.5},
    EscalationLevel.PROSECUTION: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: 3.0, FboStrategy.DEFECT: 1.0},
}


def fso_payoff(profile: ActionProfile, strategy: FboStrategy) -> float:
    """FSO payoff U(a, s) — see module docstring."""
    return (
        RETENTION[profile.level][strategy] * profile.deterrence_power
        - profile.fso_cost
        - FSO_RESPONSE_COST[profile.level][strategy]
    )


def fbo_best_response(profile: ActionProfile) -> tuple[FboStrategy, float]:
    """Zero-sum FBO best response to ``profile`` and the FSO's maximin value.

    Returns ``(argmin_s U(a, s), min_s U(a, s))``. Ties resolve to the
    earliest strategy in declaration order (deterministic).
    """
    best_strategy = FboStrategy.COMPLY
    best_value = fso_payoff(profile, best_strategy)
    for strategy in FboStrategy:
        value = fso_payoff(profile, strategy)
        if value < best_value:
            best_strategy, best_value = strategy, value
    return best_strategy, best_value


def fso_payoff_matrix() -> dict[EscalationLevel, dict[FboStrategy, float]]:
    """Full U table — audit/telemetry helper (also used by the tests)."""
    return {
        level: {strategy: fso_payoff(profile, strategy) for strategy in FboStrategy}
        for level, profile in ACTION_PROFILES.items()
    }
