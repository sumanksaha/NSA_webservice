"""Uniform keys contract for NSA_webservice migration.

This module defines the canonical key naming contract for all case-related data
across the four separate UIs: Inspection, Sample, Case File Generator, and Adjudication.

RULES:
- All four UIs remain separate (no merging of templates, routes, or business logic)
- Date disambiguation:
  * inspection_date: ONLY for Inspection module primary visit
  * first_inspection_date: adjudication first visit date
  * followup_inspection_date: adjudication follow-up visit date
  * inspection_date in case_file: sample draw date (different semantic)
- applicable_sections is the canonical liability list key for all templates
- Section UI keys (section_55, section_56, etc.) remain as form-level checkboxes

UI OWNERSHIP:
- Inspection: primary inspection data, FSO assignment, FBO identity
- Sample: sample collection, retailer info, lab submission
- Case File Generator: manufacturer/retailer parties, sample details, lab results
- Adjudication: non-sample cases, section selection, legal proceedings
"""

from typing import TypedDict

# =============================================================================
# CANONICAL KEY CONSTANTS
# =============================================================================

# --- SHARED IDENTITY FIELDS (across all modules)
SHARED_FSO_NAME = "food_safety_officer_name"
SHARED_FBO_OWNER = "fbo_owner"
SHARED_FBO_NAME = "fbo_name"
SHARED_FBO_ADDRESS = "fbo_address"
SHARED_FSSAI_LICENSE = "fssai_license"
SHARED_CASE_NUMBER = "case_number"
SHARED_AUTHORIZATION_DATE = "authorization_date"
SHARED_COMPLIANCE_DEADLINE = "compliance_deadline"
SHARED_CONCERNED_FOOD = "concerned_food"
SHARED_PROBLEM = "problem"
SHARED_COMPLAINT_LODGED = "complaint_lodged"
SHARED_NON_LICENSE = "non_license"
SHARED_PRE_AUTHORIZATION = "pre_authorization"
SHARED_FROM_INSPECTION = "from_inspection"


# --- DATE FIELDS (strictly disambiguated)
# Inspection module ONLY
DATE_INSPECTION = "inspection_date"  # Primary inspection visit date

# Adjudication dates
DATE_FIRST_INSPECTION = "first_inspection_date"  # Adjudication: first visit
DATE_FOLLOWUP_INSPECTION = "followup_inspection_date"  # Adjudication: follow-up

# Complaint dates
DATE_COMPLAINT = "complaint_date"

# Sample dates
DATE_SAMPLE_DRAW = "sample_draw_date"
DATE_SAMPLE_DRAW_TIME = "sample_draw_time"
DATE_SAMPLE_SUBMISSION = "sample_submission_date"

# Lab/report dates
DATE_DO_RECEIPT = "do_receipt_date"
DATE_ANALYST_REPORT = "analyst_report_date"
DATE_DIRECTIVE_LETTER = "directive_letter_date"
DATE_RETAILER_REPORT_RECEIVE = "retailer_report_receive_date"
DATE_MANUFACTURER_REPORT_RECEIVE = "manufacturer_report_receive_date"


# --- PARTY FIELDS (Manufacturer / Retailer)
PARTY_MANUFACTURER_PERSON_NAME = "manufacturer_person_name"
PARTY_MANUFACTURER_TRADE_NAME = "manufacturer_trade_name"
PARTY_MANUFACTURER_ADDRESS = "manufacturer_address"
PARTY_MANUFACTURER_FSSAI = "manufacturer_fssai_license"

PARTY_RETAILER_PERSON_NAME = "retailer_person_name"
PARTY_RETAILER_TRADE_NAME = "retailer_trade_name"
PARTY_RETAILER_ADDRESS = "retailer_address"
PARTY_RETAILER_FSSAI = "retailer_fssai_license"

PARTY_SAME_ENTITY = "same_entity"  # Derived: True if manufacturer == retailer


