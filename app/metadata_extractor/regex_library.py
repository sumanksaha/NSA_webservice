"""Comprehensive regex pattern library for Indian legal document metadata extraction.

Organised by field. Each pattern is a pre-compiled ``re.Pattern`` with a
descriptive name, so the extraction engine can report *which* pattern matched
and use the information for confidence scoring.

All patterns match case-insensitively unless otherwise noted.
"""

from __future__ import annotations

import re

# ============================================================================
# Helper
# ============================================================================

_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _p(pattern: str, flags: int = re.IGNORECASE | re.MULTILINE | re.DOTALL) -> re.Pattern:
    """Compile and cache a regex pattern."""
    key = f"{flags}:{pattern}"
    if key not in _PATTERN_CACHE:
        _PATTERN_CACHE[key] = re.compile(pattern, flags)
    return _PATTERN_CACHE[key]


# ============================================================================
# 1. Document Title
# ============================================================================

TITLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # "Food Safety and Standards Act, 2006" — short title with year
    (
        "short_title_act",
        _p(
            r"(?:THE\s+)?([A-Z][A-Z\s&,]+(?:ACT|REGULATION|RULE|ORDER|NOTIFICATION|BILL|CODE|SCHEME|POLICY))\s*,\s*\d{4}",
        ),
    ),
    # "Food Safety and Standards (Amendment) Act, 2020"
    (
        "short_title_amended",
        _p(r"(?:THE\s+)?([A-Z][A-Z\s&,(\)]+(?:AMENDMENT|REPEAL|VALIDATION)\s*(?:ACT|REGULATION|RULE))\s*,\s*\d{4}"),
    ),
    # "An Act to provide for ..." — long title first line
    (
        "long_title_act",
        _p(
            r"(?:AN\s+)?ACT\s+(?:to\s+.{10,200}?(?:,|\.))",
        ),
    ),
    # Title line after "THE GAZETTE OF INDIA"
    (
        "gazette_title",
        _p(
            r"(?:EXTRAORDINARY|EXTRA[\s,]*ORDINARY)?\s*(?:PART\s+[A-Z]+[\s,]*SECTION\s+[A-Z0-9]+[\s,]*)?\s*(?:PUBLISHED BY AUTHORITY)?\s*\n{1,3}(.{20,200}?)(?:\n|$)",
        ),
    ),
    # "In exercise of the powers conferred ..." preamble
    (
        "preamble_title",
        _p(
            r"(?:IN\s+)?EXERCISE\s+OF\s+THE\s+POWERS\s+CONFERRED\s+(?:BY|UNDER)\s+(?:SECTION\s+\d+\s+(?:OF\s+)?)?(?:THE\s+)?([A-Z][A-Z\s&]+(?:ACT|REGULATION|RULE))\s*[\(,]",
        ),
    ),
]


# ============================================================================
# 2. Version / Amendment
# ============================================================================

VERSION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "amendment_number",
        _p(
            r"(?:AS\s+)?(?:AMENDED\s+(?:FROM\s+TIME\s+TO\s+TIME\s+)?)?(?:UP\s+TO\s+)?(?:THE\s+)?(\d+(?:ST|ND|RD|TH))\s+(?:AMENDMENT)",
        ),
    ),
    (
        "amendment_year",
        _p(
            r"(?:AMENDED\s+(?:BY|VIDE|PURSUANT\s+TO)\s+)?(?:\w+\s+)?(?:AMENDMENT\s+)?(?:ACT|REGULATION|NOTIFICATION)\s+(?:NO\.?\s*)?(\d+)\s+OF\s+(\d{4})",
        ),
    ),
    (
        "latest_version",
        _p(
            r"(?:AS\s+)?(?:ON\s+)?(\d{1,2}[\s./-]+[A-Z][a-z]+[\s./-]+\d{4})\s*(?:VERSION|EDITION|REPRINT|UPDATE)",
        ),
    ),
    (
        "version_date",
        _p(
            r"(?:VERSION|EDITION|UPDATED\s+(?:UP\s+)?TO)\s*(?::\s*)?(\d{1,2}[\s./-]+[A-Z][a-z]+[\s./-]+\d{4})",
        ),
    ),
]


# ============================================================================
# 3. Dates (notification / enactment / publication)
# ============================================================================

