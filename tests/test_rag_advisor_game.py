"""Game-mechanics tests for the FSO advisor (ADR-0006).

The zero-sum payoff model in ``app/rag/advisor/game.py`` must (a) *compute*
— not assume — the FBO best response, (b) keep the statutory floor as a
hard admissibility constraint, and (c) select the floor act as the
robust-score argmax so the blueprint §6 spec Acts keep holding. Expected
values here are hand-derived from the documented payoff tables, not
recomputed from the code path under test.
"""

from __future__ import annotations

import pytest

from app.rag.advisor.game import (
    FSO_RESPONSE_COST,
    RETENTION,
    FboStrategy,
    fbo_best_response,
    fso_payoff,
    fso_payoff_matrix,
)
from app.rag.advisor.ladder import ACTION_PROFILES, EscalationLevel
from app.rag.advisor.selector import DeterministicActSelector

#: Hand-derived maximin values: v(a) = min_s U(a, s) with the shipped tables.
EXPECTED_MAXIMIN: dict[EscalationLevel, float] = {
    EscalationLevel.INSPECT_WARN: -1.4,  # 0.1·1 − 1 − 0.5
    EscalationLevel.SAMPLE_LAB_TEST: -1.1,  # 0.6·4 − 3 − 0.5
    EscalationLevel.IMPROVEMENT_NOTICE: -1.7,  # 0.3·6 − 2 − 1.5
    EscalationLevel.PENALTY_DIRECTION: -1.7,  # 0.6·8 − 4 − 2.5
    EscalationLevel.PROSECUTION: -5.0,  # 0.8·10 − 10 − 3 (contested trial)
}

#: Hand-derived FBO best responses (argmin of the U row, zero-sum).
EXPECTED_BEST_RESPONSES: dict[EscalationLevel, FboStrategy] = {
    EscalationLevel.INSPECT_WARN: FboStrategy.DEFECT,
    EscalationLevel.SAMPLE_LAB_TEST: FboStrategy.DEFECT,
    EscalationLevel.IMPROVEMENT_NOTICE: FboStrategy.DEFECT,
    EscalationLevel.PENALTY_DIRECTION: FboStrategy.DEFECT,
    EscalationLevel.PROSECUTION: FboStrategy.CONTEST,  # defiance is pointless vs a cognizable act
}


# ---------------------------------------------------------------------------
# Payoff model integrity
# ---------------------------------------------------------------------------


def test_payoff_matrix_is_complete_and_bounded():
    matrix = fso_payoff_matrix()
    assert set(matrix) == set(ACTION_PROFILES)
    for level, row in matrix.items():
        assert set(row) == set(FboStrategy)
        for strategy in FboStrategy:
            assert 0.0 <= RETENTION[level][strategy] <= 1.0
            assert FSO_RESPONSE_COST[level][strategy] >= 0.0


def test_comply_is_full_effect_at_no_extra_cost():
    for level in EscalationLevel:
        assert RETENTION[level][FboStrategy.COMPLY] == 1.0
        assert FSO_RESPONSE_COST[level][FboStrategy.COMPLY] == 0.0


def test_fso_payoff_matches_documented_formula():
    profile = ACTION_PROFILES[EscalationLevel.SAMPLE_LAB_TEST]
    for strategy in FboStrategy:
        expected = (
            RETENTION[profile.level][strategy] * profile.deterrence_power
            - profile.fso_cost
            - FSO_RESPONSE_COST[profile.level][strategy]
        )
        assert fso_payoff(profile, strategy) == pytest.approx(expected)


def test_zero_sum_best_response_is_row_argmin():
    for profile in ACTION_PROFILES.values():
        strategy, value = fbo_best_response(profile)
        row = {s: fso_payoff(profile, s) for s in FboStrategy}
        assert value == pytest.approx(min(row.values()))
        assert row[strategy] == pytest.approx(min(row.values()))


def test_best_responses_are_computed_not_assumed():
    """The argmin varies across acts — the game is genuinely solved, not a
    hardcoded 'FBO always defects' assumption."""
    responses = {level: fbo_best_response(profile)[0] for level, profile in ACTION_PROFILES.items()}
    assert len(set(responses.values())) > 1
    for level, strategy in EXPECTED_BEST_RESPONSES.items():
        assert responses[level] is strategy, level


def test_maximin_values_match_hand_derived_table():
    for level, expected in EXPECTED_MAXIMIN.items():
        _, value = fbo_best_response(ACTION_PROFILES[level])
        assert value == pytest.approx(expected), level


# ---------------------------------------------------------------------------
# Floor = admissibility constraint; robust score = selection objective
# ---------------------------------------------------------------------------


def _reachable_floors():
    return [
        EscalationLevel.SAMPLE_LAB_TEST,
        EscalationLevel.IMPROVEMENT_NOTICE,
        EscalationLevel.PENALTY_DIRECTION,
        EscalationLevel.PROSECUTION,
    ]


def _robust_score(selector, level: EscalationLevel) -> float:
    _, maximin = fbo_best_response(ACTION_PROFILES[level])
    return round(round(maximin, 6) + selector.omega_optionality * selector.optionality_score(ACTION_PROFILES[level]), 9)