# --- SAMPLE / LAB FIELDS
SAMPLE_ID = "sample_id"
SAMPLE_CODE = "sample_code"
SAMPLE_NAME = "sample_name"
SAMPLE_TYPE = "sample_type"
SAMPLE_PRODUCT_NAME = "product_name"
SAMPLE_BATCH_NO = "batch_no"
SAMPLE_QUANTITY = "sample_quantity"
SAMPLE_PACKET_COUNT = "packet_count"
SAMPLE_MFG_DATE = "mfg_date"
SAMPLE_EXPIRY_DATE = "expiry_date"
SAMPLE_TOTAL_COST = "total_cost"
SAMPLE_COST_IN_WORDS = "cost_in_words"
SAMPLE_OTHER_FOOD_ARTICLES = "other_food_articles"

LAB_REGISTRATION_NO = "lab_registration_no"
LAB_ANALYST_REPORT_NO = "analyst_report_no"
LAB_DIRECTIVE_LETTER_NO = "directive_letter_no"

SAMPLE_APPLICABLE_REGULATION = "applicable_regulation"
SAMPLE_APPLICABLE_CLAUSE = "applicable_clause"

# Sample analysis flags (UI-level in case file)
SAMPLE_IS_SUBSTANDARD = "is_substandard"
SAMPLE_IS_MISBRANDED = "is_misbranded"


# --- SECTION FIELDS (form-level checkboxes, unchanged)
SECTION_55 = "section_55"
SECTION_56 = "section_56"
SECTION_58 = "section_58"
SECTION_63 = "section_63"
SECTION_64 = "section_64"

# All section keys as a tuple for iteration
SECTION_KEYS = (SECTION_55, SECTION_56, SECTION_58, SECTION_63, SECTION_64)


# --- DERIVED FIELDS (computed by generators, documented here)
DERIVED_APPLICABLE_SECTIONS = "applicable_sections"  # list[str] e.g. ["55", "56", "58"]
DERIVED_SECTIONS_DISPLAY = "sections_display"  # str e.g. "55, 56 and 58"
DERIVED_CASE_TRACK = "case_track"  # "hygienic" | "nonsample_licence" | "sample"
DERIVED_VIOLATIONS = "violations"  # list[dict[str, str]] with keys: title, observation
DERIVED_SAME_ENTITY = "same_entity"  # bool - True if manufacturer == retailer
DERIVED_DOCUMENT_ROLE = "document_role"  # "authorization" | "petition" | "cover"


# =============================================================================
# OLD TO NEW KEY MAPPINGS PER MODULE
# =============================================================================

# --- INSPECTION MODULE MAP
# Current keys -> Canonical keys
INSPECTION_OLD_TO_NEW = {
    # FSO / dates
    "fso_name": SHARED_FSO_NAME,
    "inspection_date": DATE_INSPECTION,
    "compliance_deadline": SHARED_COMPLIANCE_DEADLINE,
    # FBO identity
    "fbo_name": SHARED_FBO_NAME,
    "fbo_address": SHARED_FBO_ADDRESS,
    "fssai_license": SHARED_FSSAI_LICENSE,
    # Food/problem
    "concerned_food": SHARED_CONCERNED_FOOD,
    "problem": SHARED_PROBLEM,
    # CE license (inspection-specific)
    "ce_license_no": "ce_license_no",  # Keep as-is for now (inspection-specific)
}

# Reverse map for inspection (canonical -> old for backward compatibility if needed)
INSPECTION_NEW_TO_OLD = {v: k for k, v in INSPECTION_OLD_TO_NEW.items()}


# --- SAMPLE MODULE MAP
SAMPLE_OLD_TO_NEW = {
    # Sample basics
    "sample_name": SAMPLE_NAME,
    "sample_type": SAMPLE_TYPE,
    # FSO
    "fso_name": SHARED_FSO_NAME,
    # Dates
    "collection_date": DATE_SAMPLE_DRAW,
    "submission_date": DATE_SAMPLE_SUBMISSION,
    # Retailer
    "retailer_fssai": PARTY_RETAILER_FSSAI,
    "retailer_name": PARTY_RETAILER_PERSON_NAME,
    # Price
    "price": SAMPLE_TOTAL_COST,
}

SAMPLE_NEW_TO_OLD = {v: k for k, v in SAMPLE_OLD_TO_NEW.items()}


