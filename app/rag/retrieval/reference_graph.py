"""Cross-reference graph — traversal of legal provision relationships.

Builds and traverses a graph of cross-reference relationships between legal
chunks/provisions.  The graph is populated from:

1. ``Reference`` objects extracted from chunk text (via ``reference_extractor``)
2. Optional Neo4j connections (best-effort, degrades to in-memory)

The ``expand_references`` function implements the spec's cross-reference
retrieval route:

    normal retrieval + identifier retrieval + cross-reference expansion

Feature flag: ``ENABLE_REFERENCE_EXPANSION`` (default false, per spec).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.rag.retrieval.reference_extractor import (
    CONFIDENCE_HIGH,
    Reference,
    extract_references,
    high_confidence_refs,
    resolve_ref_to_provision,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Edge types
# --------------------------------------------------------------------------- #

#: Legal relationship edge types (mirrors the KG schema from spec §6)
EDGE_REFS = "references"
EDGE_EXCEPTION = "exception"
EDGE_SUBJECT_TO = "subject_to"
EDGE_DEFINITION = "defined_by"
EDGE_COMPLEMENTS = "complements"
EDGE_DEPENDS_ON = "depends_on"
EDGE_CROSS_REFERENCES = "cross_references"

_EDGE_NAMES = {
    EDGE_REFS, EDGE_EXCEPTION, EDGE_SUBJECT_TO, EDGE_DEFINITION,
    EDGE_COMPLEMENTS, EDGE_DEPENDS_ON, EDGE_CROSS_REFERENCES,
}


@dataclass
class ReferenceEdge:
    """A single edge in the reference graph.

    Attributes:
        source_document: The chunk/document that contains the reference.
        target_document: The chunk/document being referenced.
        relationship: Edge type (references, exception, subject_to, etc.).
        confidence: HIGH / MEDIUM / LOW — from the extracted Reference.
        source: "text" if extracted from chunk text, "graph" if from Neo4j.
        evidence: The raw text snippet that triggered the edge.
        target_provision_id: Provision ID if resolvable.
        depth: Traversal depth (0 = direct, 1 = via one edge, etc.).
    """

    source_document: str | None = None
    target_document: str | None = None
    relationship: str = EDGE_REFS
    confidence: str = CONFIDENCE_HIGH
    source: str = "text"
    evidence: str = ""
    target_provision_id: str | None = None
    depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_document": self.source_document,
            "target_document": self.target_document,
            "relationship": self.relationship,
            "confidence": self.confidence,
            "source": self.source,
            "evidence": self.evidence,
            "target_provision_id": self.target_provision_id,
            "depth": self.depth,
        }


@dataclass
class GraphExpansion:
    """Result of a cross-reference expansion.

    Attributes:
        source_document: The document that was expanded from.
        depth: Traversal depth.
        edges: List of reference edges found at this depth.
        candidates: New document IDs discovered through expansion.
    """

    source_document: str | None = None
    depth: int = 0
    edges: list[ReferenceEdge] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# In-memory reference graph (degraded mode + cache)
# --------------------------------------------------------------------------- #


class ReferenceGraph:
    """In-memory graph of legal cross-references.

    Populated lazily as chunks are processed.  When Neo4j is available,
    the graph is seeded from the KG's relationship edges (best-effort).
    """

    def __init__(self) -> None:
        self._edges: list[ReferenceEdge] = []
        self._by_source: dict[str, list[ReferenceEdge]] = {}
        self._by_target: dict[str, list[ReferenceEdge]] = {}
        self._seeded_neo4j: bool = False

    def add_edge(self, edge: ReferenceEdge) -> None:
        """Add an edge to the graph."""
        if edge.source_document:
            self._by_source.setdefault(edge.source_document, []).append(edge)
        if edge.target_document:
            self._by_target.setdefault(edge.target_document, []).append(edge)
        self._edges.append(edge)

    def neighbors(self, document_id: str) -> list[ReferenceEdge]:
        """Return all edges where *document_id* is the source."""
        return list(self._by_source.get(document_id, []))

    def inbound(self, document_id: str) -> list[ReferenceEdge]:
        """Return all edges where *document_id* is the target."""
        return list(self._by_target.get(document_id, []))

    def edge_count(self) -> int:
        return len(self._edges)

    def seed_from_chunks(self, chunks: list[Any]) -> int:
        """Extract references from chunk text and populate the graph.

        Args:
            chunks: List of ``RetrievedChunk`` objects with ``text`` and
                ``chunk_id`` / ``document_title`` attributes.

        Returns:
            Number of HIGH-confidence edges added.
        """
        count = 0
        for chunk in chunks:
            chunk_id = getattr(chunk, "chunk_id", None)
            if not chunk_id:
                continue
            text = getattr(chunk, "text", "")
            if not text:
                continue
            act_hint = getattr(chunk, "act_name", None) or getattr(chunk, "document_title", None)

            refs = extract_references(text, act_hint=act_hint, min_confidence=CONFIDENCE_HIGH)
            for ref in refs:
                target_id = ref.section or ref.rule or ref.schedule or ref.chapter
                if not target_id:
                    continue
                if not ref.confidence == CONFIDENCE_HIGH:
                    continue

                # Map relation to edge type
                if ref.relation:
                    edge_type = {
                        "subject_to": EDGE_SUBJECT_TO,
                        "as_provided_under": EDGE_SUBJECT_TO,
                        "as_provided_in": EDGE_SUBJECT_TO,
                        "except_as_provided_by": EDGE_EXCEPTION,
                        "notwithstanding": EDGE_EXCEPTION,
                        "in_contravention_of": EDGE_EXCEPTION,
                        "as_defined_in": EDGE_DEFINITION,
                        "meaning_of": EDGE_DEFINITION,
                        "interpretation_of": EDGE_DEFINITION,
                    }.get(ref.relation, EDGE_REFS)
                else:
                    edge_type = EDGE_REFS

                edge = ReferenceEdge(
                    source_document=chunk_id,
                    target_document=f"{act_hint}::{target_id}" if act_hint else target_id,
                    relationship=edge_type,
                    confidence=ref.confidence,
                    source="text",
                    evidence=text[max(0, ref.span_start - 10):ref.span_end + 10],
                    target_provision_id=resolve_ref_to_provision(ref),
                )
                self.add_edge(edge)
                count += 1

        return count

    def seed_from_neo4j(self, provision_id: str, kg_queries: Any | None = None) -> int:
        """Seed the graph from Neo4j relationships for a provision.

        Best-effort — returns 0 if Neo4j is unavailable.
        """
        if not self._seeded_neo4j:
            try:
                from app.services.neo4j_graph import neo4j_configured

                if not neo4j_configured():
                    self._seeded_neo4j = True
                    return 0
            except Exception:
                self._seeded_neo4j = True
                return 0

        if kg_queries is None:
            try:
                from kg.queries import LegalKGQueries

                kg_queries = LegalKGQueries()
            except Exception:
                self._seeded_neo4j = True
                return 0

        try:
            related = kg_queries.get_related_provisions(provision_id)
            count = 0
            for r in related:
                edge = ReferenceEdge(
                    source_document=provision_id,
                    target_document=r.get("target_id"),
                    relationship=r.get("relationship_type", EDGE_REFS),
                    confidence=CONFIDENCE_HIGH,
                    source="graph",
                    target_provision_id=r.get("target_id"),
                    evidence=r.get("evidence", ""),
                )
                self.add_edge(edge)
                count += 1
            return count
        except Exception as exc:
            logger.debug("Neo4j graph seed failed for %s: %s", provision_id, exc)
            return 0


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #

# Singleton graph instance for the retrieval pipeline
_graph_instance: ReferenceGraph | None = None


def get_reference_graph() -> ReferenceGraph:
    """Get or create the singleton ReferenceGraph instance."""
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = ReferenceGraph()
    return _graph_instance


def _reference_expansion_enabled() -> bool:
    """Check if reference expansion is enabled via env / Flask config.

    Default is **off** per spec — the current production baseline must remain
    unchanged.
    """
    try:
        from flask import current_app

        if current_app:
            return bool(current_app.config.get("ENABLE_REFERENCE_EXPANSION", False))
    except Exception:
        pass
    return os.environ.get("ENABLE_REFERENCE_EXPANSION", "false").lower() == "true"


def expand_references(
    document_id: str,
    depth: int = 1,
    chunks: list[Any] | None = None,
    graph: ReferenceGraph | None = None,
) -> list[ReferenceEdge]:
    """Expand references from *document_id* up to *depth* levels.

    BFS traversal over the reference graph.  Seeds the graph from the
    provided chunks (or the singleton graph) before traversing.

    Args:
        document_id: The chunk_id or document_id to expand from.
        depth: Maximum traversal depth (1 = direct neighbors only).
        chunks: Optional chunk list to seed the in-memory graph.
        graph: Optional explicit graph instance.

    Returns:
        List of ``ReferenceEdge`` objects, ordered by depth then source order.
    """
    if not _reference_expansion_enabled():
        return []

    graph = graph or get_reference_graph()
    if depth < 1:
        return []

    # Seed from chunks if provided
    if chunks:
        graph.seed_from_chunks(chunks)

    visited: set[str] = {document_id}
    frontier: list[str] = [document_id]
    results: list[ReferenceEdge] = []

    for d in range(1, depth + 1):
        next_frontier: list[str] = []
        for current in frontier:
            for edge in graph.neighbors(current):
                if edge.target_document in visited:
                    continue
                visited.add(edge.target_document)
                next_frontier.append(edge.target_document)
                edge.depth = d
                results.append(edge)
        frontier = next_frontier
        if not frontier:
            break

    return results


def expand_candidates(
    chunks: list[Any],
    top_k: int = 10,
    depth: int = 1,
    graph: ReferenceGraph | None = None,
) -> list[str]:
    """Given a ranked list of chunks, expand candidates via cross-references.

    For each of the top-K chunks, finds HIGH-confidence references and
    returns their target document IDs.  These can be unioned with the
    normal retrieval candidate pool.

    Args:
        chunks: Ranked ``RetrievedChunk`` list (already reranked).
        top_k: Number of top chunks to expand from.
        depth: Graph traversal depth.
        graph: Optional explicit graph instance.

    Returns:
        List of target document IDs discovered through expansion.
    """
    if not _reference_expansion_enabled() or not chunks:
        return []

    graph = graph or get_reference_graph()
    graph.seed_from_chunks(chunks[:top_k * 2])

    seen: set[str] = set()
    candidates: list[str] = []
    for chunk in chunks[:top_k]:
        chunk_id = getattr(chunk, "chunk_id", None)
        if not chunk_id:
            continue
        edges = expand_references(chunk_id, depth=depth, graph=graph)
        for edge in edges:
            if edge.target_document and edge.target_document not in seen:
                seen.add(edge.target_document)
                candidates.append(edge.target_document)

    return candidates


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import os

    os.environ["ENABLE_REFERENCE_EXPANSION"] = "true"

    from dataclasses import dataclass

    @dataclass
    class FakeChunk:
        chunk_id: str
        text: str
        act_name: str

    chunks = [
        FakeChunk(chunk_id="chunk1", text="Section 31(2)(a) applies subject to Section 55.", act_name="FSS Act, 2006"),
        FakeChunk(chunk_id="chunk2", text="Rule 5 of the Act", act_name="FSS Act, 2006"),
    ]

    g = ReferenceGraph()
    count = g.seed_from_chunks(chunks)
    assert count >= 1, f"Expected at least 1 HIGH ref, got {count}"

    neighbors = g.neighbors("chunk1")
    assert len(neighbors) >= 1

    # Test expansion
    expansion = expand_references("chunk1", depth=1, graph=g)
    assert len(expansion) >= 1

    print(f"Self-check passed: {count} edges, {len(expansion)} expansion results")