def test_floor_act_maximizes_robust_score():
    """Generalized ADR-0006 invariant: for every reachable statutory floor,
    the floor act is the robust-score argmax among admissible acts (ties →
    least escalatory). Fails loudly if payoff retuning breaks the ordering
    the blueprint §6 spec Acts depend on."""
    selector = DeterministicActSelector()
    for floor in _reachable_floors():
        admissible = [lvl for lvl in sorted(EscalationLevel) if lvl >= floor]
        best = admissible[0]
        for level in admissible[1:]:
            if _robust_score(selector, level) > _robust_score(selector, best):
                best = level
        assert best is floor, floor


def test_floor_is_a_constraint_not_the_answer():
    """§51 without a lab report: four acts are admissible, the floor only
    bounds them from below, and the winner is flagged inside the payload."""
    out = DeterministicActSelector().select_act(retrieved_sections=["51"], lab_report_available=False)
    act = out["fso_act"]
    assert act["binding_constraint"] == {
        "type": "statutory_floor",
        "level": "SAMPLE_LAB_TEST",
        "reason": "lab_evidence_required",
    }
    admissible = act["admissible_acts"]
    assert [a["escalation_level"] for a in admissible] == [
        "SAMPLE_LAB_TEST",
        "IMPROVEMENT_NOTICE",
        "PENALTY_DIRECTION",
        "PROSECUTION",
    ]
    selected = [a for a in admissible if a["selected"]]
    assert len(selected) == 1
    assert selected[0]["escalation_level"] == "SAMPLE_LAB_TEST"
    assert act["escalation_level"] == "SAMPLE_LAB_TEST"


def test_prosecution_floor_binds_despite_worst_robust_score():
    """§63: the law leaves no alternative — the floor decides even though
    prosecution has the worst robust score on the ladder."""
    out = DeterministicActSelector().select_act(retrieved_sections=["63"])
    act = out["fso_act"]
    assert act["escalation_level"] == "PROSECUTION"
    assert act["binding_constraint"]["reason"] == "cognizable_offence_or_repeat_violation"
    assert [a["escalation_level"] for a in act["admissible_acts"]] == ["PROSECUTION"]
    assert act["maximin_value"] == pytest.approx(-5.0)
    assert act["fbo_best_response"] == "CONTEST"
    assert "the floor decides" in act["game_theory_basis"]


# ---------------------------------------------------------------------------
# Payload: computed facts, additive to the ADR-0003 contract
# ---------------------------------------------------------------------------


def test_payload_reports_computed_game_facts():
    out = DeterministicActSelector().select_act(retrieved_sections=["51"], lab_report_available=False)
    act = out["fso_act"]
    assert act["fbo_best_response"] == "DEFECT"
    assert act["maximin_value"] == pytest.approx(-1.1)
    # Legacy ADR-0003 fields intact.
    for key in ("action", "escalation_level", "statutory_anchor", "game_theory_basis", "talebian_basis", "confidence", "citations"):
        assert key in act


def test_margin_is_best_minus_runner_up():
    out = DeterministicActSelector().select_act(retrieved_sections=["51"], lab_report_available=False)
    act = out["fso_act"]
    scores = [a["robust_score"] for a in act["admissible_acts"]]
    best = max(scores)
    runner_up = max(s for s in scores if s != best)
    assert best - runner_up == pytest.approx(1.38)  # 0.6 − (−0.78)
    assert f"{1.38:+.2f}" in act["game_theory_basis"]


def test_admissible_acts_sorted_least_to_most_escalatory():
    out = DeterministicActSelector().select_act(retrieved_sections=["55"], lab_report_available=False)
    levels = [a["escalation_level"] for a in out["fso_act"]["admissible_acts"]]
    assert levels == sorted(levels, key=lambda name: EscalationLevel[name])


def test_game_theory_basis_renders_computed_facts_and_fits_ui():
    for sections, lab in ((["51"], False), (["63"], False), (["32"], False)):
        basis = DeterministicActSelector().select_act(retrieved_sections=sections, lab_report_available=lab)[
            "fso_act"
        ]["game_theory_basis"]
        assert "Zero-sum minimax vs FBO" in basis
        assert "statutory floor" in basis
        assert len(basis) <= 300  # UI truncates at 300 chars


# ---------------------------------------------------------------------------
# Stability of the reframe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("omega", [0.0, 0.5, 2.0, 10.0])
def test_selection_stable_across_omega(omega: float):
    """Robust-value and optionality orderings align below the floor, so the
    spec Acts hold for any ω ≥ 0 (the v-tie between NOTICE and PENALTY
    resolves to the least-escalatory act)."""
    selector = DeterministicActSelector(omega_optionality=omega)
    assert selector.select_act(retrieved_sections=["51"])["fso_act"]["escalation_level"] == "SAMPLE_LAB_TEST"
    assert selector.select_act(retrieved_sections=["32"])["fso_act"]["escalation_level"] == "IMPROVEMENT_NOTICE"
    assert selector.select_act(retrieved_sections=["56"])["fso_act"]["escalation_level"] == "PENALTY_DIRECTION"
    assert selector.select_act(retrieved_sections=["63"])["fso_act"]["escalation_level"] == "PROSECUTION"


def test_confidence_untouched_by_reframe():
    selector = DeterministicActSelector()
    assert selector.select_act(retrieved_sections=["51"])["fso_act"]["confidence"] == 0.7
    assert selector.select_act(retrieved_sections=["51", "52", "55"], lab_report_available=True)["fso_act"][
        "confidence"
    ] == 1.0


def test_abstain_path_unchanged():
    out = DeterministicActSelector().select_act(retrieved_sections=[])
    assert out["fso_act"] is None
    assert out["abstain_reason"] == "insufficient_statutory_grounding"
