import logging
from .sections_data import SECTIONS, VALID_SECTION_IDS

logger = logging.getLogger(__name__)

# Fields that are case metadata, not checklist items
_NON_CHECKLIST_FIELDS = {
    "food_safety_officer", "case_number", "fbo_owner", "fbo_name",
    "fbo_address", "fssai_license", "concerned_food", "complaint_lodged",
    "problem", "non_license", "pre_authorization",
    "First_inspection_date", "compliance_deadline",
    "Complaint_date", "inspection_date", "authorization_date",
}

# Checklist items indicating unhygienic/unsanitary conditions -> Sec 56
_HYGIENE_CHECKLIST_ITEMS = {
    "clean_premise": "Premises found inadequately maintained and unhygienic.",
    "refrigerator_clean": "Refrigeration facilities found unclean.",
    "proper_attire": "Food handlers lacked prescribed protective attire.",
    "proper_covered_utensil": "Food and utensils were left uncovered.",
    "food_segregation": "Improper food segregation — risk of cross-contamination.",
    "veg_nonveg_separation": "Veg/non-veg segregation not maintained.",
}

# Sections the officer must tick manually
_MANUAL_ONLY_SECTIONS = {"58", "64"}

# Checklist items indicating failure to comply with FSO directions -> Sec 55
_DIRECTION_COMPLIANCE_ITEMS = {
    "clean_premise": "Premises not maintained per prior directions.",
    "refrigerator_clean": "Refrigeration hygiene directions not followed.",
    "proper_attire": "Protective-attire directions not followed.",
    "proper_covered_utensil": "Food-covering directions not followed.",
    "date_tag": "Date-tagging/traceability directions not followed.",
    "veg_nonveg_separation": "Veg/non-veg segregation directions not followed.",
    "food_segregation": "Food segregation directions not followed.",
    "license_display": "Licence-display directions not followed.",
    "Expired_item": "Directions on removing expired stock not followed.",
    "Pest_report": "Pest-control documentation directions not followed.",
    "Water_report": "Water-test documentation directions not followed.",
}


def _is_non_license(form_data: dict) -> bool:
    return str(form_data.get("non_license", "no")).strip().lower() == "yes"


def _detect_section_56_from_checklist(form_data: dict) -> tuple[bool, str]:
    violations = [
        desc for field, desc in _HYGIENE_CHECKLIST_ITEMS.items()
        if form_data.get(field) == "no"
    ]
    if not violations:
        return False, ""
    summary = "; ".join(violations[:2])
    if len(violations) > 2:
        summary += f"; and {len(violations) - 2} more hygiene issue(s)"
    return True, f"Checklist shows unhygienic/unsanitary conditions: {summary}."


def _detect_section_55_from_checklist(form_data: dict) -> tuple[bool, str]:
    violations = [
        desc for field, desc in _DIRECTION_COMPLIANCE_ITEMS.items()
        if form_data.get(field) == "no"
    ]
    if form_data.get("artificial_colour") == "yes":
        violations.append("Artificial colours used despite standing directions.")
    if not violations:
        return False, ""
    summary = "; ".join(violations[:2])
    if len(violations) > 2:
        summary += f"; and {len(violations) - 2} more compliance lapse(s)"
    return True, f"Checklist shows failure to comply with prior FSO directions: {summary}."


def suggest_sections(form_data: dict) -> dict:
    """
    Rule-based section suggestion.
    Rules:
      1. Non-licensed FBO -> Section 63 only.
      2. Licensed FBO -> Section 55 if checklist shows direction non-compliance.
      3. Section 56 if hygiene violations detected in checklist.
      4. Sections 58 and 64 are manual-only (officer ticks in UI).
    """
    sections = []
    reasoning = {}

    # Rule 1: non-licensed -> Sec 63 only
    if _is_non_license(form_data):
        return {
            "sections": ["63"],
            "reasoning": {
                "63": "FBO is non-licensed/unregistered — Section 63 applies exclusively."
            },
        }

    # Rule 2: Section 55 from direction compliance failures
    direction_applies, direction_reason = _detect_section_55_from_checklist(form_data)
    if direction_applies:
        sections.append("55")
        reasoning["55"] = direction_reason

    # Rule 3: Section 56 from hygiene violations
    hygiene_applies, hygiene_reason = _detect_section_56_from_checklist(form_data)
    if hygiene_applies:
        sections.append("56")
        reasoning["56"] = hygiene_reason

    # Rule 4: Sections 58 and 64 are manual-only - never auto-suggested
    sections = [s for s in sections if s not in _MANUAL_ONLY_SECTIONS]

    return {"sections": sections, "reasoning": reasoning}
