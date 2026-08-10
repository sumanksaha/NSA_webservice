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

# §2.4.1 fix (2026-08-09): instrument patterns (Act/Regulation/Rule/Bill) are
# checked FIRST.  Every regex candidate scores 0.90 and ``_deduplicate`` uses a
# STABLE sort by confidence, so list order = priority: a gazette that contains
# a real instrument title line (e.g. "FOOD SAFETY AND STANDARDS (ALCOHOLIC
# BEVERAGES) REGULATIONS, 2018") is now classified by the instrument, not by
# its publication wrapper (previously 20/24 real-corpus docs collapsed to
# "notification").
#
# Performance fix (2026-08-09 evening): the original title-case/all-caps word
# runs used NESTED lazy quantifiers ``(?:[A-Za-z0-9'&,()\- \t]*?[ \t]+)+``
# where the space char is BOTH inside the word class and the separator.  That
# is inherently ambiguous (the engine re-splits every space boundary), and on
# a token-dense line that does NOT match (e.g. a 63K-char regulation body)
# ``DocumentClassifier`` catastrophically backtracked — the corpus ingestion
# hung >25 minutes on one PDF.  The word runs are now FLATTENED to a fixed
# number of explicit ``X*?[ \t]`` groups (2 for the >=2-word guard, 1 for the
# all-caps lead-in) — the matched LANGUAGE is identical (X*? may still absorb
# spaces, so >=2 space chars == the old >=2 space-runs), but worst-case
# matching is now polynomial instead of exponential.  Verified against the
# pinned §2.4.1 tests and the real corpus.
#
# Each instrument pattern is line-anchored (``^`` ... ``$``), case-scoped via
# ``(?-i:)``, and the title/keyword text never spans lines (only ``[ \t]``
# inside the title, never ``\s``).  ``DocumentTypeExtractor`` matches these
# per-line (see ``extract``) so a pathological single line cannot blow up
# either.  Three branches cover the real title styles:
#
#  * title-case branch -- >=2 leading words + title-case keyword
#    ("Food Safety and Standards (Alcoholic Beverages) Regulations, 2018"):
#    the >=2-word guard rejects wrapped body fragments like "Standards Act,
#    2006" (a continuation of "...of the Food Safety and Standards Act, 2006")
#    which have only a one-word lead-in.
#  * all-caps branch -- 0-1 leading word group + ALL-CAPS keyword, so bare
#    wrapped-title fragments ("REGULATIONS 2011", "ACT, 2026") match while
#    lowercase body words never do.
#  * paren-tail branch (added 2026-08-09 evening) -- the line STARTS with one
#    or more words ending in ``)`` followed by the keyword: "Foods) Regulations,
#    2017." is the line-wrapped tail of "(Organic Foods) Regulations, 2017" in
#    gazettes whose title breaks across a parenthetical (Organic/Fortification
#    docs).  The keyword is type-specific (title-case or all-caps) so the
#    branch cannot cross-match types.  Verified risk-free on the corpus: the 5
#    must-stay-Notification docs have zero paren-tail lines.
#
# A trailing ``[ \t]*\.?[ \t]*$`` guard requires the year to END the line,
# rejecting wrapped preamble continuations ("Safety and Standards Act, 2006,
# the Food ...").  Mid-line references ("...section 92 of the Food Safety and
# Standards Act, 2006") cannot match because ``^`` anchors at the line start.
DOCUMENT_TYPE_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("act", _p(r"^\s*(?:(?-i:THE|The)[ \t]+)?(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t][A-Za-z0-9'&,()\- \t]*?[ \t](?-i:Act)|(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t]+)?(?-i:ACT)|(?:[A-Za-z0-9'&,\-]+(?:[ \t]+[A-Za-z0-9'&,\-]+)*\))[ \t]+(?:[A-Za-z0-9'&,()\-]+[ \t]+)*?(?-i:(?:Act|ACT)))[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$"), "Act"),
    ("regulation", _p(r"^\s*(?:(?-i:THE|The)[ \t]+)?(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t][A-Za-z0-9'&,()\- \t]*?[ \t](?-i:Regulations?)|(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t]+)?(?-i:REGULATIONS?)|(?:[A-Za-z0-9'&,\-]+(?:[ \t]+[A-Za-z0-9'&,\-]+)*\))[ \t]+(?:[A-Za-z0-9'&,()\-]+[ \t]+)*?(?-i:(?:Regulations?|REGULATIONS?)))[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$"), "Regulation"),
    ("rule", _p(r"^\s*(?:(?-i:THE|The)[ \t]+)?(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t][A-Za-z0-9'&,()\- \t]*?[ \t](?-i:Rules?)|(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t]+)?(?-i:RULES?)|(?:[A-Za-z0-9'&,\-]+(?:[ \t]+[A-Za-z0-9'&,\-]+)*\))[ \t]+(?:[A-Za-z0-9'&,()\-]+[ \t]+)*?(?-i:(?:Rules?|RULES?)))[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$"), "Rule"),
    ("bill", _p(r"^\s*(?:(?-i:THE|The)[ \t]+)?(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t][A-Za-z0-9'&,()\- \t]*?[ \t](?-i:Bill)|(?:(?-i:[A-Z])[A-Za-z0-9'&,()\- \t]*?[ \t]+)?(?-i:BILL)|(?:[A-Za-z0-9'&,\-]+(?:[ \t]+[A-Za-z0-9'&,\-]+)*\))[ \t]+(?:[A-Za-z0-9'&,()\-]+[ \t]+)*?(?-i:(?:Bill|BILL)))[ \t]*,?[ \t]*\d{4}[ \t\r]*\.?[ \t\r]*$"), "Bill"),
    ("judgment", _p(r"(?:JUDGMENT|ORDER|DECREE|AWARD)\s+(?:DATED|IN\s+THE\s+(?:SUPREME|HIGH)\s+COURT)"), "Judgment"),
    # Case-sensitive + line-anchored + word-boundaried (evaluated 2026-08-09
    # against the FSSAI corpus): the module compiles patterns with IGNORECASE,
    # so without the ``(?-i:)`` scope body text like "...the Commission" or
    # "evaluating policy" would label a document Policy.  Only a proper
    # uppercase/title-case heading ("National Food Policy...") matches now.
    # Policy maps to "" in the §5.1 enum, so false positives merely shadow the
    # real Act/Regulation label.
    ("policy", _p(r"(?-i:^\s*(?:NATIONAL\s+)?[A-Z][A-Z\s]*\b(?:POLICY|FRAMEWORK|STRATEGY|PLAN|MISSION)\b)"), "Policy"),
    # Generic publication-format patterns LAST (§2.4.1): they only win when no
    # instrument title line is present.  Notification uses line-anchored
    # patterns to avoid matching common English words like "notification" or
    # "order" in body text.
    ("notification", _p(r"^\s*(?:NOTIFICATION|CIRCULAR|OFFICE\s+MEMORANDUM)\s*$"), "Notification"),
    ("order", _p(r"^\s*ORDER\s+NO\."), "Notification"),
    ("gazette", _p(r"(?:THE\s+)?GAZETTE\s+OF\s+INDIA|GAZETTE\s+NOTIFICATION"), "Gazette Notification"),
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
