"""Sequential escalation-game tests (ADR-0007).

The extensive-form subgame in ``app/rag/advisor/game.py`` must (a) collapse
to the simultaneous game when closing dominates, (b) genuinely propagate
continuation value down the tree when escalation is profitable, and (c)
keep the blueprint §6 spec Acts holding for both discount values. All
expected values are hand-derived from the documented tables, not read off
the code under test.

Shipped-table derivation (δ = 1): escalate(ℓ, s) = max_{k>ℓ}V(k) − cost − resp_cost
exercises iff max_{k>ℓ}V(k) > retention·deterrence. Chain: V(5) = −5.0 → never
exceeds any rung's realized deterrence → W ≡ U everywhere, so the subgame
values equal the simultaneous maximin values.
"""

from __future__ import annotations

import pytest

from app.rag.advisor.game import (
    DELTA_FIRST_TIME,
    DELTA_REPEAT,
    FSO_RESPONSE_COST,
    FboStrategy,
    RETENTION,
    ActionProfile,
    continuation_discount,
    escalation_subgame_values,
    fbo_best_response,
    sequential_stage_values,
)
from app.rag.advisor.ladder import ACTION_PROFILES, EscalationLevel
from app.rag.advisor.selector import DeterministicActSelector

#: Hand-derived simultaneous rows U(a, s) from the shipped tables
#: (e.g. U(sample, CONTEST) = 0.9·4 − 3 − 1 = −0.4).
U: dict[EscalationLevel, dict[FboStrategy, float]] = {
    EscalationLevel.INSPECT_WARN: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: -0.9, FboStrategy.DEFECT: -1.4},
    EscalationLevel.SAMPLE_LAB_TEST: {FboStrategy.COMPLY: 1.0, FboStrategy.CONTEST: -0.4, FboStrategy.DEFECT: -1.1},
    EscalationLevel.IMPROVEMENT_NOTICE: {FboStrategy.COMPLY: 4.0, FboStrategy.CONTEST: 0.6, FboStrategy.DEFECT: -1.7},
    EscalationLevel.PENALTY_DIRECTION: {FboStrategy.COMPLY: 4.0, FboStrategy.CONTEST: -0.4, FboStrategy.DEFECT: -1.7},
    EscalationLevel.PROSECUTION: {FboStrategy.COMPLY: 0.0, FboStrategy.CONTEST: -5.0, FboStrategy.DEFECT: -1.0},
}


