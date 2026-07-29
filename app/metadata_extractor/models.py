"""Pydantic models for the Legal Metadata Extraction Engine output.

Each metadata field has an associated confidence score, and the overall
``LegalMetadata`` result carries an aggregate confidence.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FieldConfidence(BaseModel):
    """Confidence score for a single extracted field."""

    model_config = ConfigDict(frozen=True, slots=True)

    value: str = Field(description="Extracted value")
    score: float = Field(ge=0.0, le=1.0, description="Confidence score 0-1")
    method: str = Field(
        description="Extraction method: 'regex', 'ner', 'hybrid', 'heuristic', 'default'",
    )
    detail: str | None = Field(None, description="Additional detail about extraction")


class LegalMetadata(BaseModel):
    """Metadata extracted from a legal document."""

    model_config = ConfigDict(frozen=True, slots=True)

    # Mandatory fields
    title: FieldConfidence = Field(description="Document title / short title")
    version: FieldConfidence = Field(description="Version or latest amendment reference")
    date: FieldConfidence = Field(description="Document date (notification / enactment / publication)")
    authority: FieldConfidence = Field(description="Issuing authority (e.g. FSSAI, MoHFW)")
    gazette_number: FieldConfidence = Field(description="Gazette notification number")
    notification_number: FieldConfidence = Field(description="Notification / file number")
    language: FieldConfidence = Field(description="Primary language of the document")
    jurisdiction: FieldConfidence = Field(description="Jurisdiction (e.g. India, State-level)")
    state: FieldConfidence = Field(description="State of issuance (if applicable)")
    country: FieldConfidence = Field(description="Country of issuance")
    document_type: FieldConfidence = Field(description="Type: Act, Rule, Regulation, Notification, etc.")
    amendment_status: FieldConfidence = Field(description="Amendment status: Original, Amended, Repealed, etc.")
    effective_date: FieldConfidence = Field(description="Date the document came into effect")

    # Computed
    overall_confidence: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Aggregate confidence across all fields",
    )

    @model_validator(mode="after")
    def _compute_overall(self) -> LegalMetadata:
        """Auto-compute overall confidence from individual field scores after all fields are validated."""
        scores = [
            getattr(self, k).score
            for k in (
                "title",
                "date",
                "authority",
                "jurisdiction",
                "document_type",
                "language",
                "gazette_number",
                "notification_number",
                "amendment_status",
            )
        ]
        overall = sum(scores) / len(scores) if scores else 0.0
        # Use object.__setattr__ to bypass frozen=True
        object.__setattr__(self, "overall_confidence", overall)
        return self

    def to_flat_dict(self) -> dict[str, str]:
        """Return a flat dict of field_name -> extracted value for easy serialization."""
        return {
            "title": self.title.value,
            "version": self.version.value,
            "date": self.date.value,
            "authority": self.authority.value,
            "gazette_number": self.gazette_number.value,
            "notification_number": self.notification_number.value,
            "language": self.language.value,
            "jurisdiction": self.jurisdiction.value,
            "state": self.state.value,
            "country": self.country.value,
            "document_type": self.document_type.value,
            "amendment_status": self.amendment_status.value,
            "effective_date": self.effective_date.value,
            "overall_confidence": f"{self.overall_confidence:.4f}",
        }
