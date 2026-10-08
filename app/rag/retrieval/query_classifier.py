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
from typing import Any, ClassVar, Protocol

logger = logging.getLogger(__name__)


class QueryType(StrEnum):
    """Classification categories — expanded for intelligence-layer reasoning (2.2)."""

    IDENTIFICATION = "identification"
    LOOKUP = "lookup"
    DEFINITION = "definition"
    PROHIBITION = "prohibition"
    DUTY = "duty"
    RIGHT = "right"
    POWER = "power"
    PENALTY = "penalty"
    EXCEPTION = "exception"
    PROCEDURE = "procedure"
    APPLICABILITY = "applicability"
    COMPARISON = "comparison"
    TEMPORAL = "temporal"
    JURISDICTION = "jurisdiction"
    CROSS_REFERENCE = "cross_reference"
    CASE_LAW = "case_law"
    MULTI_HOP = "multi_hop"
    AUTHORITY = "authority"
    FACT_PATTERN = "fact_pattern"
    COMPLIANCE_ASSESSMENT = "compliance_assessment"
    # Legacy compatibility
    SECTION_LOOKUP = "section_lookup"
    PROVISION_SEARCH = "provision_search"
    GENERAL_QA = "general_qa"
    AMENDMENT_QUERY = "amendment_query"


# Triage map: keyword → QueryType for the 18+ intelligence-layer types.
# Used by classify_intent() to route queries to the correct retrieval strategy.
CLASSIFIER_TRIAGE: dict[str, QueryType] = {
    "identification": QueryType.IDENTIFICATION,
    "lookup": QueryType.LOOKUP,
    "definition": QueryType.DEFINITION,
    "prohibition": QueryType.PROHIBITION,
    "duty": QueryType.DUTY,
    "right": QueryType.RIGHT,
    "power": QueryType.POWER,
    "penalty": QueryType.PENALTY,
    "exception": QueryType.EXCEPTION,
    "procedure": QueryType.PROCEDURE,
    "applicability": QueryType.APPLICABILITY,
    "comparison": QueryType.COMPARISON,
    "temporal": QueryType.TEMPORAL,
    "jurisdiction": QueryType.JURISDICTION,
    "cross_reference": QueryType.CROSS_REFERENCE,
    "case_law": QueryType.CASE_LAW,
    "multi_hop": QueryType.MULTI_HOP,
    "fact_pattern": QueryType.FACT_PATTERN,
    "compliance_assessment": QueryType.COMPLIANCE_ASSESSMENT,
}


# Full FSS Act, 2006 section coverage (expanded from the codebase's KNOWN_SECTIONS).
# Source: The Food Safety and Standards Act, 2006 (as amended) — sections 1–104.
# Canonical source is app/rag/legal_sections.py (multi-domain registry);
# re-exported here for backward compatibility.
from app.rag.legal_sections import FSS_ACT_SECTIONS  # noqa: F401  (re-export)