DATE_PATTERNS: list[tuple[str, re.Pattern]] = [
    # "Dated the 5th August, 2020"
    (
        "dated_ordinal",
        _p(
            r"(?:DATED|DATE|DT[.:]?)\s*(?:THE\s+)?(\d{1,2})(?:ST|ND|RD|TH)?\s+(?:DAY\s+OF\s+)?([A-Z][a-z]+)[\s,]+(\d{4})",
        ),
    ),
    # "dd/mm/yyyy" or "dd-mm-yyyy"
    (
        "dd_mm_yyyy",
        _p(
            r"\b(0?[1-9]|[12]\d|3[01])[\s/.-](0?[1-9]|1[0-2])[\s/.-](\d{4})\b",
        ),
    ),
    # "yyyy-mm-dd" (ISO)
    (
        "yyyy_mm_dd",
        _p(
            r"\b(\d{4})[\s/.-](0?[1-9]|1[0-2])[\s/.-](0?[1-9]|[12]\d|3[01])\b",
        ),
    ),
    # "5th August, 2020" (standalone)
    (
        "ordinal_month_year",
        _p(
            r"\b(\d{1,2})(?:ST|ND|RD|TH)?\s+(?:DAY\s+OF\s+)?([A-Z][a-z]+)[\s,]+(\d{4})\b",
        ),
    ),
    # "August 5, 2020" (US format)
    (
        "month_dd_yyyy",
        _p(
            r"\b([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})\b",
        ),
    ),
]

# Month name mapping
MONTH_MAP: dict[str, str] = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


# ============================================================================
# 4. Issuing Authority
# ============================================================================

# Known Indian legal authorities
AUTHORITY_NAMES: dict[str, list[str]] = {
    "fssai": [
        "FOOD SAFETY AND STANDARDS AUTHORITY OF INDIA",
        "FSSAI",
        "FOOD SAFETY AND STANDARDS AUTHORITY",
    ],
    "mohfw": [
        "MINISTRY OF HEALTH AND FAMILY WELFARE",
        "MoHFW",
        "MINISTRY OF HEALTH",
    ],
    "law_ministry": [
        "MINISTRY OF LAW AND JUSTICE",
        "LEGISLATIVE DEPARTMENT",
        "DEPARTMENT OF LEGAL AFFAIRS",
    ],
    "fci": [
        "FOOD CORPORATION OF INDIA",
        "FCI",
    ],
    "bureau_indian_standards": [
        "BUREAU OF INDIAN STANDARDS",
        "BIS",
    ],
    "commerce": [
        "MINISTRY OF COMMERCE AND INDUSTRY",
        "DIRECTORATE GENERAL OF FOREIGN TRADE",
        "DGFT",
    ],
    "agriculture": [
        "MINISTRY OF AGRICULTURE AND FARMERS WELFARE",
    ],
    "consumer_affairs": [
        "MINISTRY OF CONSUMER AFFAIRS",
        "DEPARTMENT OF CONSUMER AFFAIRS",
    ],
    "gst_council": [
        "GOODS AND SERVICES TAX COUNCIL",
        "GST COUNCIL",
    ],
}

AUTHORITY_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "notification_authority",
        _p(
            r"(?:ISSUED\s+BY|ISSUING\s+AUTHORITY|PROMULGATED\s+BY|PUBLISHED\s+BY)\s*[\-]?\s*([A-Z][A-Z\s&,]+(?:AUTHORITY|BOARD|COUNCIL|COMMISSION|MINISTRY|DEPARTMENT|BUREAU|DIRECTORATE|TRIBUNAL|COURT))",
        ),
    ),
    (
        "in_preamble",
        _p(
            r"IN\s+EXERCISE\s+OF\s+THE\s+POWERS\s+CONFERRED\s+BY\s+(?:SECTION\s+\d+\s+OF\s+)?(?:THE\s+)?([A-Z][A-Z\s&]+(?:ACT|REGULATION))\s*[\(,]\s*(?:THE\s+)?([A-Z][A-Z\s&,]+(?:AUTHORITY|BOARD|MINISTRY|COUNCIL|COMMISSION))",
        ),
    ),
    (
        "gazette_authority",
        _p(
            r"(?:PUBLISHED\s+BY\s+AUTHORITY|BY\s+ORDER\s+AND\s+IN\s+THE\s+NAME\s+OF\s+THE\s+(?:PRESIDENT|GOVERNOR))",
        ),
    ),
]

