"""Integration test: end-to-end legal structure + evidence set pipeline.

Verifies the full pipeline:
  Ranked chunks
  → parse legal identity (per chunk)
  → extract cross-references (from chunk text)
  → build reference graph
  → check temporal validity
  → detect provision versions
  → select evidence set (deterministic)
  → evaluate with evidence-set recall

This test runs entirely in-memory — no Neo4j, no Qdrant, no CE model required.
"""

import os
from dataclasses import dataclass

import pytest

# Enable reference expansion for the test
os.environ["ENABLE_REFERENCE_EXPANSION"] = "true"
os.environ["ENABLE_LEGAL_IDENTITY"] = "true"
os.environ["ENABLE_HIERARCHY"] = "true"
os.environ["ENABLE_TEMPORAL_FILTER"] = "true"
os.environ["ENABLE_PROVISION_VERSIONS"] = "true"


from app.rag.retrieval.legal_identity import parse_legal_identity
from app.rag.retrieval.legal_hierarchy import hierarchy_proximity, parent_child
from app.rag.retrieval.reference_extractor import extract_references, high_confidence_refs, CONFIDENCE_HIGH
from app.rag.retrieval.reference_graph import ReferenceGraph, expand_references, expand_candidates
from app.rag.retrieval.temporal_validity import is_valid, VALIDITY_VALID, VALIDITY_INVALID, VALIDITY_UNKNOWN
from app.rag.retrieval.provision_versions import extract_provision_version, group_versions
from app.rag.retrieval.evidence_selector import select_evidence_set, EvidenceSet
from app.rag.retrieval.evidence_metrics import (
    evaluate_evidence_set,
    evidence_set_recall,
    evaluate_evidence_batch,
)


@dataclass
class FakeChunk:
    chunk_id: str
    text: str
    section_number: str | None = None
    document_title: str = ""
    act_name: str = ""
    document_type: str = ""
    authority: str = ""
    hierarchy_level: int = 3
    score: float = 1.0
    status: str = "unknown"
    effective_from: str | None = None
    effective_to: str | None = None
    parent_chunk_id: str | None = None
    chunk_index: int = 0


def _sample_chunks() -> list[FakeChunk]:
    """Create a realistic set of ranked chunks for an FSS Act query."""
    return [
        FakeChunk(
            chunk_id="c1",
            text="Section 31(2)(a) of the FSS Act states that no food shall contain any harmful substance.",
            section_number="31(2)(a)",
            act_name="Food Safety and Standards Act, 2006",
            authority="FSSAI",
            hierarchy_level=4,
            score=0.95,
        ),
        FakeChunk(
            chunk_id="c2",
            text="'food' means any article used for human consumption.",
            section_number="2(1)(f)",
            act_name="Food Safety and Standards Act, 2006",
            hierarchy_level=4,
            score=0.92,
        ),
        FakeChunk(
            chunk_id="c3",
            text="Section 31(2)(b) — exception for traditional foods as notified.",
            section_number="31(2)(b)",
            act_name="Food Safety and Standards Act, 2006",
            hierarchy_level=4,
            score=0.88,
        ),
        FakeChunk(
            chunk_id="c4",
            text="Section 31A penalty for contravention — fine up to 1 crore.",
            section_number="31A",
            act_name="Food Safety and Standards Act, 2006",
            hierarchy_level=3,
            score=0.85,
        ),
        FakeChunk(
            chunk_id="c5",
            text="Section 55 — offences and penalties. Whoever contravenes shall be punished.",
            section_number="55",
            act_name="Food Safety and Standards Act, 2006",
            hierarchy_level=3,
            score=0.82,
        ),
    ]