#: Regex patterns for query classification (ordered by priority).
#: Order matters: more specific types checked first; broader types last.
#: See also: legal_query_classifier.py _TYPE_PATTERNS for 14-type legal classifier.
_QUERY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Amendment queries — must be checked before section lookup
    ("amendment", re.compile(r"\bamend|amendment|substitute|inserted|added|repeal|repealed", re.IGNORECASE)),
    ("section", re.compile(r"\bsection\s*\d{1,3}\b|\bu/s\b|\bsec\.\s*\d{1,3}\b|s\s*\d{1,3}", re.IGNORECASE)),
    (
        "case_law",
        re.compile(
            r"\b\d{4}\s*(?:SCC|SCR|SC|AIR|ILR|SCALE|All\s*ER|Cr|SLR|MLT|Comp\s*Cas)\b"
            r"|\bAIR\s+\d+\b"
            r"|\bSupreme\s*Court\b"
            r"|\bHigh\s*Court\b"
            r"|\b(?:v\.|vs\.|versus)\s",
            re.IGNORECASE,
        ),
    ),
    # Procedure queries — include form references, seizure, steps, appeal
    (
        "procedure",
        re.compile(
            r"\b(?:which|what)\s+(?:is\s+the\s+)?(?:form|forms)\b"
            r"|\bhow\s+(?:can|to|does|do|should)\b"
            r"|\bwhat\s+happens\s+(?:after|when|if)\b"
            r"|\bprocedure\b"
            r"|\bappeal(?:s|ing|ed)?\b"
            r"|\bsteps?\b"
            r"|\bseiz(?:e|ure|ing)\b"
            r"|\bform\s+(?:ii|iii|iv|v|vi|vii|viii)\b"
            r"|\b(document|order|sequence)\b",
            re.IGNORECASE,
        ),
    ),
    # Provision / regulation queries
    (
        "provision",
        re.compile(
            r"\b(fss\s*act|food\s*safety\s*and\s*standards\s*act|fssa|regulation|sub[-\s]?regulation)", re.IGNORECASE
        ),
    ),
    # Penalty queries — fines, imprisonment, penalty amounts
    (
        "penalty",
        re.compile(
            r"\b(penalty|penalties|fine|imprison|punish)\b"
            r"|\brs\.?\s*\d+[,\d]*\s*(?:per|for|each|or)\b"
            r"|\bwith\s+imprisonment\b"
            r"|\bpunishable\b"
            r"|\bmonetary\s+penalty\b",
            re.IGNORECASE,
        ),
    ),
    # Prohibition queries — what is prohibited, restrictions
    (
        "prohibition",
        re.compile(
            r"\b(prohibit|prohibition|prohibited)\b"
            r"|\bshall\s+not\b"
            r"|\bno\s+(?:person|food\s*business)\b"
            r"|\brestrict\b"
            r"|\bprohibition\s+order\b",
            re.IGNORECASE,
        ),
    ),
    # Authority queries — officers, boards, agencies
    (
        "authority",
        re.compile(
            r"\b(officer|auth|authority|board|agency|commission|tribunal|designated\s+officer|fso|food\s+analyst|appellate)\b"
            r"|\bempowered\b"
            r"|\bprincipal\s+enforcement\b",
            re.IGNORECASE,
        ),
    ),
    # Cross-reference queries — "as provided under", "read with", "schedule"
    (
        "cross_reference",
        re.compile(
            r"\b(referred\s+to\s+in|read\s+with|as\s+provided\s+under|as\s+per|schedule\s+[a-z]?\d+|rule\s+\d+|shall\s+apply\s+in\s+accordance|means\s+and\s+includes|notwithstanding\s+anything\s+contained|subject\s+to\s+the\s+provisions|explained\s+in)\b",
            re.IGNORECASE,
        ),
    ),
    # Definition queries — "means", "for the purposes of", definitions
    (
        "definition",
        re.compile(
            r"""['"][^'"]{1,60}['"]\s+means\b"""
            r"|\bdefine\b"
            r"|\bdefinition\b"
            r"|\bfor\s+the\s+purposes\b"
            r"|\bshall\s+have\s+the\s+meaning\b"
            r"|\brefer(?:s)?\s+to\b"
            r"|\bwhat\s+is\s+meant\s+by\b"
            r"|\bmeaning\s+of\b",
            re.IGNORECASE,
        ),
    ),
    # Obligation queries — duties, responsibilities, "must", "required"
    # NOTE: ``shall`` is deliberately EXCLUDED — it is too common and would
    # swallow procedural, penalty, and prohibition queries.
    (
        "obligation",
        re.compile(
            r"\b(responsibility|duty|obligation|must|required|expected|establish)\b",
            re.IGNORECASE,
        ),
    ),
    # General fallback
    ("general", re.compile(r".+", re.IGNORECASE)),
]


class QueryClassifier:
    """Rule-based query classifier for the FSS Act legal corpus.

    Classification priority: amendment → section → case law → penalty →
    prohibition → authority → cross_reference → definition → obligation →
    procedure → provision → general.

    Stateless and safe to share across requests/threads.

    Note: the legacy 5-way view (amendment, section, case_law, provision,
    general) is preserved for backward compatibility.  New types are mapped
    to their closest legacy equivalent where no exact mapping exists.
    """

    _LABEL_TO_TYPE: ClassVar[dict[str, QueryType]] = {
        "amendment": QueryType.AMENDMENT_QUERY,
        "section": QueryType.SECTION_LOOKUP,
        "case_law": QueryType.CASE_LAW,
        "penalty": QueryType.PENALTY,
        "prohibition": QueryType.PROHIBITION,
        "authority": QueryType.AUTHORITY,
        "cross_reference": QueryType.CROSS_REFERENCE,
        "definition": QueryType.DEFINITION,
        "obligation": QueryType.DUTY,
        "procedure": QueryType.PROCEDURE,
        "provision": QueryType.PROVISION_SEARCH,
        "general": QueryType.GENERAL_QA,
    }

    def classify(self, query: str) -> QueryType:
        if not query or not query.strip():
            return QueryType.GENERAL_QA
        for label, pattern in _QUERY_PATTERNS:
            if pattern.search(query):
                return self._LABEL_TO_TYPE.get(label, QueryType.GENERAL_QA)
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
    "FSSAI",
    "Food Safety and Standards Authority of India",
    "Ministry of Health",
    "Ministry of Health and Family Welfare",
    "MoHFW",
    "Ministry of Environment",
    "Ministry of Commerce",
    "Central Government",
    "State Government",
    "National Green Tribunal",
    "Supreme Court",
    "High Court",
    "Food Safety and Standards Appellate Tribunal",
    "FSSAT",
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