# Build a combined authority pattern from known names
_KNOWN_AUTH_NAMES = "|".join(name.replace(" ", r"\s+") for names in AUTHORITY_NAMES.values() for name in names)
AUTHORITY_KNOWN_PATTERN = _p(
    rf"\b({_KNOWN_AUTH_NAMES})\b",
)


# ============================================================================
# 5. Gazette & Notification Numbers
# ============================================================================

GAZETTE_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "gazette_of_india",
        _p(
            r"(?:THE\s+)?GAZETTE\s+OF\s+INDIA\s*(?:\n|,|:)?\s*(?:\s*(?:EXTRAORDINARY|PART\s+[A-Z]+|SECTION\s+[A-Z0-9]+))?",
        ),
    ),
    (
        "gazette_notification_no",
        _p(
            r"(?:GAZETTE\s+)?(?:NOTIFICATION|ORDER|S\.?\s*O\.?|S\.?\s*R\.?\.?\s*O\.?|G\.?\s*S\.?\s*R\.?)\s*(?:NO\.?\s*)?([A-Z\d]+[\s\-/]*[A-Z\d]+)",
        ),
    ),
    (
        "notification_no",
        _p(
            r"(?:NOTIFICATION|FILE|REF|F\.?\s*NO\.?|NO\.?)\s*(?:NO\.?\s*)?[\-]?\s*([A-Z]*\d{1,6}[-\s/]?\d{1,4}[-\s/]?\d{0,4})",
        ),
    ),
    (
        "fssai_notification",
        _p(
            r"(?:F\.?\s*NO\.?\s*)?(\d{1,2}\(?\d{0,2}\)?/\d{4}(?:-\w+)?)",
        ),
    ),
]


# ============================================================================
# 6. Language Detection (based on Unicode scripts)
# ============================================================================

LANGUAGE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("hindi", _p(r"[\u0900-\u097F]")),
    ("bengali", _p(r"[\u0980-\u09FF]")),
    ("tamil", _p(r"[\u0B80-\u0BFF]")),
    ("telugu", _p(r"[\u0C00-\u0C7F]")),
    ("marathi", _p(r"[\u0900-\u097F]")),  # Same Devanagari as Hindi
    ("gujarati", _p(r"[\u0A80-\u0AFF]")),
    ("kannada", _p(r"[\u0C80-\u0CFF]")),
    ("malayalam", _p(r"[\u0D00-\u0D7F]")),
    ("punjabi", _p(r"[\u0A00-\u0A7F]")),
    ("oriya", _p(r"[\u0B00-\u0B7F]")),
]


# ============================================================================
# 7. Jurisdiction
# ============================================================================

JURISDICTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("supreme_court", _p(r"\b(?:SUPREME\s+COURT\s+OF\s+INDIA|SCI)\b")),
    ("high_court", _p(r"\b([A-Z][A-Z\s]+)HIGH\s+COURT\b")),
    ("central_govt", _p(r"\b(CENTRAL\s+GOVERNMENT|GOVERNMENT\s+OF\s+INDIA|REPUBLIC\s+OF\s+INDIA)\b")),
    ("state_govt", _p(r"\b(GOVERNMENT\s+OF\s+(?:[A-Z][A-Z\s]+))\b")),
]

# Indian states and union territories
INDIAN_STATES: list[str] = [
    "ANDHRA PRADESH",
    "ARUNACHAL PRADESH",
    "ASSAM",
    "BIHAR",
    "CHHATTISGARH",
    "GOA",
    "GUJARAT",
    "HARYANA",
    "HIMACHAL PRADESH",
    "JHARKHAND",
    "KARNATAKA",
    "KERALA",
    "MADHYA PRADESH",
    "MAHARASHTRA",
    "MANIPUR",
    "MEGHALAYA",
    "MIZORAM",
    "NAGALAND",
    "ODISHA",
    "PUNJAB",
    "RAJASTHAN",
    "SIKKIM",
    "TAMIL NADU",
    "TELANGANA",
    "TRIPURA",
    "UTTAR PRADESH",
    "UTTARAKHAND",
    "WEST BENGAL",
    "ANDAMAN AND NICOBAR ISLANDS",
    "CHANDIGARH",
    "DADRA AND NAGAR HAVELI AND DAMAN AND DIU",
    "DELHI",
    "JAMMU AND KASHMIR",
    "LADAKH",
    "LAKSHADWEEP",
    "PUDUCHERRY",
]

