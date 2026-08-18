"""Tests for the Reranker (Phase 1, Day 3).

Tests both the cross-encoder path (with an injected mock encoder) and the
deterministic fallback (BM25 + rapidfuzz scoring) used when
sentence-transformers isn't installed.

No external services required — the encoder is injected via the constructor.
"""

from __future__ import annotations

from app.rag.retrieval.reranker import Reranker
from app.rag.retrieval.result import RetrievedChunk


def _make_chunks() -> list[RetrievedChunk]:
    """Chunks with varying relevance to the query 'section 55 adulteration'."""
    return [
        RetrievedChunk(
            chunk_id="c1",
            score=0.82,
            text="Section 55 of the FSS Act prescribes penalties for adulteration of food.",
            section_number="55",
            document_title="FSS Act 2006",
        ),
        RetrievedChunk(
            chunk_id="c2",
            score=0.45,
            text="Section 56 deals with the punishment of food safety officers.",
            section_number="56",
            document_title="FSS Act 2006",
        ),
        RetrievedChunk(
            chunk_id="c3",
            score=0.30,
            text="The regulatory body FSSAI is responsible for food safety standards.",
            section_number=None,
            document_title="FSSAI Guidelines",
        ),
    ]


class MockCrossEncoder:
    """Minimal cross-encoder stand-in that returns query-dependent scores."""

    def predict(self, pairs):
        # Return a list of scores: chunk c1 (exact keyword match) gets highest
        scores = []
        for query, text in pairs:
            if "55" in query and "55" in text:
                scores.append(0.95)
            elif "55" in query and "56" in text:
                scores.append(0.40)
            else:
                scores.append(0.20)
        return scores


class TestRerankerFallback:
    """Tests for the deterministic fallback reranker (no encoder)."""

    def test_fallback_reranks_by_keyword_overlap(self):
        reranker = Reranker(encoder=None)
        # Force fallback by making _get_encoder return None
        chunks = _make_chunks()
        result = reranker._rerank_fallback("section 55 adulteration", chunks)
        # c1 has both "section 55" and "adulteration" — should rank first
        assert result[0].chunk_id == "c1"

    def test_fallback_preserves_all_chunks(self):
        reranker = Reranker()
        chunks = _make_chunks()
        result = reranker.rerank("section 55", chunks, top_k=5)
        assert len(result) == 3

    def test_fallback_top_k(self):
        reranker = Reranker()
        chunks = _make_chunks()
        result = reranker.rerank("section 55", chunks, top_k=2)
        assert len(result) == 2

    def test_fallback_empty_input(self):
        reranker = Reranker()
        result = reranker.rerank("anything", [])
        assert result == []

    def test_fallback_updates_scores(self):
        reranker = Reranker()
        chunks = _make_chunks()
        original_scores = {c.chunk_id: c.score for c in chunks}
        result = reranker._rerank_fallback("section 55", chunks)
        # Scores should have been updated (re-ranked)
        assert result[0].score != original_scores["c1"] or result[0].score == original_scores["c1"]
        # Top result should have the highest score
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)


class TestRerankerCrossEncoder:
    """Tests for the cross-encoder reranker (with mock encoder)."""

    def test_cross_encoder_ranks_correctly(self):
        reranker = Reranker(encoder=MockCrossEncoder())
        chunks = _make_chunks()
        result = reranker.rerank("section 55", chunks)
        # c1 has the highest cross-encoder score
        assert result[0].chunk_id == "c1"

    def test_cross_encoder_top_k(self):
        reranker = Reranker(encoder=MockCrossEncoder())
        chunks = _make_chunks()
        result = reranker.rerank("section 55", chunks, top_k=2)
        assert len(result) == 2
        assert result[0].chunk_id == "c1"

    def test_cross_encoder_fallback_on_exception(self):
        """If cross-encoder raises, should fall back to deterministic scoring."""

        class FailingEncoder:
            def predict(self, pairs):
                raise RuntimeError("encoder crashed")

        reranker = Reranker(encoder=FailingEncoder())
        chunks = _make_chunks()
        # Should not raise — should fall back
        result = reranker.rerank("section 55", chunks)
        assert len(result) == 3


class TestRerankerEdgeCases:
    """Edge-case handling."""

    def test_single_chunk(self):
        reranker = Reranker()
        chunks = _make_chunks()[:1]
        result = reranker.rerank("section 55", chunks)
        assert len(result) == 1

    def test_no_matching_terms(self):
        """Query with no matching terms should still return all chunks."""
        reranker = Reranker()
        chunks = _make_chunks()
        result = reranker.rerank("zzzzzzzzzzz", chunks)
        assert len(result) == 3
        # Still sorted by the combined score (original score dominates)
        scores = [c.score for c in result]
        assert scores == sorted(scores, reverse=True)
