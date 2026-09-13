"""Derived context helpers for document generation (STEP 4 of uniform-keys migration).

This module provides pure functions to derive the following context fields:
- applicable_sections: list[str] - e.g., ["55", "56", "58"]
- sections_display: str - e.g., "55, 56 and 58"
- case_track: "hygienic" | "nonsample_licence" | "sample"
- violations: list[dict] with keys: title, observation, field (adjudication only)
- same_entity: bool (case file)

All functions are pure (no side effects, same input -> same output).
"""

# =============================================================================
# VIOLATION DEFINITION (from adjudication RULES)
# =============================================================================

# Checklist violation rules - these map checkbox field names to (title, observation) tuples
# Used by adjudication to build the violations list
CHECKLIST_RULES: dict[str, tuple[str, str]] = {
    "clean_premise": (
        "Unclean Premises",
        "The food premises, including floors, walls, ceilings and food-contact surfaces, "
        "were not maintained in a clean and hygienic condition at the time of inspection.",
    ),
    "refrigerator_clean": (
        "Improper Refrigerator Maintenance",
        "The refrigeration facilities were not maintained in a clean condition and in proper "
        "working order, risking contamination of the food stored therein.",
    ),
    "proper_attire": (
        "Improper Protective Attire",
        "Food handlers on duty were not wearing clean protective clothing, headgear and "
        "footwear as required for the hygienic handling of food.",
    ),
    "proper_covered_utensil": (
        "Improper Covering of Food",
        "Food articles and utensils were found uncovered and exposed to dust, pests and "
        "other contaminants.",
    ),
    "date_tag": (
        "Absence of Date Tagging",
        "Stored food articles did not bear legible date tags or traceability markings, "
        "so batch identity and stock rotation could not be established.",
    ),
    "veg_nonveg_separation": (
        "Improper Veg/Non-Veg Separation",
        "Vegetarian and non-vegetarian food articles were not stored, handled and "
        "displayed separately from each other.",
    ),
    "food_segregation": (
        "Improper Food Segregation",
        "Raw, cooked and ready-to-eat food articles were not segregated at each stage "
        "of handling, creating a risk of cross-contamination.",
    ),
    "license_display": (
        "Improper License Display",
        "The FSSAI license/registration was not displayed at a prominent place in the "
        "food business premises.",
    ),
    "Pest_report": (
        "Pest Control Report Missing",
        "No valid pest-control record was produced at the time of inspection; routine "
        "pest management of the premises is undocumented.",
    ),
    "Water_report": (
        "Water Test Report Missing",
        "No potable-water test report was produced; the safety of water used in food "
        "preparation and cleaning is unverified.",
    ),
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


# Remediation directives per checklist field, rendered as the numbered
# corrective actions (Part 2) of the Improvement Notice. Unlike the
# observations above — which record what was *found* — these instruct
# what the FBO must *do*.
REMEDIATION_ACTIONS: dict[str, str] = {
    "clean_premise": (
        "Maintain the entire food premises, including all food-contact surfaces, "
        "in a clean and hygienic condition at all times."
    ),
    "refrigerator_clean": (
        "Clean and sanitise all refrigeration facilities and keep them in proper "
        "working condition."
    ),
    "proper_attire": (
        "Ensure all food handlers wear clean protective attire, including headgear "
        "and footwear, while on duty."
    ),
    "proper_covered_utensil": (
        "Keep all food articles and utensils covered and protected from dust, pests "
        "and other contaminants at all times."
    ),
    "date_tag": (
        "Affix legible date tags to all stored food articles and maintain "
        "batch-wise traceability and stock-rotation records."
    ),
    "veg_nonveg_separation": (
        "Store, handle and display vegetarian and non-vegetarian food articles "
        "strictly separately from each other."
    ),
    "food_segregation": (
        "Segregate raw, cooked and ready-to-eat food articles at every stage of "
        "handling so as to eliminate cross-contamination."
    ),
    "license_display": (
        "Display the FSSAI license/registration prominently at the entry of the "
        "food business premises."
    ),
    "artificial_colour": (
        "Discontinue the use of artificial colours except as permitted under the "
        "Food Safety and Standards (Food Products Standards and Food Additives) "
        "Regulations, and declare permitted colours on the label."
    ),
    "Expired_item": (
        "Remove all expired food articles from the premises immediately and "
        "institute first-expiry-first-out stock rotation."
    ),
    "Pest_report": (
        "Engage licensed pest control, eliminate pest harbourage in the premises, "
        "and preserve pest-management records for verification."
    ),
    "Water_report": (
        "Use only potable water in food operations and preserve periodic "
        "water-test reports for verification."
    ),
}


def derive_actions(violations: list[dict[str, str]]) -> list[str]:
    """Derive corrective actions for an Improvement Notice from violations.

    One directive per violation, same order. Each violation carries its
    checklist ``field`` (see :func:`derive_violations`), which selects a
    specific remediation instruction from :data:`REMEDIATION_ACTIONS`.
    Violations without a known field fall back to a generic directive.
    """
    actions = []
    for v in violations:
        field = v.get("field", "") if isinstance(v, dict) else ""
        directive = REMEDIATION_ACTIONS.get(field)
        if directive:
            actions.append(directive)
            continue
        title = v.get("title", "") if isinstance(v, dict) else str(v)
        actions.append(f"Take immediate corrective action to rectify: {title}.")
    return actions


# Special violation rules that are not in the checklist but need to be checked
SPECIAL_VIOLATION_RULES: dict[str, tuple[str, str]] = {
    "artificial_colour": (
        "Use of Artificial Colours",
        "Artificial colours were reportedly used in the preparation of food articles.",
    ),
    "Expired_item": (
        "Expired Items Present",
        "Food articles past their expiry/best-before date were found stored on the premises.",
    ),
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
    'title', 'observation' and 'field' (checklist key) keys.

    Also handles special cases like artificial_colour and Expired_item
    which have different logic.

    Args:
        form_data: Dictionary of adjudication form data with checklist fields

    Returns:
        List of violation dicts, each with 'title', 'observation' and
        'field' (checklist key) keys. Empty list if no violations found.

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
                "field": field_name,
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
                        "field": field_name,
                    })
            elif field_value:
                violations.append({
                    "title": title,
                    "observation": observation,
                    "field": field_name,
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
    "REMEDIATION_ACTIONS",
    "SPECIAL_VIOLATION_RULES",
    "derive_actions",
    "derive_applicable_sections_from_adjudication",
    # Individual derivations
    "derive_applicable_sections_from_case_file",
    "derive_case_track",
    "derive_same_entity",
    "derive_sections_display",
    "derive_violations",
]
