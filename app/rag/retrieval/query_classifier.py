"""Query classification & structured query parsing for the RAG pipeline.

``QueryClassifier`` is a **rule-based** classifier (not LLM-based) so it works
without API keys and stays deterministic for legal queries.  It classifies
user queries into :class:`QueryType` and provides structured parsers that
extract section numbers, authorities, case-law references, and jurisdictions.

Patterns are adapted from:
- ``app/cross_reference/engine.py`` — ``KNOWN_SECTIONS`` and ``_SECTION_RUN_RE``
  (regex patterns for section reference extraction, R1 adaptation)
- ``app/metadata_extractor/extractors/regex.py`` — regex-based field
  extraction pattern (R2 conceptual reuse)
- ``app/metadata_extractor/validation.py`` — cross-field consistency rules
  (R2 conceptual reuse)

The FSS Act, 2006 section set is expanded from the codebase's
``KNOWN_SECTIONS`` (12 entries) to full Act coverage (sections 1–104) as
required by ``RAG_AGENT_B_SCOPE.md`` §4 Day 1 warning #5.
"""

from __future__ import annotations

import logging
import re
from enum import StrEnum
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class QueryType(StrEnum):
    """Classification categories for user queries."""

    SECTION_LOOKUP = "section_lookup"
    CASE_LAW = "case_law"
    PROVISION_SEARCH = "provision_search"
    GENERAL_QA = "general_qa"
    AMENDMENT_QUERY = "amendment_query"


# Full FSS Act, 2006 section coverage (expanded from the codebase's KNOWN_SECTIONS).
# Source: The Food Safety and Standards Act, 2006 (as amended) — sections 1–104.
# Canonical source is app/rag/legal_sections.py (multi-domain registry);
# re-exported here for backward compatibility.
from app.rag.legal_sections import FSS_ACT_SECTIONS  # noqa: F401  (re-export)

#: Regex patterns for query classification (ordered by priority).
_QUERY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Amendment queries — must be checked before section lookup
    ("amendment", re.compile(r"\bamend|amendment|substitute|inserted|added|repeal|repealed", re.IGNORECASE)),
    ("section", re.compile(r"\bsection\s*\d{1,3}\b|\bu/s\b|\bsec\.\s*\d{1,3}\b|s\s*\d{1,3}", re.IGNORECASE)),
    ("case_law", re.compile(
        r"\b\d{4}\s*(?:SCC|SCR|SC|AIR|ILR|SCALE|All\s*ER|Cr|SLR|MLT|Comp\s*Cas)\b"
        r"|\bAIR\s+\d+\b"
        r"|\bSupreme\s*Court\b"
        r"|\bHigh\s*Court\b"
        r"|\b(?:v\.|vs\.|versus)\s",
        re.IGNORECASE,
    )),
    ("provision", re.compile(r"\b(fss\s*act|food\s*safety\s*and\s*standards\s*act|fssa|regulation|sub[-\s]?regulation)", re.IGNORECASE)),
]


class QueryClassifier:
    """Rule-based query classifier for the FSS Act legal corpus.

    Classification priority: amendment → section → case law → provision → general.
    Stateless and safe to share across requests/threads.
    """

    def classify(self, query: str) -> QueryType:
        if not query or not query.strip():
            return QueryType.GENERAL_QA
        for label, pattern in _QUERY_PATTERNS:
            if pattern.search(query):
                if label == "amendment":
                    return QueryType.AMENDMENT_QUERY
                if label == "section":
                    return QueryType.SECTION_LOOKUP
                if label == "case_law":
                    return QueryType.CASE_LAW
                if label == "provision":
                    return QueryType.PROVISION_SEARCH
        return QueryType.GENERAL_QA

# ---------------------------------------------------------------------------
# Query parsers — extract structured filters from a classified query
# ---------------------------------------------------------------------------

# Matches: "Section 55", "Sec. 32", "u/s 55", "s. 55", "section 55(2)"
_SECTION_NUMBER_RE = re.compile(
    r"\b(?:section|sec\.|s\.|u/s)\s*(\d{1,3})(?:\((\d+)\))?",
    re.IGNORECASE,
)

# Matches: "Sections 55, 56 and 58" — a run of section numbers
_SECTION_RUN_RE = re.compile(
    r"\b(?:sections?|secs?)\s+(\d{1,3}(?:\s*[,&and-]+\s*\d{1,3})*)",
    re.IGNORECASE,
)

