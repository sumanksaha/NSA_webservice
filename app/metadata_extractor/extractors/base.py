"""Abstract base extractor and concrete field extractors.

Each extractor implements ``extract(text)``, returning a list of
``(value, confidence, method, detail)`` tuples ordered by confidence.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import app.metadata_extractor.regex_library as rx

logger = logging.getLogger(__name__)

# A single extraction result
Extraction = tuple[str, float, str, str]  # value, confidence, method, detail


class BaseExtractor(ABC):
    """Abstract base for a metadata field extractor."""

    field_name: str = ""

    @abstractmethod
    def extract(self, text: str) -> list[Extraction]:
        """Extract candidate values from text, ordered by decreasing confidence."""
        ...


class TitleExtractor(BaseExtractor):
    """Extract document title/short title."""

    field_name = "title"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern in rx.TITLE_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else match.group(0).strip()
                if not value or len(value) < 5:
                    continue
                # Higher confidence for shorter, more concise titles
                conf = 0.95 if name in ("short_title_act", "short_title_amended") else 0.75
                # Boost amendment titles (they contain "AMENDMENT" keyword)
                if "AMEND" in value.upper() and name != "short_title_amended":
                    conf = max(conf, 0.80)
                results.append((value, conf, "regex", name))
        return _deduplicate(results)


class DateExtractor(BaseExtractor):
    """Extract dates from legal documents."""

    field_name = "date"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        seen: set = set()

        for name, pattern in rx.DATE_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(0).strip()
                if raw in seen:
                    continue
                seen.add(raw)
                # Prefer "Dated the" format (highest confidence)
                conf = (
                    0.95 if name == "dated_ordinal" else 0.85 if name in ("dd_mm_yyyy", "ordinal_month_year") else 0.80
                )
                results.append((raw, conf, "regex", name))

        return results[:10]  # limit to top 10 date candidates


class AuthorityExtractor(BaseExtractor):
    """Extract issuing authority."""

    field_name = "authority"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []

        # Known authority names (direct match)
        for match in rx.AUTHORITY_KNOWN_PATTERN.finditer(text):
            value = match.group(1).strip()
            conf = 0.95  # Exact known match
            results.append((value, conf, "regex", "known_authority"))

        # Pattern-based extraction
        for name, pattern in rx.AUTHORITY_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1).strip() if match.lastindex else match.group(0).strip()
                conf = 0.80
                results.append((value, conf, "regex", name))

        return _deduplicate(results)


class GazetteExtractor(BaseExtractor):
    """Extract gazette and notification numbers."""

    field_name = "gazette_number"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern in rx.GAZETTE_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else match.group(0).strip()
                conf = 0.90 if name in ("gazette_notification_no",) else 0.80
                results.append((value, conf, "regex", name))
        return _deduplicate(results)


class NotificationExtractor(BaseExtractor):
    """Extract notification/file numbers."""

    field_name = "notification_number"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern in rx.GAZETTE_PATTERNS:
            if name in ("notification_no", "fssai_notification"):
                for match in pattern.finditer(text):
                    value = (
                        match.group(1).strip() if match.lastindex and match.lastindex >= 1 else match.group(0).strip()
                    )
                    results.append((value, 0.85, "regex", name))
        return _deduplicate(results)


class LanguageExtractor(BaseExtractor):
    """Detect document language via Unicode script analysis."""

    field_name = "language"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        # Check for English first (Latin script)
        latin_chars = sum(1 for c in text if c.isascii() and c.isalpha())
        total_alpha = sum(1 for c in text if c.isalpha())

        if total_alpha == 0:
            return [("english", 0.5, "heuristic", "default")]

        # Count non-Latin script characters
        script_counts: dict[str, int] = {}
        for name, pattern in rx.LANGUAGE_PATTERNS:
            count = len(pattern.findall(text))
            if count > 0:
                script_counts[name] = count

        if script_counts:
            primary = max(script_counts, key=script_counts.get)
            ratio = script_counts[primary] / total_alpha
            conf = min(0.95, ratio)
            results.append((primary, conf, "heuristic", f"unicode_script:{primary}={script_counts[primary]}"))

        # Default to English if Latin dominates
        if latin_chars > total_alpha * 0.5:
            results.append(("english", min(0.90, latin_chars / max(total_alpha, 1)), "heuristic", "latin_dominant"))

        # Explicit language declaration in document
        for match in rx._p(r"(?:LANGUAGE|LANG)\s*[:\-]\s*(\w+)").finditer(text):
            results.append((match.group(1).strip().lower(), 0.95, "regex", "explicit_language"))

        return _deduplicate(results)


class JurisdictionExtractor(BaseExtractor):
    """Extract jurisdiction, state, and country."""

    field_name = "jurisdiction"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        # Jurisdiction patterns
        for name, pattern in rx.JURISDICTION_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else match.group(0).strip()
                conf = 0.95 if name == "central_govt" else 0.90
                results.append((value, conf, "regex", name))

        # Default to India if not specified
        if not results:
            results.append(("India", 0.60, "heuristic", "default_country"))

        return _deduplicate(results)


class StateExtractor(BaseExtractor):
    """Extract state of issuance."""

    field_name = "state"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for match in rx.STATE_PATTERN.finditer(text):
            value = match.group(1).strip().title()
            results.append((value, 0.90, "regex", "state_match"))
        # Check jurisdiction results for state-level indicators
        if "STATE" in text.upper() or "GOVERNMENT OF" in text.upper():
            for state in rx.INDIAN_STATES:
                if state in text.upper():
                    results.append((state.title(), 0.85, "regex", "state_in_text"))
                    break
        return _deduplicate(results)


class CountryExtractor(BaseExtractor):
    """Extract country of issuance."""

    field_name = "country"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern in rx.COUNTRY_PATTERNS:
            for match in pattern.finditer(text):
                value = (
                    match.group(1).strip().title()
                    if match.lastindex and match.lastindex >= 1
                    else match.group(0).title()
                )
                conf = 0.95 if name == "india" else 0.90
                results.append((value, conf, "regex", name))

        if not results:
            results.append(("India", 0.60, "heuristic", "default_country"))

        return _deduplicate(results)


class DocumentTypeExtractor(BaseExtractor):
    """Classify document type (Act, Rule, Regulation, Notification, etc.)."""

    field_name = "document_type"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern, doc_type in rx.DOCUMENT_TYPE_PATTERNS:
            for _match in pattern.finditer(text):
                results.append((doc_type, 0.90, "regex", name))
        if not results:
            results.append(("Notification", 0.50, "heuristic", "default_type"))
        return _deduplicate(results)


class AmendmentExtractor(BaseExtractor):
    """Extract amendment status."""

    field_name = "amendment_status"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern, status in rx.AMENDMENT_PATTERNS:
            for _match in pattern.finditer(text):
                results.append((status, 0.90, "regex", name))
        if not results:
            results.append(("Original", 0.50, "heuristic", "default_status"))
        return _deduplicate(results)


class VersionExtractor(BaseExtractor):
    """Extract version information."""

    field_name = "version"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        for name, pattern in rx.VERSION_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0).strip()
                conf = 0.85
                results.append((value, conf, "regex", name))
        if not results:
            results.append(("Latest", 0.40, "heuristic", "default_version"))
        return _deduplicate(results)


class EffectiveDateExtractor(BaseExtractor):
    """Extract effective/commencement date."""

    field_name = "effective_date"

    def extract(self, text: str) -> list[Extraction]:
        results: list[Extraction] = []
        # Look for specific commence/effective keywords
        for pattern_name, pattern in [
            (
                "commencement",
                rx._p(
                    r"(?:COMMENCEMENT|COMES?\s+INTO\s+FORCE|EFFECTIVE|ENACTED|PUBLISHED)\s*(?:ON|FROM|WITH\s+EFFECT\s+FROM)?\s*[:\-]?\s*(?:THE\s+)?(\d{1,2}(?:ST|ND|RD|TH)?\s+(?:DAY\s+OF\s+)?[A-Z][a-z]+[\s,]+(?:20|19)\d{2})",
                ),
            ),
            (
                "deemed",
                rx._p(
                    r"(?:DEEMED\s+TO\s+HAVE\s+COME\s+INTO\s+FORCE|SHALL\s+BE\s+DEEMED)\s*(?:ON|FROM)?\s*[:\-]?\s*(?:THE\s+)?(\d{1,2}(?:ST|ND|RD|TH)?\s+(?:DAY\s+OF\s+)?[A-Z][a-z]+[\s,]+(?:20|19)\d{2})",
                ),
            ),
        ]:
            for match in pattern.finditer(text):
                value = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else match.group(0).strip()
                results.append((value, 0.90, "regex", f"effective_{pattern_name}"))

        return _deduplicate(results)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _deduplicate(items: list[Extraction]) -> list[Extraction]:
    """Remove duplicate values while preserving order and highest confidence."""
    seen: dict[str, Extraction] = {}
    for item in items:
        value = item[0]
        if value not in seen or item[1] > seen[value][1]:
            seen[value] = item
    # Sort by confidence descending
    return sorted(seen.values(), key=lambda x: -x[1])
