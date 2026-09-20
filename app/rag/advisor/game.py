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
    "DELTA_FIRST_TIME",
    "DELTA_REPEAT",
    "FboStrategy",
    "continuation_discount",
    "escalation_subgame_values",
    "fbo_best_response",
    "fso_payoff",
    "fso_payoff_matrix",
    "sequential_stage_values",
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


# ---------------------------------------------------------------------------
# Extensive-form escalation game (ADR-0007)
# ---------------------------------------------------------------------------

#: Repeated-game continuation discount (ADR-0007). The FSO–FBO relationship
#: repeats; the escalation threat behind every low rung is only worth what a
#: future encounter will actually deliver. A first-time FBO weighs future
#: statutory consequences fully (δ = 1.0 — the ladder's option value is
#: intact). A *convicted* FBO has revealed that defiance is sustainable:
#: the escalation threat loses credibility (δ = 0.3), so only the immediate
#: statutory effect of the current act counts. The statutory floor still
#: escalates repeat offences by law (§64); δ explains *why* that is also
#: strategically sound — it is reported in the payload, not assumed.
DELTA_FIRST_TIME = 1.0
DELTA_REPEAT = 0.3


def continuation_discount(has_prior_violations: bool) -> float:
    """Map the FBO's offence history to the continuation discount δ."""
    return DELTA_REPEAT if has_prior_violations else DELTA_FIRST_TIME


def sequential_stage_values(
    *,
    retention: dict[EscalationLevel, dict[FboStrategy, float]] | None = None,
    response_cost: dict[EscalationLevel, dict[FboStrategy, float]] | None = None,
    profiles: dict[EscalationLevel, ActionProfile] | None = None,
    discount: float = DELTA_FIRST_TIME,
) -> dict[EscalationLevel, dict[FboStrategy, float]]:
    """Solve the escalation game by backward induction (top rung down).

    The tree: the FSO plays act ``a_ℓ`` → the FBO responds ``s`` → on a
    non-complying response the FSO may **close** (bank ``U(a_ℓ, s)``) or
    **escalate** directly to any higher rung's subgame (the statutory
    floor permits skipping — e.g. repeat offences jump straight to
    prosecution). Escalating keeps the costs already sunk (``cost`` +
    ``response_cost``) and discards the partial deterrence of the
    abandoned rung — the higher act re-achieves the statutory outcome on
    its own — so the continuation value is the best higher subgame:

        escalate(ℓ, s) = δ · max_{k>ℓ} V(k) − cost(ℓ) − response_cost(ℓ, s)

    δ is the repeated-game continuation discount (see above). Each stage
    row holds ``W(a_ℓ, s) = max(U(a_ℓ, s), escalate)`` — the FBO's best
    response at ``a_ℓ`` is ``argmin_s W`` *anticipating* escalation, and
    ``V(ℓ) = min_s W`` is the subgame value the selector scores. Ties
    resolve to the earliest strategy in declaration order.

    All tables are injectable so tests can exercise the option mechanics
    with synthetic payoffs; defaults are the shipped seam.
    """
    retention = retention if retention is not None else RETENTION
    response_cost = response_cost if response_cost is not None else FSO_RESPONSE_COST
    profiles = profiles if profiles is not None else ACTION_PROFILES

    rows: dict[EscalationLevel, dict[FboStrategy, float]] = {}
    best_future: float | None = None  # max_{k>ℓ} V(k); None at the absorbing top rung
    for level in sorted(profiles, reverse=True):
        profile = profiles[level]
        row: dict[FboStrategy, float] = {
            strategy: (
                retention[level][strategy] * profile.deterrence_power
                - profile.fso_cost
                - response_cost[level][strategy]
            )
            for strategy in FboStrategy
        }
        if best_future is not None:
            for strategy in (FboStrategy.CONTEST, FboStrategy.DEFECT):
                escalate = discount * best_future - profile.fso_cost - response_cost[level][strategy]
                if escalate > row[strategy]:
                    row[strategy] = escalate
        rows[level] = row
        subgame_value = min(row.values())
        best_future = subgame_value if best_future is None else max(best_future, subgame_value)
    return rows


def escalation_subgame_values(
    *,
    retention: dict[EscalationLevel, dict[FboStrategy, float]] | None = None,
    response_cost: dict[EscalationLevel, dict[FboStrategy, float]] | None = None,
    profiles: dict[EscalationLevel, ActionProfile] | None = None,
    discount: float = DELTA_FIRST_TIME,
) -> dict[EscalationLevel, float]:
    """Per-rung subgame value ``V(ℓ) = min_s W(a_ℓ, s)`` (see above)."""
    return {
        level: min(row.values())
        for level, row in sequential_stage_values(
            retention=retention,
            response_cost=response_cost,
            profiles=profiles,
            discount=discount,
        ).items()
    }