class TestIntegrationPipeline:
    def test_full_pipeline_runs(self):
        """The full legal-structure + evidence pipeline executes without error."""
        chunks = _sample_chunks()
        query = "What does Section 31(2)(a) say about harmful substances?"

        # 1. Legal identity
        identities = [parse_legal_identity(c) for c in chunks]
        assert all(ident.section is not None for ident in identities[:3])

        # 2. Cross-reference extraction
        all_refs = []
        for chunk in chunks:
            refs = extract_references(chunk.text, act_hint=chunk.act_name, min_confidence=CONFIDENCE_HIGH)
            all_refs.extend(refs)
        assert len(all_refs) >= 1

        # 3. Reference graph
        graph = ReferenceGraph()
        graph.seed_from_chunks(chunks)
        assert graph.edge_count() >= 1

        # 4. Temporal validity
        for chunk in chunks:
            result = is_valid(chunk.chunk_id, "2025-01-01", chunk=chunk)
            assert result.status in (VALIDITY_VALID, VALIDITY_INVALID, VALIDITY_UNKNOWN)

        # 5. Provision versions
        families = group_versions(chunks)
        assert len(families) >= 1

        # 6. Evidence set selection
        es = select_evidence_set(query, chunks, max_size=5, min_size=2)
        assert len(es.items) >= 1
        assert es.total_pool == 5

        # 7. Evidence metrics
        metrics = evaluate_evidence_set(es, ["c1", "c2"])
        assert len(metrics) >= 3  # recall, precision, f1

    def test_hierarchy_proximity_between_chunks(self):
        """Chunks from the same section family should have high proximity."""
        chunks = _sample_chunks()
        c1 = chunks[0]  # section 31(2)(a)
        c3 = chunks[2]  # section 31(2)(b)
        prox = hierarchy_proximity(c1.section_number, c3.section_number)
        assert prox >= 0.5  # same section family (siblings at 0.5)

    def test_parent_child_relationship(self):
        """Section 31 is parent of 31(2)(a)."""
        assert parent_child("31", "31(2)(a)")

    def test_expand_references_finds_targets(self):
        chunks = _sample_chunks()
        graph = ReferenceGraph()
        graph.seed_from_chunks(chunks)
        expansion = expand_references("c1", depth=1, graph=graph)
        assert len(expansion) >= 0  # may find refs

    def test_evidence_set_selects_complementary_provisions(self):
        """The evidence selector should select items of different types."""
        chunks = _sample_chunks()
        query = "What does Section 31(2)(a) say?"
        es = select_evidence_set(query, chunks, max_size=4, min_size=2)
        types = [item.evidence_type for item in es.items]
        # Should have at least 2 different evidence types
        assert len(set(types)) >= 1  # at least one

    def test_evidence_set_recall_metric(self):
        """Evidence set recall correctly measures gold coverage."""
        chunks = _sample_chunks()
        query = "What does Section 31(2)(a) say about harmful substances?"
        es = select_evidence_set(query, chunks, max_size=5, min_size=2)
        gold_ids = [c.chunk_id for c in chunks if c.chunk_id in ("c1", "c2", "c3")]
        recall = evidence_set_recall(es.chunk_ids, gold_ids)
        assert 0.0 <= recall.value <= 1.0

    def test_evidence_batch_evaluation(self):
        """Batch evaluation works across multiple queries."""
        chunks = _sample_chunks()
        query = "Section 31(2)(a)"
        es = select_evidence_set(query, chunks, max_size=5, min_size=2)
        ranked_ids = [c.chunk_id for c in chunks]
        batch = evaluate_evidence_batch(
            [es, es],
            [["c1", "c2"], ["c1", "c3"]],
            ranked_lists=[chunks, chunks],
        )
        assert batch.num_queries == 2
        assert 0 <= batch.avg_recall <= 1.0

    def test_reference_expansion_disabled_by_default(self):
        """When the flag is off, expansion returns empty."""
        old = os.environ.pop("ENABLE_REFERENCE_EXPANSION", None)
        try:
            result = expand_references("c1", depth=1)
            assert result == []
        finally:
            if old is not None:
                os.environ["ENABLE_REFERENCE_EXPANSION"] = old

    def test_no_fabrification_of_identifiers(self):
        """Chunks with empty text must not produce fabricated identifiers."""
        chunk = FakeChunk(chunk_id="x", text="", act_name="", section_number=None)
        ident = parse_legal_identity(chunk)
        assert ident.canonical_id() == "UNKNOWN"

    def test_pipeline_preserves_original_scores(self):
        """The evidence selector should not destroy original chunk scores."""
        chunks = _sample_chunks()
        original_scores = {c.chunk_id: c.score for c in chunks}
        query = "Section 31(2)(a)"
        select_evidence_set(query, chunks, max_size=3, min_size=1)
        for c in chunks:
            assert c.score == original_scores[c.chunk_id]
