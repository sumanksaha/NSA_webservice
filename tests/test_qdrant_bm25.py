"""Tests for Qdrant-side BM25 (server-side sparse inference).

Covers ``QdrantStore.search_sparse_text`` / ``hybrid_search_text`` (the
``Qdrant/bm25`` Document-query paths), the ``SparseRetriever`` ``server_bm25``
branch, the ``HybridRetriever`` text-fusion path, and the ``RAG_QDRANT_BM25``
flag.  No network and no fastembed required — fake clients/stores stand in.

Verified live against the provisioned cluster (2026-08-16): a
``{"query": {"text": ..., "model": "Qdrant/bm25"}, "using": "text_sparse"}``
query returns ranked points and is free on the free tier.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.qdrant_client import DEFAULT_COLLECTION, QdrantStore
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.result import SearchResult
from app.rag.retrieval.sparse_retriever import SparseRetriever


def _point(cid: str = "c1", score: float = 18.4) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        score=score,
        payload={"document_id": "d1", "chunk_text": "text", "section_number": "55"},
    )


# --------------------------------------------------------------------------- #
# QdrantStore.search_sparse_text
# --------------------------------------------------------------------------- #


class TestSearchSparseText:
    def test_dict_fallback_sends_document_query(self):
        """No models module → raw text Document dict + using=text_sparse."""
        points = [_point()]
        received = {}

        def query_points(collection_name, query, limit=10, using=None, with_payload=True,
                         with_vectors=False, score_threshold=None, query_filter=None, **kw):
            received.update(
                collection_name=collection_name, query=query, limit=limit,
                using=using, score_threshold=score_threshold, query_filter=query_filter,
            )
            return SimpleNamespace(points=points)

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store._models = False
        results = store.search_sparse_text(
            "penalty for selling substandard food",
            top_k=4, score_threshold=0.5, filters={"document_type": "act"},
        )
        assert received["collection_name"] == DEFAULT_COLLECTION
        assert received["query"] == {"text": "penalty for selling substandard food", "model": "Qdrant/bm25"}
        assert received["using"] == "text_sparse"
        assert received["limit"] == 4
        assert received["score_threshold"] == 0.5
        assert received["query_filter"]["must"][0] == {"key": "document_type", "match": {"value": "act"}}
        assert results[0] == {"id": "c1", "score": 18.4, "payload": {"document_id": "d1", "chunk_text": "text", "section_number": "55"}}

    def test_real_models_uses_document(self):
        """Real client path: query is a models.Document with the BM25 model."""
        received = {}

        def query_points(collection_name, query, limit=10, using=None, **kw):
            received.update(query=query, using=using)
            return SimpleNamespace(points=[_point()])

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store.search_sparse_text("penalty", top_k=3)
        assert received["using"] == "text_sparse"
        assert received["query"].text == "penalty"
        assert received["query"].model == "Qdrant/bm25"

    def test_no_filters_omits_query_filter(self):
        received = {}

        def query_points(collection_name, query, limit=10, using=None, with_payload=True,
                         with_vectors=False, score_threshold=None, query_filter=None, **kw):
            received.update(query_filter=query_filter)
            return SimpleNamespace(points=[])

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store._models = False
        store.search_sparse_text("q")
        assert received["query_filter"] is None

    def test_raises_without_query_points(self):
        store = QdrantStore(client=SimpleNamespace(search=lambda **kw: []))
        with pytest.raises(RuntimeError, match="query_points"):
            store.search_sparse_text("q")


# --------------------------------------------------------------------------- #
# QdrantStore.hybrid_search_text
# --------------------------------------------------------------------------- #


class TestHybridSearchText:
    def test_dict_fallback_prefetch_text_arm(self):
        """Sparse prefetch is the raw text Document; fusion stays RRF."""
        received = {}

        def query_points(collection_name, prefetch, query, limit=10, with_payload=True,
                         with_vectors=False, query_filter=None, **kw):
            received.update(collection_name=collection_name, prefetch=prefetch,
                            query=query, limit=limit, query_filter=query_filter)
            return SimpleNamespace(points=[_point()])

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store._models = False
        results = store.hybrid_search_text(
            [0.1] * 768, "penalty for substandard food", top_k=5, filters={"is_current": True}
        )
        assert received["collection_name"] == DEFAULT_COLLECTION
        assert received["limit"] == 5
        assert received["query"] == {"fusion": "rrf"}
        dense_prefetch, sparse_prefetch = received["prefetch"]
        assert dense_prefetch["using"] == "dense"
        assert dense_prefetch["query"] == [0.1] * 768
        assert sparse_prefetch["using"] == "text_sparse"
        assert sparse_prefetch["query"] == {"text": "penalty for substandard food", "model": "Qdrant/bm25"}
        assert dense_prefetch["limit"] >= 25
        assert received["query_filter"]["must"][0] == {"key": "is_current", "match": {"value": True}}
        assert results[0]["id"] == "c1"

    def test_real_models_document_prefetch(self):
        received = {}

        def query_points(collection_name, prefetch, query, limit=10, **kw):
            received.update(prefetch=prefetch, query=query)
            return SimpleNamespace(points=[_point()])

        store = QdrantStore(client=SimpleNamespace(query_points=query_points))
        store.hybrid_search_text([0.1] * 768, "penalty", top_k=5)
        assert received["query"].fusion == "rrf"
        sparse_prefetch = received["prefetch"][1]
        assert sparse_prefetch.using == "text_sparse"
        assert sparse_prefetch.query.text == "penalty"
        assert sparse_prefetch.query.model == "Qdrant/bm25"

    def test_raises_without_query_points(self):
        store = QdrantStore(client=SimpleNamespace(search=lambda **kw: []))
        with pytest.raises(RuntimeError, match="query_points"):
            store.hybrid_search_text([0.1], "q")


# --------------------------------------------------------------------------- #
# SparseRetriever server_bm25 branch
# --------------------------------------------------------------------------- #


class _Bm25Store:
    """QdrantStore double with both sparse search paths recorded."""

    def __init__(self):
        self.text_calls = []
        self.vector_calls = []

    def has_sparse_vectors(self):
        return True

    def search_sparse_text(self, text, top_k=10, filters=None):
        self.text_calls.append((text, top_k, filters))
        return [_point()]

    def search_sparse(self, sparse_vector, top_k=10, filters=None):
        self.vector_calls.append((sparse_vector, top_k, filters))
        return [_point()]


class _FakeEmbedder:
    def embed_sparse(self, text):
        return {"indices": [1, 5], "values": [0.9, 0.4]}


class TestSparseRetrieverServerBm25:
    def test_server_bm25_sends_raw_text(self):
        store = _Bm25Store()
        sparse = SparseRetriever(corpus={}, store=store, embedder=_FakeEmbedder(), server_bm25=True)
        result = sparse.retrieve("penalty for substandard food", top_k=4)
        assert result.source == "sparse"
        assert result.chunks[0].chunk_id == "c1"
        assert store.text_calls == [("penalty for substandard food", 4, None)]
        assert store.vector_calls == []  # no local fastembed

    def test_default_keeps_local_embed_path(self):
        store = _Bm25Store()
        sparse = SparseRetriever(corpus={}, store=store, embedder=_FakeEmbedder())
        sparse.retrieve("penalty", top_k=3)
        assert store.vector_calls[0][0] == {"indices": [1, 5], "values": [0.9, 0.4]}
        assert store.text_calls == []

    def test_server_bm25_missing_method_degrades(self):
        """Store without search_sparse_text → rapidfuzz fallback, not a crash."""
        class _NoText:
            def has_sparse_vectors(self):
                return True

            def search_sparse(self, *a, **kw):
                raise AssertionError("must not be called")

        sparse = SparseRetriever(corpus={}, store=_NoText(), server_bm25=True)
        result = sparse.retrieve("penalty")
        assert result.source == "sparse"
        assert result.chunks == []  # empty corpus → rapidfuzz returns nothing


# --------------------------------------------------------------------------- #
# HybridRetriever text-fusion path
# --------------------------------------------------------------------------- #


class _TextHybridStore(_Bm25Store):
    def hybrid_search(self, dense_vector, sparse_vector, top_k=10, filters=None):
        raise AssertionError("vector hybrid must not be called in server_bm25 mode")

    def hybrid_search_text(self, dense_vector, text, top_k=10, filters=None):
        self.text_calls.append((dense_vector, text, top_k, filters))
        return [_point("c1", 0.99)]


class _EmbeddingDenseRetriever:
    def embed_query(self, text):
        return [0.1] * 768

    def search(self, query, top_k=10, score_threshold=None, filters=None):
        return SearchResult(query=query, query_type="", chunks=[], total=0, source="dense")


class TestHybridRetrieverServerBm25:
    def test_server_bm25_uses_text_fusion(self):
        store = _TextHybridStore()
        sparse = SparseRetriever(corpus={}, store=store, embedder=_FakeEmbedder(), server_bm25=True)
        dense = _EmbeddingDenseRetriever()
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("penalty for substandard food", top_k=7)
        assert result.source == "hybrid"
        assert result.chunks[0].chunk_id == "c1"
        # One fused round trip with the raw query text (no sparse vector built).
        dense_vec, text, top_k, _filters = store.text_calls[0]
        assert dense_vec == [0.1] * 768
        assert text == "penalty for substandard food"
        assert top_k == 7

    def test_non_server_bm25_keeps_vector_fusion(self):
        store = _TextHybridStore()
        sparse = SparseRetriever(corpus={}, store=store, embedder=_FakeEmbedder())
        dense = _EmbeddingDenseRetriever()
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q")
        assert result.source == "hybrid"
        assert store.vector_calls  # the _Bm25Store base records hybrid_search via vector_calls
        assert store.text_calls == []


# --------------------------------------------------------------------------- #
# RAG_QDRANT_BM25 flag
# --------------------------------------------------------------------------- #


class TestQdrantBm25Flag:
    def test_env_flag(self, monkeypatch):
        from app.rag.tasks import _qdrant_bm25_enabled

        monkeypatch.delenv("RAG_QDRANT_BM25", raising=False)
        assert _qdrant_bm25_enabled() is False
        monkeypatch.setenv("RAG_QDRANT_BM25", "true")
        assert _qdrant_bm25_enabled() is True
        monkeypatch.setenv("RAG_QDRANT_BM25", "false")
        assert _qdrant_bm25_enabled() is False
