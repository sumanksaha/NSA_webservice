"""
Hybrid Legal Metadata Extraction Engine.

Orchestrates:
1. Regex-based extraction (primary, fast, high precision)
2. NER-based extraction (complementary, catches what regex misses)
3. Confidence scoring
4. Cross-field validation

The engine selects the best candidate per field by confidence score,
then validates for internal consistency.
"""

from __future__ import annotations

import logging

from app.metadata_extractor.confidence import score_field
from app.metadata_extractor.extractors.base import (
    AmendmentExtractor,
    AuthorityExtractor,
    BaseExtractor,
    CountryExtractor,
    DateExtractor,
    DocumentTypeExtractor,
    EffectiveDateExtractor,
    GazetteExtractor,
    JurisdictionExtractor,
    LanguageExtractor,
    NotificationExtractor,
    StateExtractor,
    TitleExtractor,
    VersionExtractor,
)
from app.metadata_extractor.models import FieldConfidence, LegalMetadata
from app.metadata_extractor.ner import NERExtractor
from app.metadata_extractor.validation import Validator

logger = logging.getLogger(__name__)


class LegalMetadataEngine:
    """Hybrid extraction engine for Indian legal document metadata.

    Usage::

        engine = LegalMetadataEngine()
        result = engine.extract(document_text)
        print(result.model_dump_json(indent=2))
    """

    def __init__(self, use_ner: bool = False) -> None:
        self._ner = NERExtractor() if use_ner else None
        self._validator = Validator()

        # Register extractors in order: each maps to a metadata field
        self._extractors: dict[str, BaseExtractor] = {
            "title": TitleExtractor(),
            "version": VersionExtractor(),
            "date": DateExtractor(),
            "authority": AuthorityExtractor(),
            "gazette_number": GazetteExtractor(),
            "notification_number": NotificationExtractor(),
            "language": LanguageExtractor(),
            "jurisdiction": JurisdictionExtractor(),
            "state": StateExtractor(),
            "country": CountryExtractor(),
            "document_type": DocumentTypeExtractor(),
            "amendment_status": AmendmentExtractor(),
            "effective_date": EffectiveDateExtractor(),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str) -> LegalMetadata:
        """Extract all metadata fields from legal document text.

        Args:
            text: Full text of the legal document.

        Returns:
            A :class:`LegalMetadata` with all fields populated.
        """
        if not text or not text.strip():
            return self._empty_result()

        # Step 1: Regex extraction
        regex_results: dict[str, list[tuple]] = {}
        for field_name, extractor in self._extractors.items():
            try:
                candidates = extractor.extract(text)
                regex_results[field_name] = candidates
            except Exception as exc:
                logger.warning("Regex extractor '%s' failed: %s", field_name, exc)
                regex_results[field_name] = []

        # Step 2: NER extraction (complementary)
        ner_results: dict[str, list[tuple]] = {}
        if self._ner and self._ner.available:
            try:
                ner_entities = self._ner.extract_entities(text)
                ner_results = self._map_ner_to_fields(ner_entities)
            except Exception as exc:
                logger.warning("NER extraction failed: %s", exc)

        # Step 3: Merge candidates per field
        field_values: dict[str, FieldConfidence] = {}
        for field_name in self._extractors:
            candidates = regex_results.get(field_name, [])
            ner_candidates = ner_results.get(field_name, [])

            # Prefer regex results (higher precision), supplement with NER
            merged = candidates + ner_candidates

            if merged:
                # Pick the best candidate (highest confidence)
                best = max(merged, key=lambda x: x[1])
                value, raw_conf, method, detail = best
            else:
                value = ""
                method = "default"
                detail = "no_extraction"

            # Compute adjusted confidence
            field_values[field_name] = score_field(
                value=value,
                method=method if value else "default",
                candidates=merged,
                field_name=field_name,
                text_length=len(text),
            )

        # Step 4: Cross-field validation
        field_values = self._validator.validate_all(field_values, text)

        # Step 5: Build final result
        return LegalMetadata(
            title=field_values.get("title", FieldConfidence(value="", score=0.0, method="default")),
            version=field_values.get("version", FieldConfidence(value="Latest", score=0.3, method="default")),
            date=field_values.get("date", FieldConfidence(value="", score=0.0, method="default")),
            authority=field_values.get("authority", FieldConfidence(value="", score=0.0, method="default")),
            gazette_number=field_values.get("gazette_number", FieldConfidence(value="", score=0.0, method="default")),
            notification_number=field_values.get(
                "notification_number", FieldConfidence(value="", score=0.0, method="default")
            ),
            language=field_values.get("language", FieldConfidence(value="english", score=0.5, method="default")),
            jurisdiction=field_values.get("jurisdiction", FieldConfidence(value="India", score=0.6, method="default")),
            state=field_values.get("state", FieldConfidence(value="", score=0.0, method="default")),
            country=field_values.get("country", FieldConfidence(value="India", score=0.6, method="default")),
            document_type=field_values.get(
                "document_type", FieldConfidence(value="Notification", score=0.5, method="default")
            ),
            amendment_status=field_values.get(
                "amendment_status", FieldConfidence(value="Original", score=0.5, method="default")
            ),
            effective_date=field_values.get("effective_date", FieldConfidence(value="", score=0.0, method="default")),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _map_ner_to_fields(self, entities: dict[str, list[tuple]]) -> dict[str, list[tuple]]:
        """Map spaCy NER entity labels to our metadata fields."""
        mapping: dict[str, str] = {
            "LAW": "title",
            "ORG": "authority",
            "GPE": "jurisdiction",
            "DATE": "date",
            "LOC": "state",
        }
        results: dict[str, list[tuple]] = {}
        for label, items in entities.items():
            target = mapping.get(label)
            if target:
                if target not in results:
                    results[target] = []
                results[target].extend(items)
        return results

    @staticmethod
    def _empty_result() -> LegalMetadata:
        """Return an empty result with default values."""
        default = lambda v="", s=0.0, m="default": FieldConfidence(value=v, score=s, method=m)
        return LegalMetadata(
            title=default(),
            version=default("Latest", 0.3),
            date=default(),
            authority=default(),
            gazette_number=default(),
            notification_number=default(),
            language=default("english", 0.5),
            jurisdiction=default("India", 0.6),
            state=default(),
            country=default("India", 0.6),
            document_type=default("Notification", 0.5),
            amendment_status=default("Original", 0.5),
            effective_date=default(),
        )
