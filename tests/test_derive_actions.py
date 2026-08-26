"""Tests for derive_actions — corrective-action derivation for Improvement Notices.

Contract: one corrective action per violation, same order, empty in → empty out.
Action phrasing is templated from the violation's title + observation.
"""

from __future__ import annotations

from app.shared.context_derivers import derive_actions, derive_violations

UNCLEAN_OBSERVATION = "The premises were found inadequately maintained and unhygienic."


class TestDeriveActions:
    def test_empty_violations_give_no_actions(self):
        assert derive_actions([]) == []

    def test_one_action_per_violation_with_exact_phrasing(self):
        violations = [
            {
                "title": "Unclean Premises",
                "observation": UNCLEAN_OBSERVATION,
            }
        ]
        assert derive_actions(violations) == [
            "Take corrective action: Unclean Premises — " + UNCLEAN_OBSERVATION,
        ]

    def test_order_preserved_across_multiple_violations(self):
        violations = [
            {"title": "Unclean Premises", "observation": UNCLEAN_OBSERVATION},
            {"title": "Expired Items Present", "observation": "Expired food items were found on the premises."},
        ]
        actions = derive_actions(violations)
        assert len(actions) == 2
        assert "Unclean Premises" in actions[0]
        assert "Expired Items Present" in actions[1]

    def test_actions_derived_from_real_checklist_violations(self):
        """Tracer bullet: checklist dict → derive_violations → derive_actions."""
        checklist = {
            "clean_premise": "no",
            "artificial_colour": "yes",
            # everything else compliant
        }
        violations = derive_violations(checklist)
        assert len(violations) == 2  # clean_premise flagged "no", artificial_colour flagged "yes"
        actions = derive_actions(violations)
        assert len(actions) == 2
