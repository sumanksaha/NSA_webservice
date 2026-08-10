"""Tests for the HybridRetriever — RRF score fusion (Phase 1, Day 2).

Tests verify that reciprocal rank fusion correctly combines dense and sparse
results, handling duplicates, ranking, and the optional reranker.

Follows the mock-injection pattern: fake DenseRetriever and SparseRetriever
stubs return pre-built SearchResult objects.
"""

from __future__ import annotations

from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.result import RetrievedChunk, SearchResult
from app.rag.retrieval.sparse_retriever import SparseRetriever


def _chunk(cid, score, text="some text", title="Doc"):
    return RetrievedChunk(
        chunk_id=cid, score=score, text=text,
        section_number=None, document_title=title,
        document_type="Act", authority="FSSAI",
        chunk_index=0, hierarchy_level=1,
    )


class StubDenseRetriever:
    """Fake DenseRetriever — returns canned SearchResult."""
    def __init__(self, result: SearchResult):
        self._result = result
    def search(self, query, top_k=10, score_threshold=None, filters=None):
        return self._result


class StubSparseRetriever:
    """Fake SparseRetriever — returns canned SearchResult."""
    def __init__(self, result: SearchResult):
        self._result = result
    def retrieve(self, query, top_k=10, threshold=65.0, filters=None):
        return self._result


class TestHybridRetrieverBasic:
    def test_fusion_ranks_high_overlap_highest(self):
        chunk_a = _chunk("a", 0.9)
        chunk_b = _chunk("b", 0.8)
        chunk_c = _chunk("c", 0.7)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk_a, chunk_b], total=2, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk_a, chunk_c], total=2, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q", top_k=10)
        assert result.chunks[0].chunk_id == "a"

    def test_fusion_deduplicates_chunks(self):
        chunk = _chunk("x", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q", top_k=10)
        assert result.total == 1

    def test_fusion_top_k(self):
        chunks = [_chunk(f"c{i}", 0.9 - i * 0.1) for i in range(5)]
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=chunks, total=5, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=list(reversed(chunks)), total=5, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q", top_k=3)
        assert len(result.chunks) == 3

    def test_fusion_empty_results(self):
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[], total=0, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[], total=0, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q", top_k=10)
        assert result.total == 0
        assert result.chunks == []

    def test_fusion_source_is_hybrid(self):
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[], total=0, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q")
        assert result.source == "hybrid"

    def test_fusion_latency_recorded(self):
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="dense", latency_ms=10))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="sparse", latency_ms=5))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q")
        assert result.latency_ms >= 0

    def test_fusion_error_propagation(self):
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[], total=0, source="dense", error="dense failed"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)

class TestHybridRetrieverReranker:
    def test_reranker_applied_when_provided(self):
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="sparse"))

        class StubReranker:
            def rerank(self, query, chunks, top_k=None):
                return list(reversed(chunks))

        hybrid = HybridRetriever(dense=dense, sparse=sparse, reranker=StubReranker())
        result = hybrid.retrieve("q")
        assert result.total == 1

    def test_reranker_failure_falls_back(self):
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="sparse"))

        class FailingReranker:
            def rerank(self, query, chunks, top_k=None):
                raise RuntimeError("reranker crashed")

        hybrid = HybridRetriever(dense=dense, sparse=sparse, reranker=FailingReranker())
        result = hybrid.retrieve("q")
        assert result.total == 1


class TestHybridRetrieverRRF:
    def test_rrf_ranks_by_reciprocal_rank(self):
        """Verify RRF math: chunk in rank 1 of both lists gets highest score."""
        a = _chunk("a", 0.5)
        b = _chunk("b", 0.9)
        c = _chunk("c", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[b, a, c], total=3, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[a, c, b], total=3, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse, rrf_k=60.0)
        result = hybrid.retrieve("q", top_k=10)
        # a: rank 2 in dense + rank 1 in sparse → 1/62 + 1/61
        # b: rank 1 in dense + rank 3 in sparse → 1/61 + 1/63
        # c: rank 3 in dense + rank 2 in sparse → 1/63 + 1/62
        # a should be highest
        assert result.chunks[0].chunk_id == "a"

    def test_custom_rrf_k(self):
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[chunk], total=1, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse, rrf_k=100.0)
        assert hybrid._rrf_k == 100.0
        result = hybrid.retrieve("q")
        assert result.total == 1
        assert result.chunks[0].chunk_id == "a"

    def test_fusion_both_errors(self):
        dense = StubDenseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[], total=0, source="dense", error="dense down"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="general_qa", chunks=[], total=0, source="sparse", error="sparse down"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q")
        assert result.total == 0
        assert result.error is not None


