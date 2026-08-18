"""Tests for the retrieval cache (Legal_AI_implementation.md §12.1).

The cache memoizes the deterministic, LLM-free retrieval step
(``QueryClassifier -> HybridRetriever.retrieve``) so repeated identical
queries skip the Qdrant round-trip.  These tests patch
``HybridRetriever.retrieve`` directly so they stay offline and need no
Qdrant/embedding service, and they assert that the hash-chained audit log is
still written on cache hits.

Mocking pattern mirrors ``tests/test_rag_e2e.py::test_pipeline_retrieve_task``
(``DenseRetriever._get_client`` / ``_get_encoder`` patched so construction is
network-free; ``_build_reranker`` patched so no torch model loads).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from app.extensions import db
from app.rag import tasks as rag_tasks
from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.result import RetrievedChunk, SearchResult
from app.rag.tasks import clear_retrieval_cache, run_retrieval_pipeline


def _setup_test_env():
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    ctx = app.app_context()
    ctx.push()
    db.drop_all()
    db.create_all()
    return app, ctx


def _teardown(ctx):
    db.session.remove()
    db.drop_all()
    ctx.pop()


def _fake_chunks():
    return [
        RetrievedChunk(
            chunk_id="c-cache-1",
            score=0.91,
            text="Section 55 penalties for food adulteration.",
            section_number="55",
            document_title="FSS Act 2006",
            act_name="FSS Act, 2006",
            document_type="Act",
            authority="FSSAI",
            chunk_index=0,
        )
    ]


def _patched_pipeline(retrieve_mock):
    """Context-manager stack patching the network/torch touch-points of the
    retrieval pipeline so the cache is the only thing under test."""
    return (
        mock.patch.object(HybridRetriever, "retrieve", retrieve_mock),
        mock.patch.object(DenseRetriever, "_get_client", return_value=SimpleNamespace()),
        mock.patch.object(
            DenseRetriever, "_get_encoder",
            return_value=SimpleNamespace(encode=lambda text: [0.5] * 768),
        ),
        mock.patch.object(rag_tasks, "_build_reranker", return_value=SimpleNamespace()),
    )


QUERY = "What does Section 55 of the FSS Act say?"


class TestRetrievalCache:
    def setup_method(self):
        clear_retrieval_cache()

    def teardown_method(self):
        clear_retrieval_cache()

    def test_cache_hit_skips_second_retrieve(self):
        """Identical query within TTL: HybridRetriever.retrieve runs once."""
        app, ctx = _setup_test_env()
        try:
            app.config["RAG_RETRIEVAL_CACHE"] = True
            retrieve_mock = mock.Mock(return_value=SearchResult(
                query=QUERY, query_type="section_lookup", chunks=_fake_chunks(),
                total=1, latency_ms=42, source="hybrid"))
            cm = _patched_pipeline(retrieve_mock)
            with cm[0], cm[1], cm[2], cm[3]:
                r1 = run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
                r2 = run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
            # 2nd call was a cache hit — retrieve() was only invoked once.
            assert retrieve_mock.call_count == 1
            assert r1["chunks"] == r2["chunks"]
            # Hit path reports zero retrieval latency (the miss path carries the
            # real Qdrant round-trip latency) — the distinguishing hit signal
            # surfaced in the return contract.
            assert r2["retrieval_latency_ms"] == 0
            # Audit log is still written on a cache hit (hash-chained trail intact).
            assert r1["log_id"] is not None and r2["log_id"] is not None
        finally:
            _teardown(ctx)

    def test_cache_disabled_by_default(self):
        """With RAG_RETRIEVAL_CACHE unset, identical queries are twice-run."""
        _, ctx = _setup_test_env()
        try:
            retrieve_mock = mock.Mock(return_value=SearchResult(
                query=QUERY, query_type="section_lookup", chunks=_fake_chunks(),
                total=1, latency_ms=42, source="hybrid"))
            cm = _patched_pipeline(retrieve_mock)
            with cm[0], cm[1], cm[2], cm[3]:
                run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
                run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
            assert retrieve_mock.call_count == 2
        finally:
            _teardown(ctx)

    def test_clear_invalidates_cache(self):
        """clear_retrieval_cache() forces the next callable to re-retrieve."""
        app, ctx = _setup_test_env()
        try:
            app.config["RAG_RETRIEVAL_CACHE"] = True
            retrieve_mock = mock.Mock(return_value=SearchResult(
                query=QUERY, query_type="section_lookup", chunks=_fake_chunks(),
                total=1, latency_ms=42, source="hybrid"))
            cm = _patched_pipeline(retrieve_mock)
            with cm[0], cm[1], cm[2], cm[3]:
                run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
                run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
                # Hit -> only 1 retrieve so far.
                assert retrieve_mock.call_count == 1
                clear_retrieval_cache()
                run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
                # Eviction + cache miss -> retrieve() called again.
                assert retrieve_mock.call_count == 2
        finally:
            _teardown(ctx)

    def test_different_top_k_misses_cache(self):
        """A different top_k yields a distinct cache key -> cache miss."""
        app, ctx = _setup_test_env()
        try:
            app.config["RAG_RETRIEVAL_CACHE"] = True
            retrieve_mock = mock.Mock(return_value=SearchResult(
                query=QUERY, query_type="section_lookup", chunks=_fake_chunks(),
                total=1, latency_ms=42, source="hybrid"))
            cm = _patched_pipeline(retrieve_mock)
            with cm[0], cm[1], cm[2], cm[3]:
                run_retrieval_pipeline(QUERY, top_k=5, collection_name="fssai_legal_768")
                run_retrieval_pipeline(QUERY, top_k=10, collection_name="fssai_legal_768")
            assert retrieve_mock.call_count == 2
        finally:
            _teardown(ctx)