STATE_PATTERN = _p(
    r"(?:STATE\s+OF\s+|GOVERNMENT\s+OF\s+)?(" + "|".join(s.replace(" ", r"[\s-]+") for s in INDIAN_STATES) + r")",
)


# ============================================================================
# 8. Document Type
# ============================================================================

# Broader/generic patterns first so they are checked before specific act/rule
# references in the text (e.g., "NOTIFICATION" heading should win over a
# passing reference to "the Food Safety and Standards Act, 2006").
# Notification match uses line-anchored patterns to avoid matching common
# English words like "notification" or "order" in body text.
DOCUMENT_TYPE_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("notification", _p(r"^\s*(?:NOTIFICATION|CIRCULAR|OFFICE\s+MEMORANDUM)\s*$"), "Notification"),
    ("order", _p(r"^\s*ORDER\s+NO\."), "Notification"),
    ("gazette", _p(r"(?:THE\s+)?GAZETTE\s+OF\s+INDIA|GAZETTE\s+NOTIFICATION"), "Gazette Notification"),
    ("judgment", _p(r"(?:JUDGMENT|ORDER|DECREE|AWARD)\s+(?:DATED|IN\s+THE\s+(?:SUPREME|HIGH)\s+COURT)"), "Judgment"),
    ("policy", _p(r"(?:NATIONAL\s+)?[A-Z][A-Z\s]*(?:POLICY|FRAMEWORK|STRATEGY|PLAN|MISSION)"), "Policy"),
    ("act", _p(r"(?:THE\s+)?[A-Z][A-Z\s&,(\)]*(?:ACT)\s*,\s*\d{4}"), "Act"),
    ("regulation", _p(r"(?:THE\s+)?[A-Z][A-Z\s&,(\)]*(?:REGULATION|REGULATIONS)\s*,\s*\d{4}"), "Regulation"),
    ("rule", _p(r"(?:THE\s+)?[A-Z][A-Z\s&,(\)]*(?:RULES?)\s*,\s*\d{4}"), "Rule"),
    ("bill", _p(r"(?:THE\s+)?[A-Z][A-Z\s&]*(?:BILL)\s*,\s*\d{4}"), "Bill"),
]


# ============================================================================
# 9. Amendment Status
# ============================================================================

AMENDMENT_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "original_act",
        _p(r"\b(?:ORIGINAL\s+(?:ACT|REGULATION|RULE)|AS\s+ENACTED|PRINCIPAL\s+(?:ACT|LEGISLATION))\b"),
        "Original",
    ),
    ("amended", _p(r"\b(?:AMENDED\s+(?:UP\s+TO|FROM\s+TIME\s+TO\s+TIME|BY|VIDE)|AS\s+AMENDED|AMENDMENT)\b"), "Amended"),
    ("repealed", _p(r"\b(?:REPEALED|REPEAL|RESCINDED|WITHDRAWN|ABOLISHED)\b"), "Repealed"),
    ("substituted", _p(r"\b(?:SUBSTITUTED|SUPERSEDED|REPLACED)\b"), "Superseded"),
    ("consolidated", _p(r"\b(?:CONSOLIDATED|CONSOLIDATION|COMPILATION)\b"), "Consolidated"),
    ("draft", _p(r"\b(?:DRAFT|PROPOSED|CONSULTATION\s+PAPER|TENTATIVE)\b"), "Draft"),
]


# ============================================================================
# 10. Section / Chapter References
# ============================================================================

SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("section", _p(r"\bS(?:ECTION|ec)?\.?\s*(\d+[A-Z]?)\b")),
    ("chapter", _p(r"\bC(?:HAPTER|hap\.?)?\.?\s*([IVXLCDM]+)\b")),
    ("schedule", _p(r"\b(?:SCHEDULE|SCH\.?|APPENDIX|ANNEXURE)\s+([IVXLCDM\d]+)\b")),
    ("rule_ref", _p(r"\bR(?:ULE)?\.?\s*(\d+[A-Z]?)\b")),
]

# ============================================================================
# 11. Country & Generic Location
# ============================================================================

COUNTRY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("india", _p(r"\b(?:INDIA|REPUBLIC\s+OF\s+INDIA|BHARAT|HINDUSTAN)\b")),
    (
        "other_common",
        _p(
            r"\b(?:UNITED\s+STATES|UNITED\s+KINGDOM|CANADA|AUSTRALIA|BANGLADESH|PAKISTAN|SRI\s+LANKA|NEPAL|BHUTAN|MYANMAR)\b",
        ),
    ),
]
