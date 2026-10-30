"""Canonical inspection/adjudication checklist vocabulary (Candidate 1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ChecklistField:
    key: str
    title: str
    observation: str
    remediation: str
    inverted: bool = False
    photo_observable: bool = True


CHECKLIST: Tuple[ChecklistField, ...] = (
ChecklistField(
        key="clean_premise",
        title="Unclean Premises",
        observation=(
            "The food premises, including floors, walls, ceilings and food-contact surfaces, "
            "were not maintained in a clean and hygienic condition at the time of inspection."
        ),
        remediation=(
            "Maintain the entire food premises, including all food-contact surfaces, "
            "in a clean and hygienic condition at all times."
        ),
    ),
    ChecklistField(
        key="refrigerator_clean",
        title="Improper Refrigerator Maintenance",
        observation=(
            "The refrigeration facilities were not maintained in a clean condition and in proper "
            "working order, risking contamination of the food stored therein."
        ),
        remediation="Clean and sanitise all refrigeration facilities and keep them in proper working condition.",
    ),
    ChecklistField(
        key="proper_attire",
        title="Improper Protective Attire",
        observation=(
            "Food handlers on duty were not wearing clean protective clothing, headgear and "
            "footwear as required for the hygienic handling of food."
        ),
        remediation="Ensure all food handlers wear clean protective attire, including headgear and footwear, while on duty.",
    ),
    ChecklistField(
        key="proper_covered_utensil",
        title="Improper Covering of Food",
        observation="Food articles and utensils were found uncovered and exposed to dust, pests and other contaminants.",
        remediation="Keep all food articles and utensils covered and protected from dust, pests and other contaminants at all times.",
    ),
    ChecklistField(
        key="date_tag",
        title="Absence of Date Tagging",
        observation=(
            "Stored food articles did not bear legible date tags or traceability markings, "
            "so batch identity and stock rotation could not be established."
        ),
        remediation="Affix legible date tags to all stored food articles and maintain batch-wise traceability and stock-rotation records.",
    ),
    ChecklistField(
        key="veg_nonveg_separation",
        title="Improper Veg/Non-Veg Separation",
        observation="Vegetarian and non-vegetarian food articles were not stored, handled and displayed separately from each other.",
        remediation="Store, handle and display vegetarian and non-vegetarian food articles strictly separately from each other.",
    ),
    ChecklistField(
        key="food_segregation",
        title="Improper Food Segregation",
        observation=(
            "Raw, cooked and ready-to-eat food articles were not segregated at each stage "
            "of handling, creating a risk of cross-contamination."
        ),
        remediation="Segregate raw, cooked and ready-to-eat food articles at every stage of handling so as to eliminate cross-contamination.",
    ),
    ChecklistField(
        key="license_display",
        title="Improper License Display",
        observation="The FSSAI license/registration was not displayed at a prominent place in the food business premises.",
        remediation="Display the FSSAI license/registration prominently at the entry of the food business premises.",
    ),
    ChecklistField(
        key="artificial_colour",
        title="Use of Artificial Colours",
        observation="Artificial colours were reportedly used in the preparation of food articles.",
        remediation=(
            "Discontinue the use of artificial colours except as permitted under the "
            "Food Safety and Standards (Food Products Standards and Food Additives) "
            "Regulations, and declare permitted colours on the label."
        ),
        inverted=True,
    ),
    ChecklistField(
        key="Expired_item",
        title="Expired Items Present",
        observation="Food articles past their expiry/best-before date were found stored on the premises.",
        remediation="Remove all expired food articles from the premises immediately and institute first-expiry-first-out stock rotation.",
        inverted=True,
    ),
    ChecklistField(
        key="Pest_report",
        title="Pest Control Report Missing",
        observation=(
            "No valid pest-control record was produced at the time of inspection; routine "
            "pest management of the premises is undocumented."
        ),
        remediation="Engage licensed pest control, eliminate pest harbourage in the premises, and preserve pest-management records for verification.",
        photo_observable=False,
    ),
    ChecklistField(
        key="Water_report",
        title="Water Test Report Missing",
        observation="No potable-water test report was produced; the safety of water used in food preparation and cleaning is unverified.",
        remediation="Use only potable water in food operations and preserve periodic water-test reports for verification.",
        photo_observable=False,
    ),
)


def by_key() -> dict[str, "ChecklistField"]:
    return {f.key: f for f in CHECKLIST}


CHECKLIST_FIELDS: tuple[str, ...] = tuple(f.key for f in CHECKLIST)

CHECKLIST_RULES: dict[str, tuple[str, str]] = {
    f.key: (f.title, f.observation) for f in CHECKLIST if not f.inverted
}

SPECIAL_VIOLATION_RULES: dict[str, tuple[str, str]] = {
    f.key: (f.title, f.observation) for f in CHECKLIST if f.inverted
}

REMEDIATION_ACTIONS: dict[str, str] = {f.key: f.remediation for f in CHECKLIST}

PHOTO_UNOBSERVABLE_FIELDS: frozenset[str] = frozenset(
    f.key for f in CHECKLIST if not f.photo_observable
)

__all__ = [
    "CHECKLIST",
    "CHECKLIST_FIELDS",
    "CHECKLIST_RULES",
    "ChecklistField",
    "PHOTO_UNOBSERVABLE_FIELDS",
    "REMEDIATION_ACTIONS",
    "SPECIAL_VIOLATION_RULES",
    "by_key",
]

