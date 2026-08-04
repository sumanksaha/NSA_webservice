"""Tests for S6a/S6b wiring: app.utils.suggester driven by canonical sections_data.

Covers:
- ``suggest_sections`` output is always a subset of the canonical
  ``VALID_SECTION_IDS`` whitelist (single source of truth).
- Rule behavior: non-license -> 63 only; hygiene -> 56; direction
  non-compliance -> 55; manual-only sections (58, 64) never auto-suggested.
- The manual-only set invariant and statutory text loading from
  ``fss_sections.md``.
"""

from app.utils.sections_data import SECTIONS, VALID_SECTION_IDS
from app.utils.suggester import (
    _MANUAL_ONLY_SECTIONS,
    section_title,
    suggest_sections,
)


def _checklist(**overrides):
    """A compliant default checklist; pass values to trigger rules.

    NOTE: ``Expired_item`` defaults to ``"yes"`` here because the rule engine
    treats ``Expired_item == "no"`` as a Section 55 direction-compliance
    violation (pre-existing suggester semantics); the real form default is
    ``"no"`` but that alone would trigger Rule 2.
    """
    defaults = {
        "non_license": "no",
        "clean_premise": "yes",
        "refrigerator_clean": "yes",
        "proper_attire": "yes",
        "proper_covered_utensil": "yes",
        "food_segregation": "yes",
        "veg_nonveg_separation": "yes",
        "date_tag": "yes",
        "license_display": "yes",
        "Expired_item": "yes",
        "Pest_report": "yes",
        "Water_report": "yes",
        "artificial_colour": "no",
    }
    defaults.update(overrides)
    return defaults


def test_suggestions_subset_of_valid_ids():
    """Every suggested section must be a member of the canonical whitelist."""
    cases = [
        _checklist(),
        _checklist(non_license="yes"),
        _checklist(clean_premise="no"),
        _checklist(refrigerator_clean="no", proper_attire="no", food_segregation="no"),
        _checklist(artificial_colour="yes", clean_premise="no"),
    ]
    for form_data in cases:
        result = suggest_sections(form_data)
        for section_id in result["sections"]:
            assert section_id in VALID_SECTION_IDS, (
                f"suggested section {section_id} not in VALID_SECTION_IDS"
            )


def test_non_license_returns_63_only():
    result = suggest_sections(_checklist(non_license="yes"))
    assert result["sections"] == ["63"]
    assert "63" in result["reasoning"]


def test_hygiene_violation_returns_56():
    result = suggest_sections(_checklist(clean_premise="no"))
    assert "56" in result["sections"]
    assert "56" in result["reasoning"]


def test_direction_noncompliance_returns_55():
    result = suggest_sections(_checklist(artificial_colour="yes"))
    assert "55" in result["sections"]
    assert "55" in result["reasoning"]


def test_manual_only_never_auto_suggested():
    # Even with every hygiene/direction failure ticked, 58/64 must never appear.
    result = suggest_sections(_checklist(clean_premise="no", proper_attire="no"))
    assert "58" not in result["sections"]
    assert "64" not in result["sections"]


def test_clean_checklist_suggests_nothing():
    result = suggest_sections(_checklist())
    assert result["sections"] == []
    assert result["reasoning"] == {}


def test_manual_only_subset_invariant():
    assert _MANUAL_ONLY_SECTIONS <= VALID_SECTION_IDS


def test_sections_loads_statute_text():
    for section_id in ("55", "56", "58", "63", "64"):
        assert section_id in SECTIONS
        assert SECTIONS[section_id].startswith(f"# Section {section_id}")


def test_section_title_helper():
    assert section_title("58") == "Penalty for contraventions for which no specific penalty is provided"
    assert section_title("63") == "Punishment for carrying out a business without licence"
    assert section_title("999") is None
