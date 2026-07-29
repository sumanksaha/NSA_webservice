"""
Cross-field validation rules for the Legal Metadata Extraction Engine.

Validates extracted fields for internal consistency:
- Date format coherence
- Authority-to-jurisdiction mapping
- Document-type-to-authority plausibility
- State-country consistency
- Language-script consistency
"""

from __future__ import annotations

import logging
import re

from app.metadata_extractor.models import FieldConfidence

logger = logging.getLogger(__name__)


class Validator:
    """Cross-field validation for extracted legal metadata.

    Each method returns a potentially adjusted ``FieldConfidence``.
    """

    def validate_all(
        self,
        fields: dict[str, FieldConfidence],
        text: str,
    ) -> dict[str, FieldConfidence]:
        """Run all validation rules across extracted fields."""
        result = dict(fields)  # mutable copy

        self._validate_title_authority(result)
        self._validate_date_coherence(result)
        self._validate_jurisdiction_hierarchy(result)
        self._validate_language_script(result, text)
        self._validate_document_type_authority(result)

        return result

    # ------------------------------------------------------------------
    # Validation rules
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_title_authority(fields: dict[str, FieldConfidence]) -> None:
        """Boost if authority appears in or near the title."""
        title = fields.get("title", FieldConfidence(value="", score=0.0, method="default"))
        authority = fields.get("authority", FieldConfidence(value="", score=0.0, method="default"))
        if title.value and authority.value:
            # Check if authority name appears within 500 chars of title
            if authority.value.split()[-1] in title.value.upper():
                fields["authority"] = FieldConfidence(
                    value=authority.value,
                    score=min(1.0, authority.score + 0.08),
                    method=authority.method,
                    detail=f"{authority.detail}; validated_by_title",
                )

    @staticmethod
    def _validate_date_coherence(fields: dict[str, FieldConfidence]) -> None:
        """Ensure effective_date >= date (if both present)."""
        date_val = fields.get("date", FieldConfidence(value="", score=0.0, method="default"))
        eff_date = fields.get("effective_date", FieldConfidence(value="", score=0.0, method="default"))
        if date_val.value and eff_date.value:
            # Extract years
            date_years = re.findall(r"\b(20|19)\d{2}\b", date_val.value)
            eff_years = re.findall(r"\b(20|19)\d{2}\b", eff_date.value)
            if date_years and eff_years:
                if int(eff_years[0]) >= int(date_years[0]):
                    fields["effective_date"] = FieldConfidence(
                        value=eff_date.value,
                        score=min(1.0, eff_date.score + 0.05),
                        method=eff_date.method,
                        detail=f"{eff_date.detail}; date_coherent",
                    )

    @staticmethod
    def _validate_jurisdiction_hierarchy(fields: dict[str, FieldConfidence]) -> None:
        """Ensure state and country are consistent with jurisdiction."""
        jur = fields.get("jurisdiction", FieldConfidence(value="", score=0.0, method="default"))
        state = fields.get("state", FieldConfidence(value="", score=0.0, method="default"))
        country = fields.get("country", FieldConfidence(value="India", score=0.6, method="default"))

        # If jurisdiction mentions a specific state, boost state
        if jur.value and state.value and jur.value.upper() in state.value.upper():
            fields["state"] = FieldConfidence(
                value=state.value,
                score=min(1.0, state.score + 0.05),
                method=state.method,
                detail=f"{state.detail}; jurisdiction_validated",
            )

        # If jurisdiction says "India" or "Central Government", country is India
        if "INDIA" in jur.value.upper() or "CENTRAL" in jur.value.upper():
            fields["country"] = FieldConfidence(
                value="India",
                score=min(1.0, country.score + 0.05),
                method=country.method,
                detail=f"{country.detail}; jurisdiction_consistent",
            )

    @staticmethod
    def _validate_language_script(
        fields: dict[str, FieldConfidence],
        text: str,
    ) -> None:
        """Validate detected language matches actual Unicode script in text."""
        lang = fields.get("language", FieldConfidence(value="english", score=0.5, method="default"))
        if not text or not lang.value:
            return

        # Count Devanagari vs Latin characters
        devanagari = len(re.findall(r"[\u0900-\u097F]", text))
        latin = len(re.findall(r"[a-zA-Z]", text))

        if lang.value == "hindi" and devanagari < latin * 0.1:
            # Hindi was detected but very few Devanagari chars — lower confidence
            fields["language"] = FieldConfidence(
                value=lang.value,
                score=max(0.1, lang.score - 0.2),
                method=lang.method,
                detail=f"{lang.detail}; script_mismatch",
            )
        elif lang.value == "english" and devanagari > latin:
            # English detected but mostly Devanagari — switch to Hindi
            fields["language"] = FieldConfidence(
                value="hindi",
                score=min(0.9, devanagari / (devanagari + latin)),
                method="heuristic",
                detail="script_analysis:devanagari_dominant",
            )

    @staticmethod
    def _validate_document_type_authority(fields: dict[str, FieldConfidence]) -> None:
        """Validate document type against known authority."""
        doc_type = fields.get("document_type", FieldConfidence(value="", score=0.0, method="default"))
        authority = fields.get("authority", FieldConfidence(value="", score=0.0, method="default"))

        if not doc_type.value or not authority.value:
            return

        auth_upper = authority.value.upper()
        type_upper = doc_type.value.upper()

        # Regulations typically come from regulatory bodies
        if "REGULATION" in type_upper and "AUTHORITY" in auth_upper:
            fields["document_type"] = FieldConfidence(
                value=doc_type.value,
                score=min(1.0, doc_type.score + 0.05),
                method=doc_type.method,
                detail=f"{doc_type.detail}; authority_validated",
            )

        # Acts typically come from Parliament / Legislature
        if "ACT" in type_upper and any(kw in auth_upper for kw in ["MINISTRY", "PARLIAMENT", "LEGISLATIVE"]):
            fields["document_type"] = FieldConfidence(
                value=doc_type.value,
                score=min(1.0, doc_type.score + 0.05),
                method=doc_type.method,
                detail=f"{doc_type.detail}; authority_validated",
            )
