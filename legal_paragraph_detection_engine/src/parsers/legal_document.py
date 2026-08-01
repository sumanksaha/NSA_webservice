"""Legal document type recognition module

Identifies and categorizes different types of Indian legal documents.
"""

import re
from enum import Enum
from typing import ClassVar

from ..utils.cache import evict_if_full, stable_key


class LegalDocumentType(Enum):
    """Types of Indian legal documents."""

    ACT = "act"
    RULE = "rule"
    REGULATION = "regulation"
    NOTIFICATION = "notification"
    CIRCULAR = "circular"
    GOVERNMENT_ORDER = "government_order"
    ORDINANCE = "ordinance"
    BILL = "bill"
    AMENDMENT = "amendment"
    PANCHAYATI_RAJ_ACT = "panchayati_raj_act"
    MUNICIPAL_ACT = "municipal_act"
    SPECIAL_ACT = "special_act"
    REPORT = "report"
    INSPECTION_REPORT = "inspection_report"
    UNKNOWN = "unknown"


class LegalDocument:
    """Represents a legal document with metadata."""

    def __init__(
        self,
        doc_type: LegalDocumentType,
        title: str | None = None,
        source: str | None = None,
        year: int | None = None,
        jurisdiction: str | None = None,
    ):
        self.type = doc_type
        self.title = title
        self.source = source
        self.year = year
        self.jurisdiction = jurisdiction


