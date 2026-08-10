"""Tests for the SparseRetriever (Phase 1, Day 2).

Tests rapidfuzz-based fuzzy matching against an in-memory chunk corpus.
No external services (Qdrant, sentence-transformers) required — the corpus
is injected directly into the constructor.

Follows the import style of ``tests/test_ai_assistant.py``: lazy imports
inside test methods so the module collects even outside an app context.
"""

from __future__ import annotations

from app.rag.retrieval.sparse_retriever import SparseRetriever


def _make_corpus() -> dict[str, dict]:
    """A small in-memory corpus for testing."""
    return {
        "chunk_1": {
            "chunk_id": "chunk_1",
            "text": "Section 55 of the Food Safety and Standards Act, 2006 deals with penalties for adulteration.",
            "document_title": "FSS Act 2006",
            "document_type": "Act",
            "authority": "FSSAI",
            "section_number": "55",
            "chunk_index": 0,
            "hierarchy_level": 1,
        },
        "chunk_2": {
            "chunk_id": "chunk_2",
            "text": "Section 56 provides for punishment of food safety officers who fail to perform their duties.",
            "document_title": "FSS Act 2006",
            "document_type": "Act",
            "authority": "FSSAI",
            "section_number": "56",
            "chunk_index": 1,
            "hierarchy_level": 1,
        },
        "chunk_3": {
            "chunk_id": "chunk_3",
            "text": "The Food Safety and Standards Authority of India is the regulatory body for food safety.",
            "document_title": "FSSAI Guidelines",
            "document_type": "Regulation",
            "authority": "FSSAI",
            "section_number": None,
            "chunk_index": 0,
            "hierarchy_level": 0,
        },
    }