# Matches: "Sub-section (2) of Section 55"
_SUBSECTION_RE = re.compile(
    r"\bsub[-\s]?section\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Known Indian legal authorities / ministries that issue notifications
_KNOWN_AUTHORITIES = frozenset({
    "FSSAI", "Food Safety and Standards Authority of India",
    "Ministry of Health", "Ministry of Health and Family Welfare",
    "MoHFW", "Ministry of Environment", "Ministry of Commerce",
    "Central Government", "State Government",
    "National Green Tribunal", "Supreme Court", "High Court",
    "Food Safety and Standards Appellate Tribunal", "FSSAT",
})


class SectionQueryParser:
    """Parse section-lookup queries into structured section filters.

    Examples::
        "What does Section 55 say?"           -> {"section_number": "55"}
        "Section 55(2) of the FSS Act"        -> {"section_number": "55", "subsection": "2"}
        "Sections 55, 56 and 58"              -> {"section_numbers": ["55", "56", "58"]}
    """

    @staticmethod
    def parse(query: str) -> dict[str, Any]:
        result: dict[str, Any] = {}

        # Multi-section run: "Sections 55, 56 and 58"
        run_match = _SECTION_RUN_RE.search(query)
        if run_match:
            numbers = re.findall(r"\d{1,3}", run_match.group(1))
            if numbers:
                result["section_numbers"] = numbers

        # Single section with optional subsection: "Section 55(2)"
        single_match = _SECTION_NUMBER_RE.search(query)
        if single_match:
            num = single_match.group(1)
            result["section_number"] = num
            if single_match.lastindex and single_match.lastindex >= 2 and single_match.group(2):
                result["subsection"] = single_match.group(2)

        # Sub-section mention: "sub-section (2)"
        ss_match = _SUBSECTION_RE.search(query)
        if ss_match:
            result["subsection"] = ss_match.group(1)

        return result


class AuthorityQueryParser:
    """Parse authority references from a query.

    Examples::
        "Ministry of Health notification on food labeling"
        -> {"authority": "Ministry of Health"}
    """

    @staticmethod
    def parse(query: str) -> dict[str, Any]:
        for auth in _KNOWN_AUTHORITIES:
            pattern = re.compile(r"\b" + re.escape(auth) + r"\b", re.IGNORECASE)
            if pattern.search(query):
                return {"authority": auth}

        # Fuzzy authority match
        ministry_re = re.compile(
            r"\b(ministry\s+of\s+[a-z\s]+?|central\s+government|state\s+government)"
            r"(?:\s+notification|\s+order|\s+guideline|\s+circular)?",
            re.IGNORECASE,
        )
        m = ministry_re.search(query)
        if m:
            return {"authority": m.group(1).strip()}

        return {}


class CaseLawQueryParser:
    """Parse case-law citations from a query.

    Examples::
        "What did the Supreme Court say in 2023 S.C.C. 123?"
        -> {"citation": "2023 S.C.C. 123", "court": "Supreme Court"}
    """

    _CASE_CITATION_RE = re.compile(
        r"\b(\d{4})\s*((?:SCC|SC|AIR|ILR|SCALE|All\s*ER|Cr|SLR|MLT|Comp\s*Cas))"
        r"\s+(\d+(?:\s*\d+)?)",
        re.IGNORECASE,
    )

    @staticmethod
    def parse(query: str) -> dict[str, Any]:
        result: dict[str, Any] = {}

        cit_match = CaseLawQueryParser._CASE_CITATION_RE.search(query)
        if cit_match:
            result["citation"] = f"{cit_match.group(1)} {cit_match.group(2)} {cit_match.group(3)}"

        court_match = re.search(r"\b(Supreme\s*Court|High\s*Court)\b", query, re.IGNORECASE)
        if court_match:
            result["court"] = court_match.group(1)

        return result


class JurisdictionQueryParser:
    """Parse jurisdiction references from a query.

    Examples::
        "Maharashtra food safety rules"
        -> {"jurisdiction": "Maharashtra", "level": "state"}
    """

    _INDIAN_STATES = frozenset({
        "andhra pradesh", "telangana", "karnataka", "kerala", "tamil nadu",
        "maharashtra", "gujarat", "rajasthan", "uttar pradesh", "bihar",
        "west bengal", "punjab", "haryana", "delhi", "uttarakhand",
        "himachal pradesh", "jammu and kashmir", "ladakh", "chhattisgarh",
        "odisha", "jharkhand", "madhya pradesh", "assam", "meghalaya",
        "manipur", "mizoram", "nagaland", "tripura", "goa",
        "chandigarh", "dadra and nagar haveli", "daman and diu",
        "andaman and nicobar", "puducherry", "lakshadweep",
    })

    @staticmethod
    def parse(query: str) -> dict[str, Any]:
        query_lower = query.lower()

        for state in JurisdictionQueryParser._INDIAN_STATES:
            pattern = re.compile(r"\b" + re.escape(state) + r"\b", re.IGNORECASE)
            if pattern.search(query):
                return {"jurisdiction": state.title(), "level": "state"}

        if re.search(r"\b(central government|national|central|india|federal)\b", query_lower):
            return {"jurisdiction": "India", "level": "central"}

        return {}


class QueryParser:
    """Dispatch query parsing to the appropriate sub-parser based on QueryType."""

    _PARSERS: ClassVar[dict[QueryType, type]] = {
        QueryType.SECTION_LOOKUP: SectionQueryParser,
        QueryType.AMENDMENT_QUERY: SectionQueryParser,
        QueryType.PROVISION_SEARCH: AuthorityQueryParser,
        QueryType.CASE_LAW: CaseLawQueryParser,
        QueryType.GENERAL_QA: AuthorityQueryParser,
    }

    def parse(self, query: str, query_type: QueryType) -> dict[str, Any]:
        parser_cls = self._PARSERS.get(query_type, AuthorityQueryParser)
        return parser_cls.parse(query)