def _close(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


# ---------------------------------------------------------------------------
# Backward induction mechanics
# ---------------------------------------------------------------------------


def test_top_rung_is_absorbing():
    """Prosecution has no next rung: W = U there, no escalation branch."""
    rows = sequential_stage_values()
    assert rows[EscalationLevel.PROSECUTION] == U[EscalationLevel.PROSECUTION]


def test_zero_discount_collapses_to_simultaneous_game():
    """δ = 0 makes escalation worthless (0·V − sunk costs ≤ any close), so
    every cell must equal the simultaneous payoff."""
    rows = sequential_stage_values(discount=0.0)
    for level in EscalationLevel:
        for strategy in FboStrategy:
            assert _close(rows[level][strategy], U[level][strategy]), (level, strategy)


def test_shipped_tables_never_exercise_the_option():
    """Under the shipped payoffs prosecution's value is dominated, so
    closing always beats escalating: W ≡ U at δ = 1 for every cell."""
    rows = sequential_stage_values(discount=DELTA_FIRST_TIME)
    for level in EscalationLevel:
        for strategy in FboStrategy:
            assert _close(rows[level][strategy], U[level][strategy]), (level, strategy)


def test_subgame_values_match_hand_derived_chain():
    """V(5) = −5.0; V(4) = max(−1.7, −5.0−4−2.5) = −1.7; V(3) = max(−1.7,
    −1.7−2−1.5) = −1.7; V(2) = max(−1.1, −1.7−3−0.5) = −1.1; V(1) =
    max(−1.4, −1.1−1−0.5) = −1.4."""
    values = escalation_subgame_values(discount=DELTA_FIRST_TIME)
    expected = {
        EscalationLevel.PROSECUTION: -5.0,
        EscalationLevel.PENALTY_DIRECTION: -1.7,
        EscalationLevel.IMPROVEMENT_NOTICE: -1.7,
        EscalationLevel.SAMPLE_LAB_TEST: -1.1,
        EscalationLevel.INSPECT_WARN: -1.4,
    }
    for level, value in expected.items():
        assert _close(values[level], value), level


def test_defect_beats_contest_in_every_stage_row():
    """DEFECT burdens the FSO more than CONTEST at every rung (follow-on
    enforcement dominates litigation), so the anticipated best response is
    DEFECT everywhere except the absorbing top rung, where CONTEST is worst."""
    rows = sequential_stage_values()
    for level in EscalationLevel:
        worst = min(rows[level], key=lambda s: rows[level][s])
        expected = FboStrategy.CONTEST if level is EscalationLevel.PROSECUTION else FboStrategy.DEFECT
        assert worst is expected, level


def test_repeat_discount_deflates_continuation():
    """With δ = 0.3, escalating from warn after defiance is worth
    0.3·(−1.1) − 1.5 = −1.83 < −1.4, so the FSO closes: the escalation
    threat is no longer credible against a convicted FBO."""
    rows = sequential_stage_values(discount=DELTA_REPEAT)
    warn_defect = rows[EscalationLevel.INSPECT_WARN][FboStrategy.DEFECT]
    assert _close(warn_defect, U[EscalationLevel.INSPECT_WARN][FboStrategy.DEFECT])


def test_continuation_discount_mapping():
    assert continuation_discount(False) == DELTA_FIRST_TIME == 1.0
    assert continuation_discount(True) == DELTA_REPEAT == 0.3


def test_synthetic_tables_exercise_the_option_mechanics():
    """Injectable tables: a cheap top rung (cost 2, deterrence 10 →
    V(top) = min(8, 8−3, 10−1) = 3.0) makes escalating from warn after
    defiance worth 1·3.0 − 1 − 0.5 = 1.5 ≫ closing at −1.4 — and the FBO,
    anticipating that, flips its warn best response to COMPLY."""
    profiles = dict(ACTION_PROFILES)
    top = ACTION_PROFILES[EscalationLevel.PROSECUTION]
    profiles[EscalationLevel.PROSECUTION] = ActionProfile(
        level=top.level,
        action_name=top.action_name,
        reversibility=top.reversibility,
        information_yield=top.information_yield,
        fso_cost=2.0,
        deterrence_power=10.0,
    )
    rows = sequential_stage_values(
        retention={lvl: dict(row) for lvl, row in RETENTION.items()},
        response_cost={lvl: dict(row) for lvl, row in FSO_RESPONSE_COST.items()},
        profiles=profiles,
        discount=DELTA_FIRST_TIME,
    )
    warn = rows[EscalationLevel.INSPECT_WARN]
    assert _close(warn[FboStrategy.DEFECT], 1.5)  # escalates into the valuable subgame
    assert _close(warn[FboStrategy.CONTEST], 1.5)  # same after contest
    assert min(warn, key=warn.get) is FboStrategy.COMPLY  # anticipation flips the response  # type: ignore[type-var]


# ---------------------------------------------------------------------------
# Selector integration (spec Acts must keep holding)
# ---------------------------------------------------------------------------


def _robust_score(selector: DeterministicActSelector, level: EscalationLevel, discount: float) -> float:
    row = sequential_stage_values(discount=discount)[level]
    value = min(row.values())
    return round(round(value, 6) + selector.omega_optionality * selector.optionality_score(ACTION_PROFILES[level]), 9)


def test_floor_act_maximizes_robust_score_both_discounts():
    """Generalized ADR-0006 invariant over subgame values: for every
    reachable floor and both discount values, the floor act is the
    robust-score argmax among admissible acts (ties → least escalatory)."""
    selector = DeterministicActSelector()
    for discount in (DELTA_FIRST_TIME, DELTA_REPEAT):
        for floor in (
            EscalationLevel.SAMPLE_LAB_TEST,
            EscalationLevel.IMPROVEMENT_NOTICE,
            EscalationLevel.PENALTY_DIRECTION,
            EscalationLevel.PROSECUTION,
        ):
            admissible = [lvl for lvl in sorted(EscalationLevel) if lvl >= floor]
            best = admissible[0]
            for level in admissible[1:]:
                if _robust_score(selector, level, discount) > _robust_score(selector, best, discount):
                    best = level
            assert best is floor, (floor, discount)


def test_selector_reports_sequential_payload():
    out = DeterministicActSelector().select_act(retrieved_sections=["51"], lab_report_available=False)
    act = out["fso_act"]
    assert act["escalation_level"] == "SAMPLE_LAB_TEST"
    assert act["continuation_discount"] == 1.0
    assert act["fbo_best_response"] == "DEFECT"
    assert act["maximin_value"] == pytest.approx(-1.1)


def test_repeat_offender_reports_deflated_discount():
    out = DeterministicActSelector().select_act(retrieved_sections=["51"], has_prior_violations=True)
    act = out["fso_act"]
    assert act["continuation_discount"] == 0.3
    assert act["escalation_level"] == "PROSECUTION"  # statutory floor, unchanged


def test_spec_acts_hold_for_both_offender_types():
    selector = DeterministicActSelector()
    assert (
        selector.select_act(retrieved_sections=["51"], lab_report_available=False)["fso_act"]["escalation_level"]
        == "SAMPLE_LAB_TEST"
    )
    assert selector.select_act(retrieved_sections=["32"])["fso_act"]["escalation_level"] == "IMPROVEMENT_NOTICE"
    assert selector.select_act(retrieved_sections=["56"])["fso_act"]["escalation_level"] == "PENALTY_DIRECTION"
    assert selector.select_act(retrieved_sections=["63"])["fso_act"]["escalation_level"] == "PROSECUTION"
    assert selector.select_act(retrieved_sections=["51"], has_prior_violations=True)["fso_act"]["escalation_level"] == "PROSECUTION"


def test_simultaneous_helper_still_matches_hand_derived_table():
    """The static-game helper remains the δ=0 close-value reference."""
    for level in EscalationLevel:
        strategy, value = fbo_best_response(ACTION_PROFILES[level])
        assert value == pytest.approx(min(U[level].values()))
        assert U[level][strategy] == pytest.approx(min(U[level].values()))
