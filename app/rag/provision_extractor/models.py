"""Data contract for the tiered statutory provision extraction engine (ADR-0009).

The engine emits immutable :class:`ProvisionRecord` instances.  ``frozen=True``
guarantees thread-safety and deterministic serialization — records are safe to
cache, hash, and share across ingestion threads without defensive copying.

This module is deliberately dependency-free beyond Pydantic so it can be
imported by ingestion, the backfill CLI, and the Knowledge Graph writer without
pulling in the optional Tier-2 ML stack.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DeonticModality(StrEnum):
    """Primary legal modality of a provision (ADR-0009 §2.2)."""

    OBLIGATION = "obligation"  # "shall ensure", "must maintain"
    PROHIBITION = "prohibition"  # "no person shall manufacture/sell"
    POWER = "power"  # "Food Safety Officer may seize"
    PENALTY = "penalty"  # "liable to penalty not exceeding..."
    DEFINITION = "definition"  # "means and includes"
    EXEMPTION = "exemption"  # "Provided that nothing shall apply..."
    PROCEDURE = "procedure"  # "sample shall be sent to food analyst"
    UNKNOWN = "unknown"  # not classified (Tier 1 leaves this for the ML tier)


#: Extraction tiers, matching the ``extraction_tier`` field values in ADR-0009.
TIER_RULES = "tier1_regex"
TIER_ML = "tier2_ml"
TIER_LLM = "tier3_llm"


class ProvisionRecord(BaseModel):
    """One isolated, structured statutory provision (ADR-0009 §2.2).

    ``provision_id`` follows the gold identifier grammar
    ``<family>:s<section>[(<subsection>)]`` (e.g. ``fssai:s31(2)``) so emitted
    ids round-trip through ``evaluation/benchmark.py::_section_from_id``.
    """

    model_config = ConfigDict(frozen=True)

    provision_id: str
    act_name: str = ""
    family_id: str = ""
    section: str
    subsection: list[str] = Field(default_factory=list)
    clause: list[str] = Field(default_factory=list)
    title: str = ""
    text: str
    modality: DeonticModality = DeonticModality.UNKNOWN
    subject_entity: str | None = None
    penalty_max_inr: float | None = None
    imprisonment_max_months: int | None = None
    cross_references: list[str] = Field(default_factory=list)
    is_proviso: bool = False
    source_chunk_ids: list[str] = Field(default_factory=list)
    char_span: tuple[int, int] | None = None
    confidence: float = 0.0
    extraction_tier: str = TIER_RULES
    #: Provenance of the accepted boundary: ``engine_main`` | ``engine_word`` |
    #: ``dotted_clause`` | ``l4_header`` | ``ml`` | ``review`` (mirrors the
    #: ``L4_override`` provenance convention).
    source: str = ""