class TestSparseRetrieverBasic:
    """Basic retrieve() behaviour."""

    def test_exact_match_returns_chunk(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("adulteration", top_k=10)
        assert result.total >= 1
        assert any("adulteration" in c.text for c in result.chunks)

    def test_empty_query_returns_empty(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("", top_k=10)
        assert result.total == 0
        assert result.chunks == []

    def test_whitespace_query_returns_empty(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("   ", top_k=10)
        assert result.total == 0

    def test_top_k_limit(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("food", top_k=2)
        assert len(result.chunks) <= 2

    def test_scores_are_normalised(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("food", top_k=10)
        for chunk in result.chunks:
            assert 0.0 <= chunk.score <= 1.0


class TestSparseRetrieverFuzzy:
    """Fuzzy matching with typos."""

    def test_typo_match(self):
        retriever = SparseRetriever(_make_corpus())
        # "adulteraton" (typo for "adulteration") should still fuzzy-match
        result = retriever.retrieve("adulteraton", top_k=10)
        assert result.total >= 1

    def test_results_sorted_by_score_desc(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("section", top_k=10)
        scores = [c.score for c in result.chunks]
        assert scores == sorted(scores, reverse=True)

    def test_threshold_filters_low_scores(self):
        retriever = SparseRetriever(_make_corpus())
        # Very high threshold — should return fewer or no results
        result = retriever.retrieve("unrelated nonsense qzzzx", top_k=10, threshold=99.9)
        # The query is unrelated enough that no chunk should score 99.9
        assert result.total == 0


class TestSparseRetrieverFilters:
    """Pre-filter behaviour."""

    def test_filter_by_document_type(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("food", top_k=10, filters={"document_type": "Regulation"})
        assert result.total >= 1
        assert all(c.document_type == "Regulation" for c in result.chunks)

    def test_filter_excludes_non_matching(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("adulteration", top_k=10, filters={"document_type": "Regulation"})
        # chunk_1 is type "Act", so it should be filtered out
        assert result.total == 0


class TestSparseRetrieverMetadata:
    """Verify payload fields are mapped correctly."""

    def test_chunk_metadata_mapped(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("adulteration", top_k=10)
        chunk = result.chunks[0]
        assert chunk.section_number == "55"
        assert chunk.document_title == "FSS Act 2006"
        assert chunk.authority == "FSSAI"
        assert chunk.chunk_index == 0
        assert chunk.hierarchy_level == 1

    def test_source_is_sparse(self):
        retriever = SparseRetriever(_make_corpus())
        result = retriever.retrieve("food", top_k=10)
        assert result.source == "sparse"


# --------------------------------------------------------------------------- #
# BM25 sparse-vector path (Qdrant store + fastembed embedder) — 2026-08-09
# --------------------------------------------------------------------------- #


class _FakeSparseStore:
    """QdrantStore double with a BM25 sparse collection."""

    def __init__(self, points=None, sparse=True, fail=False):
        self._points = points or []
        self._sparse = sparse
        self._fail = fail
        self.searched = []

    def has_sparse_vectors(self):
        return self._sparse

    def search_sparse(self, sparse_vector, top_k=10, score_threshold=None, filters=None):
        if self._fail:
            raise RuntimeError("qdrant down")
        self.searched.append((sparse_vector, top_k, filters))
        return self._points


class _FakeSparseEmbedder:
    """SparseEmbeddingService double returning a fixed BM25 vector."""

    def __init__(self, result=None):
        self._result = result or {"indices": [1, 5], "values": [0.9, 0.4]}
        self.calls = []

    def embed_sparse(self, text):
        self.calls.append(text)
        return self._result


def _sparse_point(cid="chunk_1", score=0.88):
    return {
        "id": cid,
        "score": score,
        "payload": {
            "chunk_text": "Section 55 penalties for adulteration.",
            "section_number": "55",
            "document_title": "FSS Act 2006",
            "document_type": "Act",
            "authority": "FSSAI",
        },
    }


class TestSparseRetrieverBM25:
    """The Qdrant BM25 sparse-vector path + rapidfuzz fallbacks."""

    def test_bm25_path_used_when_store_supports_sparse(self):
        store = _FakeSparseStore(points=[_sparse_point()])
        embedder = _FakeSparseEmbedder()
        retriever = SparseRetriever(_make_corpus(), store=store, embedder=embedder)
        result = retriever.retrieve("section 55 penalties")
        assert result.total == 1
        assert result.chunks[0].chunk_id == "chunk_1"
        assert result.chunks[0].section_number == "55"
        assert result.source == "sparse"
        # Query was embedded and the sparse store was queried, not the corpus.
        assert embedder.calls == ["section 55 penalties"]
        assert store.searched[0][0] == embedder._result
        assert store.searched[0][2] is None

    def test_bm25_path_forwards_filters(self):
        store = _FakeSparseStore(points=[_sparse_point()])
        retriever = SparseRetriever(
            _make_corpus(), store=store, embedder=_FakeSparseEmbedder()
        )
        retriever.retrieve("q", filters={"document_type": "Act"})
        assert store.searched[0][2] == {"document_type": "Act"}

    def test_falls_back_to_rapidfuzz_when_collection_dense_only(self):
        store = _FakeSparseStore(sparse=False)
        retriever = SparseRetriever(
            _make_corpus(), store=store, embedder=_FakeSparseEmbedder()
        )
        result = retriever.retrieve("adulteration")
        assert result.total >= 1  # rapidfuzz path over the corpus
        assert store.searched == []

    def test_falls_back_to_rapidfuzz_when_sparse_search_fails(self):
        store = _FakeSparseStore(fail=True)
        retriever = SparseRetriever(
            _make_corpus(), store=store, embedder=_FakeSparseEmbedder()
        )
        result = retriever.retrieve("adulteration")
        assert result.total >= 1
        assert result.chunks[0].chunk_id == "chunk_1"

    def test_falls_back_to_rapidfuzz_when_embedder_unavailable(self):
        class _FailingEmbedder:
            def embed_sparse(self, text):
                raise RuntimeError("fastembed unavailable")

        store = _FakeSparseStore(points=[_sparse_point()])
        retriever = SparseRetriever(_make_corpus(), store=store, embedder=_FailingEmbedder())
        result = retriever.retrieve("adulteration")
        assert result.total >= 1  # rapidfuzz fallback, never reaches the store

    def test_store_property_exposed_for_hybrid_fusion(self):
        store = _FakeSparseStore()
        retriever = SparseRetriever(store=store)
        assert retriever.store is store

    def test_store_none_when_not_provided(self):
        retriever = SparseRetriever(_make_corpus())
        assert retriever.store is None

    def test_embed_query_returns_sparse_dict(self):
        embedder = _FakeSparseEmbedder()
        retriever = SparseRetriever(_make_corpus(), embedder=embedder)
        sv = retriever.embed_query("q")
        assert sv == embedder._result
        assert embedder.calls == ["q"]