def _fuzzy_match_authority(query: str, authorities: frozenset[str], threshold: int = 2) -> str | None:
    """Find authority by fuzzy string match (edit distance).

    Matches authority names that are close but not exact — e.g. "FSS A" -> "FSSAI",
    "Ministry of Health and Family Welfare" -> "Ministry of Health and Family Welfare".

    Args:
        query: User query text
        authorities: Set of known authority names
        threshold: Maximum edit distance to consider a match

    Returns:
        Matching authority name or None
    """
    q = query.lower()
    for auth in authorities:
        # Edit distance (Levenshtein) between the authority and query substrings
        # Use simple heuristic: check if authority is a close substring match
        auth_lower = auth.lower()
        # Direct substring match (already handled by caller, but keep for safety)
        if auth_lower in q:
            return auth
        # Check if query contains a token that closely matches the authority
        # Simple approach: check character-level overlap
        # For short authority names, use edit distance
        if len(auth_lower) <= 5:
            # For short names like "FSSAI", "MoHFW", check if characters are close
            for word in q.split():
                if _edit_distance(word, auth_lower) <= threshold:
                    return auth
    return None


def _edit_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


# Authority aliases — common abbreviations/misspellings -> canonical name
_AUTHORITY_ALIASES: dict[str, str] = {
    "central government of india": "Central Government",
    "state government of india": "State Government",
    "govt": "Government",
}


