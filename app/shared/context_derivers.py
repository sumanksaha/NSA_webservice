"""Derived context helpers for document generation (STEP 4 of uniform-keys migration).

This module provides pure functions to derive the following context fields:
- applicable_sections: list[str] - e.g., ["55", "56", "58"]
- sections_display: str - e.g., "55, 56 and 58"
- case_track: "hygienic" | "nonsample_licence" | "sample"
- violations: list[dict] with keys: title, observation (adjudication only)
- same_entity: bool (case file)

All functions are pure (no side effects, same input -> same output).
"""

# =============================================================================
# VIOLATION DEFINITION (from adjudication RULES)
# =============================================================================

# Checklist violation rules - these map checkbox field names to (title, observation) tuples
# Used by adjudication to build the violations list
CHECKLIST_RULES: dict[str, tuple[str, str]] = {
    "clean_premise": ("Unclean Premises", "The premises were found inadequately maintained and unhygienic."),
    "refrigerator_clean": ("Improper Refrigerator Maintenance", "Refrigeration facilities were found unclean."),
    "proper_attire": ("Improper Protective Attire", "Food handlers lacked prescribed attire."),
    "proper_covered_utensil": ("Improper Covering of Food", "Food and utensils were uncovered."),
    "date_tag": ("Absence of Date Tagging", "Stored food items lacked traceability."),
    "veg_nonveg_separation": ("Improper Veg/Non-Veg Separation", "Segregation not maintained."),
    "food_segregation": ("Improper Food Segregation", "Risk of cross contamination."),
    "license_display": ("Improper License Display", "License not prominently displayed."),
    "Expired_item": ("Expired Items", "Expired items present."),
    "Pest_report": ("Pest Control Report Missing", "Routine pest control not documented."),
    "Water_report": ("Water Test Report Missing", "Potable water testing unavailable."),
}

CHECKLIST_FIELDS: tuple[str, ...] = (
    "clean_premise",
    "refrigerator_clean",
    "proper_attire",
    "proper_covered_utensil",
    "date_tag",
    "veg_nonveg_separation",
    "food_segregation",
    "license_display",
    "artificial_colour",
    "Expired_item",
    "Pest_report",
    "Water_report",
)


def derive_actions(violations: list[dict[str, str]]) -> list[str]:
    """Derive corrective actions for an Improvement Notice from violations.

    One action per violation, same order. Each action names the violated
    item (``title``) and what was observed, as a directive sentence.
    """
    return [f"Take corrective action: {v['title']} — {v['observation']}" for v in violations]


# Special violation rules that are not in the checklist but need to be checked
SPECIAL_VIOLATION_RULES: dict[str, tuple[str, str]] = {
    "artificial_colour": ("Use of Artificial Colours", "Artificial colours were reportedly used in food preparation."),
    "Expired_item": ("Expired Items Present", "Expired food items were found on the premises."),
}


# =============================================================================
# DERIVED CONTEXT HELPERS
# =============================================================================


def derive_applicable_sections_from_case_file(
    is_substandard: bool = False,
    is_misbranded: bool = False,
) -> list[str]:
    """Derive applicable sections for case file (sample-based) cases.

    Sample cases use sections 51 (substandard) and 52 (misbranded).

    Args:
        is_substandard: True if sample was found substandard
        is_misbranded: True if sample was found misbranded

    Returns:
        List of section numbers as strings (e.g., ["51", "52"])

    """
    sections = []
    if is_substandard:
        sections.append("51")
    if is_misbranded:
        sections.append("52")
    return sorted(sections)


def derive_applicable_sections_from_adjudication(
    section_55: bool = False,
    section_56: bool = False,
    section_58: bool = False,
    section_63: bool = False,
    section_64: bool = False,
) -> list[str]:
    """Derive applicable sections from adjudication form checkboxes.

    Scans all section checkbox fields and returns the enabled ones.
    The checkbox values should be boolean or 'yes'/'no' strings.

    Args:
        section_55: True if section 55 is selected
        section_56: True if section 56 is selected
        section_58: True if section 58 is selected
        section_63: True if section 63 is selected
        section_64: True if section 64 is selected

    Returns:
        List of section numbers as strings (e.g., ["55", "56", "58"])

    """
    sections = []

    # Normalize boolean checks - handle 'yes', 'no', True, False, 1, 0, etc.
    def is_checked(val):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("yes", "true", "1", "on")
        return bool(val)

    if is_checked(section_55):
        sections.append("55")
    if is_checked(section_56):
        sections.append("56")
    if is_checked(section_58):
        sections.append("58")
    if is_checked(section_63):
        sections.append("63")
    if is_checked(section_64):
        sections.append("64")

    return sorted(sections)


