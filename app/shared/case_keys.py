"""Canonical key names for case-related template context.

Only keys imported by live modules are kept here. The ``*_OLD_TO_NEW``
migration maps, TypedDict shapes, and section/track helper functions that
previously lived in this module had zero importers and were removed
(shrink pass, 2026-09-13). The pure derivation functions live in
:mod:`app.shared.context_derivers`.

RULES:
- Date disambiguation:
  * inspection_date: ONLY for Inspection module primary visit
  * first_inspection_date: adjudication first visit date
  * followup_inspection_date: adjudication follow-up visit date
  * inspection_date in case_file: sample draw date (different semantic)
- applicable_sections is the canonical liability list key for all templates
- Section UI keys (section_55, section_56, etc.) remain as form-level checkboxes
"""

# --- Shared flags (adjudication case track) ---
SHARED_NON_LICENSE = "non_license"
SHARED_PRE_AUTHORIZATION = "pre_authorization"
SHARED_COMPLAINT_LODGED = "complaint_lodged"

# --- Section checkboxes (form-level, unchanged) ---
SECTION_55 = "section_55"
SECTION_56 = "section_56"
SECTION_58 = "section_58"
SECTION_63 = "section_63"
SECTION_64 = "section_64"

# --- Derived context fields (template keys) ---
DERIVED_APPLICABLE_SECTIONS = "applicable_sections"  # list[str] e.g. ["55", "56", "58"]
DERIVED_SECTIONS_DISPLAY = "sections_display"  # str e.g. "55, 56 and 58"
DERIVED_CASE_TRACK = "case_track"  # "hygienic" | "nonsample_licence" | "sample"
DERIVED_VIOLATIONS = "violations"  # list[dict[str, str]] with keys: title, observation
DERIVED_SAME_ENTITY = "same_entity"  # bool - True if manufacturer == retailer

__all__ = [
    "DERIVED_APPLICABLE_SECTIONS",
    "DERIVED_CASE_TRACK",
    "DERIVED_SAME_ENTITY",
    "DERIVED_SECTIONS_DISPLAY",
    "DERIVED_VIOLATIONS",
    "SECTION_55",
    "SECTION_56",
    "SECTION_58",
    "SECTION_63",
    "SECTION_64",
    "SHARED_COMPLAINT_LODGED",
    "SHARED_NON_LICENSE",
    "SHARED_PRE_AUTHORIZATION",
]
