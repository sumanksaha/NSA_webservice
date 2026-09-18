"""Tests for derive_actions — corrective-action derivation for Improvement Notices.

Contract: one corrective directive per violation, same order, empty in → empty out.
Directives come from REMEDIATION_ACTIONS keyed by the violation's checklist field.
"""

from __future__ import annotations

from app.shared.context_derivers import derive_actions, derive_violations

UNCLEAN_OBSERVATION = (
    "The food premises, including floors, walls, ceilings and food-contact surfaces, "
    "were not maintained in a clean and hygienic condition at the time of inspection."
)
UNCLEAN_DIRECTIVE = (
    "Maintain the entire food premises, including all food-contact surfaces, "
    "in a clean and hygienic condition at all times."
)


class TestDeriveActions:
    def test_empty_violations_give_no_actions(self):
        assert derive_actions([]) == []

    def test_one_action_per_violation_with_exact_phrasing(self):
        violations = [
            {
                "title": "Unclean Premises",
                "observation": UNCLEAN_OBSERVATION,
                "field": "clean_premise",
            }
        ]
        assert derive_actions(violations) == [UNCLEAN_DIRECTIVE]

    def test_unknown_field_falls_back_to_generic_directive(self):
        violations = [{"title": "Unclean Premises", "observation": UNCLEAN_OBSERVATION}]
        assert derive_actions(violations) == [
            "Take immediate corrective action to rectify: Unclean Premises.",
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
        assert actions[0] == UNCLEAN_DIRECTIVE


class TestExpiredItemPolarity:
    """Expired_item means 'expired items present' — only 'yes' violates."""

    def test_no_means_compliant(self):
        assert derive_violations({"Expired_item": "no"}) == []

    def test_yes_means_violation_with_directive(self):
        violations = derive_violations({"Expired_item": "yes"})
        assert [v["title"] for v in violations] == ["Expired Items Present"]
        assert derive_actions(violations) == [
            "Remove all expired food articles from the premises immediately and "
            "institute first-expiry-first-out stock rotation."
        ]
