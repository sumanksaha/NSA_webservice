"""Tests comparing HybridRetriever vs DenseRetriever quality.

Uses mock dense/sparse retrievers to compare how hybrid (RRF fusion)
ranks results vs. dense-only, without requiring a real Qdrant instance.
Follows the pattern from test_hybrid_retriever.py and test_dense_retriever.py.
"""

from __future__ import annotations

from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.reranker import Reranker
from app.rag.retrieval.result import RetrievedChunk, SearchResult


def _make_chunk(chunk_id, score, text, section=None):
    return RetrievedChunk(
        chunk_id=chunk_id,
        score=score,
        text=text,
        section_number=section,
        document_title="FSS Act",
        document_type="act",
        authority="FSSAI",
    )


class _MockDense:
    """Dense retriever mock — .search() returns SearchResult with chunks."""

    def __init__(self, chunks):
        self.chunks = sorted(chunks, key=lambda c: c.score, reverse=True)

    def search(self, query, top_k=10, filters=None):
        return SearchResult(query=query, query_type="dense", chunks=self.chunks[:top_k])


class _MockSparse:
    """Sparse retriever mock — .retrieve() returns SearchResult."""

    def __init__(self, chunks):
        self.corpus = {c.chunk_id: c for c in chunks}

    def retrieve(self, query, top_k=10, filters=None):
        query_words = set(query.lower().split())
        ranked = sorted(
            self.corpus.values(),
            key=lambda c: -len(query_words & set(c.text.lower().split())),
        )
        return SearchResult(query=query, query_type="sparse", chunks=ranked[:top_k])


def _hybrid_results(chunks):
    """Run HybridRetriever with mock backends, return chunk IDs."""
    dense = _MockDense(chunks)
    sparse = _MockSparse(chunks)
    h = HybridRetriever(dense=dense, sparse=sparse, reranker=Reranker())
    result = h.retrieve("Section 55 license", top_k=10)
    return [c.chunk_id for c in result.chunks]


class TestHybridVsDense:
    def test_hybrid_returns_union(self):
        """Hybrid should return results from both dense + sparse backends."""
        chunks = [
            _make_chunk("c0", 0.95, "Section 55 of the FSS Act licensing", "55"),
            _make_chunk("c1", 0.30, "food business license Section 55", "55"),
            _make_chunk("c2", 0.10, "unrelated text about cooking"),
        ]
        [c.chunk_id for c in sorted(chunks, key=lambda c: c.score, reverse=True)]
        hybrid_ids = _hybrid_results(chunks)
        # Hybrid should contain all chunks
        assert set(hybrid_ids) == {"c0", "c1", "c2"}
        # c0 (highest dense score) should be near the top
        assert hybrid_ids[0] == "c0"

    def test_hybrid_rrf_fusion(self):
        """Hybrid uses RRF — a chunk ranked high by both should rank high."""
        chunks = [
            _make_chunk("top_both", 0.99, "Section 55 licensing authority", "55"),
            _make_chunk("dense_only", 0.95, "unrelated cooking text", None),
            _make_chunk("sparse_only", 0.10, "license Section 55 food business", "55"),
        ]
        hybrid_ids = _hybrid_results(chunks)
        # "top_both" has highest dense score AND contains query words =>
        # should rank first under RRF.
        assert hybrid_ids[0] == "top_both"

    def test_hybrid_includes_sparse_only(self):
        """A chunk that dense ranks low but sparse ranks high should appear."""
        chunks = [
            _make_chunk("dense_top", 0.95, "unrelated cooking text", None),
            _make_chunk("sparse_top", 0.20, "license Section 55 food business", "55"),
        ]
        hybrid_ids = _hybrid_results(chunks)
        # sparse_top should appear in hybrid results even though dense ranks it #2
        assert "sparse_top" in hybrid_ids

    def test_dense_would_miss_low_score(self):
        """Dense-only with top_k=1 misses the textually-relevant chunk."""
        chunks = [
            _make_chunk("irrelevant", 0.95, "unrelated cooking text", None),
            _make_chunk("relevant", 0.40, "Section 55 licensing food business", "55"),
        ]
        # Dense-only returns only the highest-score chunk
        dense_top = _MockDense(chunks).search("Section 55 license", top_k=1)
        assert dense_top.chunks[0].chunk_id == "irrelevant"  # dense misses the relevant chunk

        # Hybrid should still surface the relevant chunk (via sparse)
        hybrid_ids = _hybrid_results(chunks)
        assert "relevant" in hybrid_ids
        assert "irrelevant" in hybrid_ids

    def test_hybrid_rrf_ranking_consistency(self):
        """HybridRetriever with same inputs produces consistent ranking."""
        chunks = [
            _make_chunk(
                f"c{i}", 0.9 - i * 0.1, "Section 55 licensing" if i < 2 else "other text", "55" if i < 2 else None
            )
            for i in range(5)
        ]
        ids1 = _hybrid_results(chunks)
        ids2 = _hybrid_results(chunks)
        assert ids1 == ids2

    def test_empty_query_returns_all(self):
        """Short query — hybrid still returns results."""
        chunks = [_make_chunk(f"c{i}", 0.5, "text about Section 55") for i in range(3)]
        hybrid_ids = _hybrid_results(chunks)
        assert len(hybrid_ids) == 3

    def test_hybrid_better_than_dense_for_low_score_relevant(self):
        """Hybrid surfaces textually-relevant chunks that dense misses."""
        import copy

        chunks = [
            _make_chunk("irrelevant", 0.95, "unrelated cooking text", None),
            _make_chunk("relevant", 0.40, "Section 55 licensing food business", "55"),
        ]
        # Compute dense ranking first (before hybrid mutates scores)
        dense_ids = [c.chunk_id for c in sorted(chunks, key=lambda c: c.score, reverse=True)]
        # Dense ranks irrelevant first (higher score)
        assert dense_ids[0] == "irrelevant"
        # Hybrid should still surface the relevant chunk
        hybrid_ids = _hybrid_results(copy.deepcopy(chunks))
        assert "relevant" in hybrid_ids
        assert "irrelevant" in hybrid_ids
        # In hybrid, "relevant" should rank higher than or equal to dense-only
        assert hybrid_ids.index("relevant") <= hybrid_ids.index("irrelevant")
