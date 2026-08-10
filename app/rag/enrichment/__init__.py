"""Incremental legal enrichment package (Phases 3–15).

Pure deterministic extraction (``deterministic``), structural validation
(``validation``) and ORM persistence (``store``) for enriching the existing
Qdrant chunk corpus without touching the original chunk text or the vector
index.  LLM semantic enrichment (Phase 4) plugs into the same record
shape/checkpoint machinery later.
"""

from app.rag.enrichment.deterministic import (
    build_deterministic_record,
    build_section_index,
    enrich_document,
    extract_crossref_candidates,
    find_act_document,
    resolve_cross_references,
)
from app.rag.enrichment.validation import validate_record

__all__ = [
    "build_deterministic_record",
    "build_section_index",
    "enrich_document",
    "extract_crossref_candidates",
    "find_act_document",
    "resolve_cross_references",
    "validate_record",
]