# --- ADJUDICATION MODULE MAP
ADJUDICATION_OLD_TO_NEW = {
    # FSO
    "food_safety_officer": SHARED_FSO_NAME,
    # Case flags
    "non_license": SHARED_NON_LICENSE,
    "pre_authorization": SHARED_PRE_AUTHORIZATION,
    "complaint_lodged": SHARED_COMPLAINT_LODGED,
    # Case identity
    "case_number": SHARED_CASE_NUMBER,
    "fbo_owner": SHARED_FBO_OWNER,
    "fbo_name": SHARED_FBO_NAME,
    "fbo_address": SHARED_FBO_ADDRESS,
    "fssai_license": SHARED_FSSAI_LICENSE,
    # Food/problem
    "concerned_food": SHARED_CONCERNED_FOOD,
    "problem": SHARED_PROBLEM,
    # Dates - CRITICAL: disambiguate
    "First_inspection_date": DATE_FIRST_INSPECTION,  # Adjudication first visit
    "inspection_date": DATE_FOLLOWUP_INSPECTION,  # Adjudication follow-up (currently misnamed)
    "Complaint_date": DATE_COMPLAINT,
    "Expired_item": "expired_item",
    "Pest_report": "pest_report",
    "Water_report": "water_report",
    "compliance_deadline": SHARED_COMPLIANCE_DEADLINE,
    "authorization_date": SHARED_AUTHORIZATION_DATE,
    # Sections (keep as-is, form-level)
    SECTION_55: SECTION_55,
    SECTION_56: SECTION_56,
    SECTION_58: SECTION_58,
    SECTION_63: SECTION_63,
    SECTION_64: SECTION_64,
    # Link to inspection
    "from_inspection": SHARED_FROM_INSPECTION,
}

ADJUDICATION_NEW_TO_OLD = {v: k for k, v in ADJUDICATION_OLD_TO_NEW.items()}


# --- CASE FILE GENERATOR MODULE MAP
CASE_FILE_OLD_TO_NEW = {
    # Case identity
    "case_number": SHARED_CASE_NUMBER,
    # FSO
    "food_safety_officer_name": SHARED_FSO_NAME,
    # Dates
    "authorization_date": SHARED_AUTHORIZATION_DATE,
    "inspection_date": DATE_SAMPLE_DRAW,  # Case file: this is sample draw date
    "inspection_time": DATE_SAMPLE_DRAW_TIME,
    # Manufacturer
    "manufacturer_fssai": PARTY_MANUFACTURER_FSSAI,
    "manufacturer_name": PARTY_MANUFACTURER_PERSON_NAME,
    "manufacturer_fbo_name": PARTY_MANUFACTURER_TRADE_NAME,
    "manufacturer_address": PARTY_MANUFACTURER_ADDRESS,
    # Retailer
    "retailer_fssai": PARTY_RETAILER_FSSAI,
    "retailer_name": PARTY_RETAILER_PERSON_NAME,
    "retailer_fbo_name": PARTY_RETAILER_TRADE_NAME,
    "retailer_address": PARTY_RETAILER_ADDRESS,
    # Sample link
    "sample_id": SAMPLE_ID,
    # Product/sample details
    "product_name": SAMPLE_PRODUCT_NAME,
    "sample_name": SAMPLE_NAME,
    "batch_no": SAMPLE_BATCH_NO,
    "sample_quantity": SAMPLE_QUANTITY,
    "packet_count": SAMPLE_PACKET_COUNT,
    "mfg_date": SAMPLE_MFG_DATE,
    "expiry_date": SAMPLE_EXPIRY_DATE,
    "total_cost": SAMPLE_TOTAL_COST,
    "cost_in_words": SAMPLE_COST_IN_WORDS,
    "other_food_articles": SAMPLE_OTHER_FOOD_ARTICLES,
    # Lab
    "Lab_Registration_No": LAB_REGISTRATION_NO,
    "sample_submission_date": DATE_SAMPLE_SUBMISSION,
    "do_receipt_date": DATE_DO_RECEIPT,
    "analyst_report_no": LAB_ANALYST_REPORT_NO,
    "analyst_report_date": DATE_ANALYST_REPORT,
    "directive_letter_no": LAB_DIRECTIVE_LETTER_NO,
    "directive_letter_date": DATE_DIRECTIVE_LETTER,
    # Regulations
    "applicable_regulation": SAMPLE_APPLICABLE_REGULATION,
    "applicable_clause": SAMPLE_APPLICABLE_CLAUSE,
    # Sample analysis flags
    "is_misbranded": SAMPLE_IS_MISBRANDED,
    "is_substandard": SAMPLE_IS_SUBSTANDARD,
    # Report receive dates
    "retailer_report_receive_date": DATE_RETAILER_REPORT_RECEIVE,
    "manufacturer_report_receive_date": DATE_MANUFACTURER_REPORT_RECEIVE,
    # Derived
    "same_entity": PARTY_SAME_ENTITY,
    # Sample code
    "sample_code": SAMPLE_CODE,
}