class DocumentTypeClassifier:
    """Classifies legal documents based on their content and structure."""

    # Pattern definitions for document type recognition
    # Order: Specific patterns first, generic patterns last with negative lookaheads
    TYPE_PATTERNS: ClassVar[dict[LegalDocumentType, list[str]]] = {
        # Report documents — checked before ACT so a report quoting "FSS Act, 2006"
        # is not mislabelled as the Act itself (T-46d / v6.6). Accepted tradeoff:
        # the generic patterns ("Test Report", "Report dated…") may label a
        # document that merely *references* a report as ``report`` — acceptable
        # best-effort metadata since a caller-supplied hint stays authoritative.
        LegalDocumentType.INSPECTION_REPORT: [
            r"\bInspection\s+Report\b",
            r"\bInspection\s+Findings?\b",
            r"\bReport\s+of\s+Inspection\b",
        ],
        LegalDocumentType.REPORT: [
            r"\bAnalysis\s+Report\b",
            r"\bAnalyst'?s?\s+Report\b",
            r"\bReport\s+of\s+the\s+(?:Public\s+)?Analyst\b",
            r"\bLaboratory\s+(?:Test\s+)?Report\b",
            r"\bTest\s+Report\b",
            r"\bReport\s+dated\b",
        ],
        LegalDocumentType.PANCHAYATI_RAJ_ACT: [
            r"Panchayati [Rr]aj",
            r"\bPanchayati [Rr]aj [Aa]ct",
            r"Rural [Dd]evelopment",
        ],
        LegalDocumentType.MUNICIPAL_ACT: [
            r"Municipal [Aa]ct",
            r"\bMunicipal [Cc]orporation",
            r"Local [Gg]overnment",
            r"Urban [Dd]evelopment",
        ],
        LegalDocumentType.SPECIAL_ACT: [
            r"Special [Aa]ct",
            r"\bSpecial\b[^.\n]*\b[Aa]ct\b",
            r"Specific [Ll]egislation",
        ],
        LegalDocumentType.NOTIFICATION: [
            r"\bNotification\b",
            r"Public\s+Notification",
            r"\bNotified\b",
            r"Public Notice",
            r"Official Notice",
            r"This is to notify",
            r"Notice in pursuance",
        ],
        LegalDocumentType.ACT: [
            r"^\s*An Act to",
            r"^\s*The [A-Z].* Act",
            r"\bAct\b(?!\s*,\s*\d{4})(?!\b(?:Municipal|Panchayati Raj|Special)\b).*\d{4}",
            r"^\s*Short Title:",
            r"^\s*An Act of",
        ],
        LegalDocumentType.RULE: [
            r"^\s*Rules under",
            r"^\s*The [A-Z].* Rules",
            r"\bRules\b.*\d{4}",
            r"Made under section",
            r"Regulated by",
        ],
        LegalDocumentType.REGULATION: [
            r"^\s*Regulations?",
            r"Regulatory [Mm]easures",
            r"Compliance [Rr]equirements",
            r"Operational [Gg]uidelines",
        ],
        LegalDocumentType.CIRCULAR: [
            r"^Circular",
            r"Department [Cc]ircular",
            r"Office [Cc]ircular",
            r"Administrative [Cc]ircular",
            r"All [Cc]ircular[s]",
        ],
        LegalDocumentType.GOVERNMENT_ORDER: [
            r"^G.O",
            r"Government [Oo]rder",
            r"G.O.(?:No\.)?\s*\d+",
            r"Executive [Oo]rder",
            r"Government [Mm]andate",
        ],
        LegalDocumentType.ORDINANCE: [
            r"^Ordinance",
            r"Emergency [Oo]rdinance",
            r"Promulgated [Oo]rdinance",
            r"Ordinance [Nn]o.",
        ],
        LegalDocumentType.BILL: [
            r"^Bill",
            r"\bBill\b.*\d{4}",
            r"Introduced [Bb]ill",
            r"Proposed [Bb]ill",
            r"\bBill\s*for\s*the",
        ],
        LegalDocumentType.AMENDMENT: [
            r"^Amendment",
            r"\bAmendment\b.*\d{4}",
            r"Amendment [Nn]o.",
            r" Amended by",
            r"\bAct\s*[A-Z]\s*amended",
        ],
    }

    # Aliases that map free-form hints (e.g. the case-file/adjudication templates'
    # "Analysis Report" / "Inspection Report") to canonical LegalDocumentType values.
    DOC_TYPE_ALIASES: ClassVar[dict[str, str]] = {
        "analysis report": "report",
        "analyst report": "report",
        "analyst's report": "report",
        "report": "report",
        "inspection report": "inspection_report",
        "inspection findings": "inspection_report",
    }

    # Jurisdiction patterns
    JURISDICTION_PATTERNS: ClassVar[dict[str, list[str]]] = {
        "central": [
            r"Government of India",
            r"Ministry of",
            r"Union of India",
            r"Central [Gg]overnment",
            r"New Delhi",
            r"India",
        ],
        "state": [
            r"Government of",
            r"State of",
            r"[A-Z][a-z]+ [Ss]tate",
            r"Shri [A-Z][a-z]+ [Gg]overnment",
        ],
        "local": [
            r"Municipal",
            r"Panchayat",
            r"Zilla Parishad",
            r"Block Panchayat",
        ],
    }

    # Title extraction patterns
    # The legal document type word is captured *inside* the group so titles like
    # "The Food Safety Act: ..." keep the word "Act" (F-07d / T-14). The optional
    # subtitle part handles "Act: Licensing and Registration" forms.
    TITLE_PATTERNS: ClassVar[list[str]] = [
        r"^([A-Z][A-Za-z0-9,. -]*?(?:Act|Rules|Regulation|Notification|Circular|Order|Bill|Amendment|Ordinance)(?::\s*[A-Za-z0-9,. -]*)?)\s*$",
        r"^([A-Z][A-Za-z0-9,. -]{10,100})\s*$",  # Fallback for titles without specific type
    ]

    # Year patterns
    YEAR_PATTERNS: ClassVar[list[str]] = [
        r"\b(\d{4})\s*\(.*?\)",
        r"\b(\d{4})\s*for",
        r"\b(\d{4})\s*to",
        r"\b(\d{4})\s*ad",
        r"\b(\d{4})\b",
    ]

    def __init__(self):
        self.document_cache: dict[str, LegalDocument] = {}

    def classify_document(self, text: str) -> LegalDocument:
        """Classify a legal document based on its content.

        Args:
            text: Clean legal document text

        Returns:
            Classified LegalDocument object
        """
        # Create a normalized key for caching
        normalized_text = re.sub(r"\s+", " ", text.strip())
        text_hash = stable_key(normalized_text)

        if text_hash in self.document_cache:
            return self.document_cache[text_hash]

        # Detect document type
        doc_type = self._detect_type(text)

        # Extract metadata
        title = self._extract_title(text)
        year = self._extract_year(text)
        jurisdiction = self._detect_jurisdiction(text)

        document = LegalDocument(
            doc_type=doc_type,
            title=title,
            source="unknown",
            year=year,
            jurisdiction=jurisdiction,
        )

        evict_if_full(self.document_cache)
        self.document_cache[text_hash] = document
        return document

    def _detect_type(self, text: str) -> LegalDocumentType:
        """Detect the type of legal document."""
        # Check patterns in order of specificity
        for doc_type, patterns in self.TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text[:500], re.IGNORECASE):
                    return doc_type

        return LegalDocumentType.UNKNOWN

    def _extract_title(self, text: str) -> str | None:
        """Extract the title of the legal document."""
        # Look for title in first few lines
        first_10_lines = "\n".join(text.split("\n")[:10])

        for pattern in self.TITLE_PATTERNS:
            matches = re.findall(pattern, first_10_lines, re.IGNORECASE)
            if matches:
                return str(matches[0]).strip()

        return None

    def _extract_year(self, text: str) -> int | None:
        """Extract the year of the legal document."""
        # Look for year patterns in first 20 lines
        first_20_lines = "\n".join(text.split("\n")[:20])

        for pattern in self.YEAR_PATTERNS:
            matches = re.findall(pattern, first_20_lines)
            if matches:
                for match in matches:
                    try:
                        year = int(match)
                        if 1800 <= year <= 2100:
                            return year
                    except ValueError:
                        continue

        return None

    def _detect_jurisdiction(self, text: str) -> str | None:
        """Detect the jurisdiction of the legal document."""
        first_30_lines = "\n".join(text.split("\n")[:30])

        for jurisdiction, patterns in self.JURISDICTION_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, first_30_lines, re.IGNORECASE):
                    return jurisdiction

        return None

    def normalize_doc_type(self, hint: str) -> str:
        """Normalize a free-form doc-type hint to a canonical value.

        Recognized aliases ("Analysis Report", "Inspection Report", …) and
        exact enum values ("act", "notification", …) map to the canonical
        ``LegalDocumentType.value``; anything else is returned verbatim (so
        callers can still use custom labels).

        Args:
            hint: The ``doc_type`` hint supplied by the caller.

        Returns:
            A canonical document-type string ("unknown" for empty hints).
        """
        if not hint.strip():
            return "unknown"
        cleaned = hint.strip().lower()
        if cleaned in self.DOC_TYPE_ALIASES:
            return self.DOC_TYPE_ALIASES[cleaned]
        for doc_type in LegalDocumentType:
            if doc_type is not LegalDocumentType.UNKNOWN and cleaned == doc_type.value:
                # Enum.value is typed ``Any`` — coerce for a str return contract.
                return str(doc_type.value)
        return hint.strip()

    def clear_cache(self):
        """Clear the document cache."""
        self.document_cache.clear()