class AuthorityQueryParser:
    """Parse authority references from a query.

    Examples::
        "Ministry of Health notification on food labeling"
        -> {"authority": "Ministry of Health"}

    Uses multiple strategies:
    1. Exact match against known authority names
    2. Fuzzy match (edit distance) for typos/abbreviations
    3. Regex match for ministry patterns
    4. Alias-based match for common abbreviations
    """

    @staticmethod
    def parse(query: str) -> dict[str, Any]:
        # 1. Exact match against known authority names (longest first)
        for auth in sorted(_KNOWN_AUTHORITIES, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(auth) + r"\b", re.IGNORECASE)
            if pattern.search(query):
                return {"authority": auth}

        # 2. Alias-based match
        q_lower = query.lower()
        for alias, canonical in sorted(_AUTHORITY_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
            if alias in q_lower:
                return {"authority": canonical}

        # 3. Regex match for ministry patterns
        ministry_re = re.compile(
            r"\b(ministry\s+of\s+[a-z\s]+?|central\s+government|state\s+government)"
            r"(?:\s+notification|\s+order|\s+guideline|\s+circular)?",
            re.IGNORECASE,
        )
        m = ministry_re.search(query)
        if m:
            return {"authority": m.group(1).strip()}

        # 4. Fuzzy match for partial authority names
        matched = _fuzzy_match_authority(query, _KNOWN_AUTHORITIES)
        if matched:
            return {"authority": matched}

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

        "What is FSS Act applicability in UP?"
        -> {"jurisdiction": "Uttar Pradesh", "level": "state"}
    """

    _INDIAN_STATES = frozenset({
        "andhra pradesh",
        "telangana",
        "karnataka",
        "kerala",
        "tamil nadu",
        "maharashtra",
        "gujarat",
        "rajasthan",
        "uttar pradesh",
        "bihar",
        "west bengal",
        "punjab",
        "haryana",
        "delhi",
        "uttarakhand",
        "himachal pradesh",
        "jammu and kashmir",
        "ladakh",
        "chhattisgarh",
        "odisha",
        "jharkhand",
        "madhya pradesh",
        "assam",
        "meghalaya",
        "manipur",
        "mizoram",
        "nagaland",
        "tripura",
        "goa",
        "chandigarh",
        "dadra and nagar haveli",
        "daman and diu",
        "andaman and nicobar",
        "puducherry",
        "lakshadweep",
    })

    #: Common abbreviations and aliases for Indian states
    _STATE_ALIASES: ClassVar[dict[str, str]] = {
        "up": "uttar pradesh",
        "mh": "maharashtra",
        "gj": "gujarat",
        "rj": "rajasthan",
        "mp": "madhya pradesh",
        "tg": "telangana",
        "ap": "andhra pradesh",
        "tn": "tamil nadu",
        "wb": "west bengal",
        "dl": "delhi",
        "ka": "karnataka",
        "kl": "kerala",
        "pb": "punjab",
        "hr": "haryana",
        "uk": "uttarakhand",
        "hp": "himachal pradesh",
        "jk": "jammu and kashmir",
        "cg": "chhattisgarh",
        "od": "odisha",
        "jh": "jharkhand",
    }

    @staticmethod
    def parse(query: str) -> dict[str, Any]:
        query_lower = query.lower()

        # 1. Full state name match (longest first)
        for state in sorted(JurisdictionQueryParser._INDIAN_STATES, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(state) + r"\b", re.IGNORECASE)
            if pattern.search(query):
                return {"jurisdiction": state.title(), "level": "state"}

        # 2. State abbreviation / alias match
        for alias, full_name in sorted(
            JurisdictionQueryParser._STATE_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            pattern = re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE)
            if pattern.search(query):
                return {"jurisdiction": full_name.title(), "level": "state"}

        # 3. Central / national jurisdiction
        if re.search(r"\b(central government|national|central|india|federal)\b", query_lower):
            return {"jurisdiction": "India", "level": "central"}

        return {}


class _SubQueryParser(Protocol):
    """Structural type for the section/authority/case-law/jurisdiction parsers."""

    @staticmethod
    def parse(query: str) -> dict[str, Any]: ...


class QueryParser:
    """Dispatch query parsing to the appropriate sub-parser based on QueryType.

    Maps each QueryType to the parser that best extracts structured filters
    for that query type.  Types without a dedicated parser fall back to
    AuthorityQueryParser or SectionQueryParser based on the query's expected
    structure.
    """

    _PARSERS: ClassVar[dict[QueryType, type[_SubQueryParser]]] = {
        # Section-related queries -> SectionQueryParser
        QueryType.SECTION_LOOKUP: SectionQueryParser,
        QueryType.AMENDMENT_QUERY: SectionQueryParser,
        # Authority/organization queries -> AuthorityQueryParser
        QueryType.AUTHORITY: AuthorityQueryParser,
        QueryType.PROVISION_SEARCH: AuthorityQueryParser,
        QueryType.IDENTIFICATION: AuthorityQueryParser,
        QueryType.LOOKUP: AuthorityQueryParser,
        # Case-law queries -> CaseLawQueryParser
        QueryType.CASE_LAW: CaseLawQueryParser,
        # Jurisdiction queries -> JurisdictionQueryParser
        QueryType.JURISDICTION: JurisdictionQueryParser,
        # Types that can appear with section references -> SectionQueryParser
        QueryType.PENALTY: SectionQueryParser,
        QueryType.PROHIBITION: SectionQueryParser,
        QueryType.DUTY: SectionQueryParser,
        QueryType.POWER: SectionQueryParser,
        QueryType.RIGHT: SectionQueryParser,
        QueryType.EXCEPTION: SectionQueryParser,
        QueryType.PROCEDURE: SectionQueryParser,
        QueryType.APPLICABILITY: SectionQueryParser,
        QueryType.COMPARISON: SectionQueryParser,
        QueryType.CROSS_REFERENCE: SectionQueryParser,
        QueryType.TEMPORAL: SectionQueryParser,
        QueryType.MULTI_HOP: SectionQueryParser,
        QueryType.FACT_PATTERN: SectionQueryParser,
        QueryType.COMPLIANCE_ASSESSMENT: SectionQueryParser,
        QueryType.DEFINITION: SectionQueryParser,
        # General queries -> AuthorityQueryParser (broadest capture)
        QueryType.GENERAL_QA: AuthorityQueryParser,
    }

    def parse(self, query: str, query_type: QueryType) -> dict[str, Any]:
        parser_cls = self._PARSERS.get(query_type, AuthorityQueryParser)
        return parser_cls.parse(query)