def derive_sections_display(applicable_sections: list[str]) -> str:
    """Convert a list of section numbers to a human-readable display string.

    Examples:
        ["55"] -> "55"
        ["55", "56"] -> "55 and 56"
        ["55", "56", "58"] -> "55, 56 and 58"
        ["55", "56", "58", "64"] -> "55, 56, 58 and 64"

    Args:
        applicable_sections: List of section numbers as strings

    Returns:
        Human-readable string for display in documents

    """
    if not applicable_sections:
        return ""
    if len(applicable_sections) == 1:
        return applicable_sections[0]
    if len(applicable_sections) == 2:
        return f"{applicable_sections[0]} and {applicable_sections[1]}"
    return ", ".join(applicable_sections[:-1]) + f" and {applicable_sections[-1]}"


def derive_case_track(
    non_license: bool = False,
    pre_authorization: bool = False,
    complaint_lodged: bool = False,
    is_sample: bool = False,
) -> str:
    """Determine the case track based on case characteristics.

    Logic:
    - "sample": cases with sample analysis (sections 51, 52) - is_sample=True
    - "nonsample_licence": non-license cases (section 63 path) - non_license=True
    - "hygienic": default inspection path (sections 55, 56, 58, 64)

    Args:
        non_license: True for non-licensed FBO cases (section 63)
        pre_authorization: True for pre-authorization cases
        complaint_lodged: True when third-party complaint was lodged
        is_sample: True for sample-based cases (case file generator)

    Returns:
        One of: "hygienic", "nonsample_licence", "sample"

    """

    # Normalize boolean inputs
    def normalize_bool(val):
        if isinstance(val, str):
            return val.strip().lower() in ("yes", "true", "1", "on")
        return bool(val)

    non_license = normalize_bool(non_license)
    is_sample = normalize_bool(is_sample)

    if is_sample:
        return "sample"
    if non_license:
        return "nonsample_licence"
    return "hygienic"


def derive_violations(form_data: dict) -> list[dict[str, str]]:
    """Derive violations list for adjudication cases.

    Scans checklist fields and builds a list of violation dicts with
    'title' and 'observation' keys (note: 'Observation' in templates,
    but canonical is 'observation').

    Also handles special cases like artificial_colour and Expired_item
    which have different logic.

    Args:
        form_data: Dictionary of adjudication form data with checklist fields

    Returns:
        List of violation dicts, each with 'title' and 'observation' keys.
        Empty list if no violations found.

    """
    violations = []

    # Helper to check if a field indicates a violation
    def is_violation(val):
        if isinstance(val, str):
            return val.strip().lower() == "no"
        return not val

    # Check checklist violations (fields marked as 'no' indicate violations)
    for field_name, (title, observation) in CHECKLIST_RULES.items():
        field_value = form_data.get(field_name)
        if field_value is not None and is_violation(field_value):
            violations.append({
                "title": title,
                "observation": observation,
            })

    # Check special violations (fields marked as 'yes' indicate violations)
    for field_name, (title, observation) in SPECIAL_VIOLATION_RULES.items():
        field_value = form_data.get(field_name)
        if field_value is not None:
            if isinstance(field_value, str):
                if field_value.strip().lower() == "yes":
                    violations.append({
                        "title": title,
                        "observation": observation,
                    })
            elif field_value:
                violations.append({
                    "title": title,
                    "observation": observation,
                })

    return violations


def derive_same_entity(
    manufacturer_fssai: str | None = None,
    retailer_fssai: str | None = None,
) -> bool:
    """Determine if manufacturer and retailer are the same entity.

    This is derived by comparing FSSAI license numbers. If they match,
    the manufacturer and retailer are considered the same entity.

    Args:
        manufacturer_fssai: Manufacturer's FSSAI license number
        retailer_fssai: Retailer's FSSAI license number

    Returns:
        True if both are provided and match, False otherwise

    """
    if not manufacturer_fssai or not retailer_fssai:
        return False
    return manufacturer_fssai.strip() == retailer_fssai.strip()


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Constants
    "CHECKLIST_RULES",
    "SPECIAL_VIOLATION_RULES",
    "derive_applicable_sections_from_adjudication",
    # Individual derivations
    "derive_applicable_sections_from_case_file",
    "derive_case_track",
    "derive_same_entity",
    "derive_sections_display",
    "derive_violations",
]