CASE_FILE_NEW_TO_OLD = {v: k for k, v in CASE_FILE_OLD_TO_NEW.items()}


# =============================================================================
# DERIVED FIELD SHAPES (TypedDict for documentation)
# =============================================================================


class ViolationDict(TypedDict):
    """Shape of a single violation entry in the derived violations list."""

    title: str
    observation: str  # lowercase observation text


class ApplicableSectionsShape(TypedDict):
    """Shape for derived applicable_sections data."""

    sections: list[str]  # e.g., ["55", "56", "58"]
    display: str  # e.g., "55, 56 and 58"


# =============================================================================
# HELPER STUBS (tiny, pure functions only)
# =============================================================================


def sections_display(sections: list[str]) -> str:
    """Convert a list of section numbers to a human-readable display string.

    Examples:
        ["55"] -> "55"
        ["55", "56"] -> "55 and 56"
        ["55", "56", "58"] -> "55, 56 and 58"
        ["55", "56", "58", "64"] -> "55, 56, 58 and 64"

    Args:
        sections: List of section numbers as strings (e.g., ["55", "56", "58"])

    Returns:
        Human-readable string for display in documents

    """
    if not sections:
        return ""
    if len(sections) == 1:
        return sections[0]
    if len(sections) == 2:
        return f"{sections[0]} and {sections[1]}"
    return ", ".join(sections[:-1]) + f" and {sections[-1]}"


def resolve_case_track(
    non_license: bool = False,
    pre_authorization: bool = False,
    complaint_lodged: bool = False,
    is_sample: bool = False,
) -> str:
    """Determine the case track based on case characteristics.

    Logic:
    - nonsample_licence: non_license cases (section 63 path)
    - sample: cases with sample analysis (sections 51, 52)
    - hygienic: default inspection path (sections 55, 56, 58, 64)

    Args:
        non_license: True for non-licensed FBO cases
        pre_authorization: True for pre-authorization cases
        complaint_lodged: True when third-party complaint was lodged
        is_sample: True for sample-based cases (case file generator)

    Returns:
        One of: "hygienic", "nonsample_licence", "sample"

    """
    if is_sample:
        return "sample"
    if non_license:
        return "nonsample_licence"
    return "hygienic"


# =============================================================================
# SECTION RESOLUTION HELPERS
# =============================================================================


def get_hygienic_sections() -> list[str]:
    """Return the canonical hygienic inspection section list."""
    return ["55", "56", "58"]


def get_nonsample_licence_sections() -> list[str]:
    """Return the canonical non-sample licence section list."""
    return ["63"]


def get_sample_sections(is_substandard: bool = True, is_misbranded: bool = True) -> list[str]:
    """Return sample-based sections based on analysis results.

    Args:
        is_substandard: True if sample was found substandard
        is_misbranded: True if sample was found misbranded

    Returns:
        List of applicable section numbers

    """
    sections = []
    if is_substandard:
        sections.append("51")
    if is_misbranded:
        sections.append("52")
    return sections


# =============================================================================
# FULL MODULE EXPORTS
# =============================================================================

