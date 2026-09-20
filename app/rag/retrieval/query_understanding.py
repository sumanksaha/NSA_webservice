"""Query understanding — one parse of the query for every consumer.

Three views of "what does the query ask" used to live in three places,
each with its own private regexes:

- ``QueryClassifier`` + ``QueryParser`` (legacy 5-way view: amendment /
  section / case-law / provision / general) — consumed by
  ``classify_node`` and the legacy ``tasks`` pipeline;
- ``classify_legal_query`` + ``get_config`` (13-type view driving reranker
  weights) — consulted inline per rerank call;
- ``QueryPlanner._extract_*`` (intent / entities / jurisdiction / temporal
  for decomposition) — with its own hand-rolled act/section patterns;
- ``identifier.detect_act`` / ``detect_section`` — consulted separately by
  the reranker, the planner, and the identifier arm.

This module is the single seam for all of them. :func:`understand` parses
once and every caller reads its own view off the same
:class:`QueryUnderstanding` value, so precedence ("which parse wins") is
decided in exactly one place and detector fixes propagate to all callers.

Boundary (deliberate): decomposition-owned contracts stay in
``QueryPlanner`` — ``Intent``, ``ComplexityLevel``, raw-text jurisdiction /
temporal scope, and requirement extraction. Those serve task construction,
not retrieval, and their value shapes are pinned by the requirement
benchmark. The planner shares the entity detectors (act/section) but keeps
its own intent vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.identifier import detect_act, detect_section, identifier_query
from app.rag.retrieval.legal_query_classifier import (
    QueryTypeConfig,
    classify_with_confidence,
    get_config,
)
from app.rag.retrieval.query_classifier import (
    AuthorityQueryParser,
    CaseLawQueryParser,
    JurisdictionQueryParser,
    QueryClassifier,
    QueryParser,
    QueryType,
)

__all__ = [
    "QueryUnderstanding",
    "understand",
]


@dataclass(frozen=True)
class QueryUnderstanding:
    """One parsed query. Every view is computed in :func:`understand`."""

    query: str
    #: Legacy 5-way view (``classify_node``, legacy pipeline filters).
    query_type: QueryType
    #: 13-type legal view (reranker weight selection).
    legal_type: str
    legal_confidence: float
    #: Canonical act name (identifier vocabulary), if mentioned.
    act: str | None
    section: str | None
    subsection: str | None
    authority: str | None
    citation: str | None
    court: str | None
    jurisdiction: str | None
    jurisdiction_level: str | None
    #: ``QueryParser`` dispatch view, keyed for filter merging.
    parsed_filters: dict[str, Any] = field(default_factory=dict)
    #: Identifier-arm view: lexical "{Act} section {N}" query, if any.
    identifier_query: str | None = None
    identifier_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def rerank_config(self) -> QueryTypeConfig:
        """Resolved reranker weights for this query's legal view."""
        return get_config(self.legal_type)

    def profile_weights(
        self,
        requirement_type: str | None = None,
        profile_name: str | None = None,
    ) -> dict[str, float]:
        """Effective ``QueryProfile`` rerank weights for a requirement type.

        Single lookup site for what the reranker used to resolve inline per
        call (including the double ``ProfileManager`` construction).
        Falls back to the ``"standard"`` profile for unknown names, exactly
        as the historical call site did.
        """
        from app.rag.planning.profiles import ProfileManager

        name = profile_name or "standard"
        try:
            profile = ProfileManager().get_query_profile(name)
        except ValueError:
            profile = ProfileManager().get_query_profile("standard")
        return profile.rerank_weights_for(requirement_type)


def understand(query: str) -> QueryUnderstanding:
    """Parse ``query`` once; every consumer reads its view off the result.

    Precedence is fixed here: the legacy ``QueryType`` picks the structured
    ``parsed_filters`` dispatch; the legal view is scored independently
    (most keyword hits win); entity detectors run unconditionally since all
    views share them. All detectors are deterministic and empty-safe.
    """
    text = query or ""

    query_type = QueryClassifier().classify(text)
    legal_type, legal_confidence = classify_with_confidence(text)

    act = detect_act(text)
    section, subsection = detect_section(text)

    parsed_authority = AuthorityQueryParser.parse(text)
    case_law = CaseLawQueryParser.parse(text)
    jurisdiction = JurisdictionQueryParser.parse(text)

    parsed_filters = QueryParser().parse(text, query_type) or {}

    identifier_text, identifier_meta = identifier_query(text)

    return QueryUnderstanding(
        query=text,
        query_type=query_type,
        legal_type=legal_type,
        legal_confidence=legal_confidence,
        act=act,
        section=section,
        subsection=subsection,
        authority=parsed_authority.get("authority"),
        citation=case_law.get("citation"),
        court=case_law.get("court"),
        jurisdiction=jurisdiction.get("jurisdiction"),
        jurisdiction_level=jurisdiction.get("level"),
        parsed_filters=dict(parsed_filters),
        identifier_query=identifier_text,
        identifier_meta=dict(identifier_meta),
    )