# --------------------------------------------------------------------------- #
# Server-side RRF fusion (Qdrant prefetch + Fusion.RRF) — 2026-08-09
# --------------------------------------------------------------------------- #


class _FakeHybridStore:
    """QdrantStore double whose collection declares BM25 sparse vectors."""

    def __init__(self, points=None, fail=False):
        self._points = points or []
        self._fail = fail
        self.calls = []

    def has_sparse_vectors(self):
        return True

    def hybrid_search(self, dense_vector, sparse_vector, top_k=10, filters=None):
        if self._fail:
            raise RuntimeError("fusion down")
        self.calls.append((dense_vector, sparse_vector, top_k, filters))
        return self._points


class _FakeSparseEmbedder:
    def __init__(self, result=None):
        self._result = result or {"indices": [1, 5], "values": [0.9, 0.4]}

    def embed_sparse(self, text):
        return self._result


class _EmbeddingDenseRetriever:
    """Dense retriever double with a real ``embed_query`` (needed for fusion)."""

    def __init__(self, result):
        self._result = result

    def embed_query(self, text):
        return [0.1] * 768

    def search(self, query, top_k=10, score_threshold=None, filters=None):
        return self._result


def _fused_point(cid="c1"):
    return {
        "id": cid,
        "score": 0.99,
        "payload": {
            "chunk_text": "Section 55 penalties.",
            "section_number": "55",
            "document_title": "FSS Act 2006",
            "document_type": "Act",
            "authority": "FSSAI",
        },
    }


class TestHybridRetrieverServerFusion:
    """Hybrid retrieval prefers Qdrant's server-side prefetch + RRF fusion."""

    def test_server_side_fusion_used(self):
        store = _FakeHybridStore(points=[_fused_point()])
        sparse = SparseRetriever(corpus={}, store=store, embedder=_FakeSparseEmbedder())
        dense = _EmbeddingDenseRetriever(
            SearchResult(query="q", query_type="", chunks=[], total=0, source="dense")
        )
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q", top_k=7)
        assert result.source == "hybrid"
        assert result.total == 1
        assert result.chunks[0].chunk_id == "c1"
        assert result.chunks[0].section_number == "55"
        # One fused round trip; dense+sparse embeddings passed through.
        assert len(store.calls) == 1
        dense_vec, sparse_vec, top_k, _filters = store.calls[0]
        assert dense_vec == [0.1] * 768
        assert sparse_vec == {"indices": [1, 5], "values": [0.9, 0.4]}
        assert top_k == 7

    def test_server_side_fusion_forwards_filters(self):
        store = _FakeHybridStore(points=[_fused_point()])
        sparse = SparseRetriever(corpus={}, store=store, embedder=_FakeSparseEmbedder())
        dense = _EmbeddingDenseRetriever(
            SearchResult(query="q", query_type="", chunks=[], total=0, source="dense")
        )
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        hybrid.retrieve("q", filters={"document_type": "Act"})
        assert store.calls[0][3] == {"document_type": "Act"}

    def test_server_side_fusion_falls_back_to_client_rrf_on_error(self):
        store = _FakeHybridStore(points=[_fused_point()], fail=True)
        sparse = SparseRetriever(
            corpus={
                "chunk_1": {
                    "chunk_id": "chunk_1",
                    "text": "Section 55 deals with penalties for adulteration.",
                    "document_title": "FSS Act 2006",
                    "section_number": "55",
                }
            },
            store=store,
            embedder=_FakeSparseEmbedder(),
        )
        chunk_a = _chunk("a", 0.9)
        dense = _EmbeddingDenseRetriever(
            SearchResult(query="q", query_type="", chunks=[chunk_a], total=1, source="dense")
        )
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("adulteration")
        # Fusion failed -> client-side RRF over dense + rapidfuzz results.
        assert result.source == "hybrid"
        assert result.total >= 1

    def test_stub_retrievers_skip_server_fusion(self):
        """Legacy stubs (no store/embed_query) use client-side RRF unchanged."""
        chunk = _chunk("a", 0.9)
        dense = StubDenseRetriever(SearchResult(query="q", query_type="", chunks=[chunk], total=1, source="dense"))
        sparse = StubSparseRetriever(SearchResult(query="q", query_type="", chunks=[chunk], total=1, source="sparse"))
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q")
        assert result.source == "hybrid"
        assert result.chunks[0].chunk_id == "a"