__all__ = [
    "ADJUDICATION_NEW_TO_OLD",
    "ADJUDICATION_OLD_TO_NEW",
    "CASE_FILE_NEW_TO_OLD",
    "CASE_FILE_OLD_TO_NEW",
    "DATE_ANALYST_REPORT",
    "DATE_COMPLAINT",
    "DATE_DIRECTIVE_LETTER",
    "DATE_DO_RECEIPT",
    "DATE_FIRST_INSPECTION",
    "DATE_FOLLOWUP_INSPECTION",
    # Canonical key constants - Dates
    "DATE_INSPECTION",
    "DATE_MANUFACTURER_REPORT_RECEIVE",
    "DATE_RETAILER_REPORT_RECEIVE",
    "DATE_SAMPLE_DRAW",
    "DATE_SAMPLE_DRAW_TIME",
    "DATE_SAMPLE_SUBMISSION",
    # Canonical key constants - Derived
    "DERIVED_APPLICABLE_SECTIONS",
    "DERIVED_CASE_TRACK",
    "DERIVED_DOCUMENT_ROLE",
    "DERIVED_SAME_ENTITY",
    "DERIVED_SECTIONS_DISPLAY",
    "DERIVED_VIOLATIONS",
    "INSPECTION_NEW_TO_OLD",
    # Mappings
    "INSPECTION_OLD_TO_NEW",
    "LAB_ANALYST_REPORT_NO",
    "LAB_DIRECTIVE_LETTER_NO",
    "LAB_REGISTRATION_NO",
    "PARTY_MANUFACTURER_ADDRESS",
    "PARTY_MANUFACTURER_FSSAI",
    # Canonical key constants - Parties
    "PARTY_MANUFACTURER_PERSON_NAME",
    "PARTY_MANUFACTURER_TRADE_NAME",
    "PARTY_RETAILER_ADDRESS",
    "PARTY_RETAILER_FSSAI",
    "PARTY_RETAILER_PERSON_NAME",
    "PARTY_RETAILER_TRADE_NAME",
    "PARTY_SAME_ENTITY",
    "SAMPLE_APPLICABLE_CLAUSE",
    "SAMPLE_APPLICABLE_REGULATION",
    "SAMPLE_BATCH_NO",
    "SAMPLE_CODE",
    "SAMPLE_COST_IN_WORDS",
    "SAMPLE_EXPIRY_DATE",
    # Canonical key constants - Sample/Lab
    "SAMPLE_ID",
    "SAMPLE_IS_MISBRANDED",
    "SAMPLE_IS_SUBSTANDARD",
    "SAMPLE_MFG_DATE",
    "SAMPLE_NAME",
    "SAMPLE_NEW_TO_OLD",
    "SAMPLE_OLD_TO_NEW",
    "SAMPLE_OTHER_FOOD_ARTICLES",
    "SAMPLE_PACKET_COUNT",
    "SAMPLE_PRODUCT_NAME",
    "SAMPLE_QUANTITY",
    "SAMPLE_TOTAL_COST",
    "SAMPLE_TYPE",
    # Canonical key constants - Sections
    "SECTION_55",
    "SECTION_56",
    "SECTION_58",
    "SECTION_63",
    "SECTION_64",
    "SECTION_KEYS",
    "SHARED_AUTHORIZATION_DATE",
    "SHARED_CASE_NUMBER",
    "SHARED_COMPLAINT_LODGED",
    "SHARED_COMPLIANCE_DEADLINE",
    "SHARED_CONCERNED_FOOD",
    "SHARED_FBO_ADDRESS",
    "SHARED_FBO_NAME",
    "SHARED_FBO_OWNER",
    "SHARED_FROM_INSPECTION",
    # Canonical key constants - Shared
    "SHARED_FSO_NAME",
    "SHARED_FSSAI_LICENSE",
    "SHARED_NON_LICENSE",
    "SHARED_PRE_AUTHORIZATION",
    "SHARED_PROBLEM",
    "ApplicableSectionsShape",
    # TypedDict shapes
    "ViolationDict",
    "get_hygienic_sections",
    "get_nonsample_licence_sections",
    "get_sample_sections",
    "resolve_case_track",
    # Helper functions
    "sections_display",
]
