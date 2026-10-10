"""Tests for the multi-domain router (Gap #2): offline classifier/fusion/gate
assertions; the live retriever is mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from app.rag.collections import DEFAULT_COLLECTION
from app.rag.domain_router import (
    _primary_dominance_guard,
    classify_domain,
    run_multi_collection_retrieval,
)
from app.rag.retrieval.result import SearchResult
from tests.test_rag_e2e import _setup_test_env, _teardown


def _make_chunk(cid: str, score: float, domain: str) -> SimpleNamespace:
    from app.rag.collections import collection_for_domain

    return SimpleNamespace(
        chunk_id=cid,
        score=score,
        domain=domain,
        collection=collection_for_domain(domain),
    )


class TestDomainClassifier:
    """The classifier is a deterministic keyword taxonomy -- assert its contract."""

    def test_no_match_returns_primary_fssai(self):
        result = classify_domain("Who is the company's legal counsel?")
        assert result == [("fssai", 1.0)]

    def test_fbo_waste_votes_fssai(self):
        result = classify_domain("A food business operator is discarding solid waste")
        assert result == [("fssai", 2.0)]

    def test_mixed_fbo_plus_segregation_votes_fssai_and_env(self):
        # FBO identity + solid-waste hygiene vote fssai (2 families);
        # standalone "segregation" votes env.
        result = classify_domain("food business operator solid waste segregation")
        assert [d for d, _ in result] == ["fssai", "env"]
        assert result[0] == ("fssai", 2.0)
        assert result[1] == ("env", 1.35)

    def test_municipality_kmc_votes_wb_state(self):
        # Municipality family matches mixed-case keywords (KMC/Kolkata).
        result = classify_domain("Kolkata KMC urban planning")
        assert result == [("wb_state", 1.3)]

    def test_commercial_act_no_fbo_keywords_no_cross_domain(self):
        result = classify_domain("Compensation for breach of Indian Contract Act section 73")
        assert result == [("fssai", 1.0)]

    def test_penalty_keyword_votes_fssai(self):
        result = classify_domain("What penalty applies to the FBO offence?")
        assert result == [("fssai", 2.0)]

    def test_multiple_fssai_families_stay_single_domain(self):
        # Three FSSAI families (solid-waste, hygiene, penalty) = 3 votes.
        result = classify_domain("solid waste hygiene and penalty in the food plant")
        assert result == [("fssai", 2.5)]


class TestMultiCollectionFusion:
    """Fusion order and end-to-end behavior; the retriever is mocked offline."""

    def _mock_retriever(self, chunks):
        retriever = mock.MagicMock()
        retriever.retrieve = mock.MagicMock(
            return_value=SimpleNamespace(
                query="",
                chunks=chunks,
                total=len(chunks),
                latency_ms=0,
                source="multi-domain",
                error=None,
            )
        )
        return retriever

    def test_fusion_order_fssai_top_1(self):
        chunks = {
            "fssai_legal_768": [_make_chunk("fssai_1", 0.95, "fssai"), _make_chunk("fssai_2", 0.90, "fssai")],
            "env_legal_768": [_make_chunk("env_1", 0.98, "env"), _make_chunk("env_2", 0.96, "env")],
        }
        factory = mock.MagicMock(side_effect=lambda name: self._mock_retriever(chunks[name]))
        with mock.patch("app.rag.domain_router.build_hybrid_retriever", factory):
            res = run_multi_collection_retrieval(
                "food business operator discarding solid waste",
                collection_names=["fssai_legal_768", "env_legal_768"],
                top_k=4,
                per_collection_top_k=2,
                rrf_k=60.0,
            )
        assert res.source == "multi-domain"
        assert [c.chunk_id for c in res.chunks] == ["fssai_1", "fssai_2", "env_1", "env_2"]
        assert all(c.domain == c.collection.replace("_legal_768", "") for c in res.chunks)

    def test_pool_wide_enough_for_recall_with_fssai_at_top(self):
        chunks = {
            "fssai_legal_768": [_make_chunk(f"fssai_{i}", 0.95 - i * 0.01, "fssai") for i in range(1, 13)],
            "env_legal_768": [_make_chunk(f"env_{i}", 0.99 - i * 0.01, "env") for i in range(1, 13)],
        }
        factory = mock.MagicMock(side_effect=lambda name: self._mock_retriever(chunks[name]))
        with mock.patch("app.rag.domain_router.build_hybrid_retriever", factory):
            res = run_multi_collection_retrieval(
                "food business operator discarding solid waste",
                collection_names=["fssai_legal_768", "env_legal_768"],
                top_k=10,
                per_collection_top_k=12,
                rrf_k=60.0,
            )
        assert len(res.chunks) == 10
        assert all(c.domain == "fssai" for c in res.chunks)

    def test_rerank_block_keeps_pool_on_failure(self):
        chunks = {
            "fssai_legal_768": [_make_chunk("fssai_1", 0.95, "fssai")],
        }
        failing_reranker = mock.MagicMock()
        failing_reranker.rerank = mock.MagicMock(side_effect=RuntimeError("CE unavailable"))
        factory = mock.MagicMock(
            side_effect=lambda name: mock.MagicMock(
                retrieve=mock.MagicMock(
                    return_value=SimpleNamespace(
                        query="",
                        chunks=chunks[name],
                        total=len(chunks[name]),
                        latency_ms=0,
                        source="multi-domain",
                        error=None,
                    )
                ),
                reranker=failing_reranker,
            )
        )
        with (
            mock.patch("app.rag.domain_router.cfg.ensemble_rerank", True),
            mock.patch("app.rag.domain_router.build_hybrid_retriever", factory),
        ):
            res = run_multi_collection_retrieval(
                "food business operator discarding solid waste",
                collection_names=["fssai_legal_768"],
                top_k=1,
                rrf_k=60.0,
            )
        assert res.source == "multi-domain"
        assert [c.chunk_id for c in res.chunks] == ["fssai_1"]


class TestPrimaryDominanceGuard:
    """The guard promotes an FSSAI chunk to top-1 when a secondary domain leads."""

    def test_promotes_fssai_when_env_leads(self):
        env_lead = [
            _make_chunk("env_1", 0.99, "env"),
            _make_chunk("fssai_1", 0.95, "fssai"),
            _make_chunk("env_2", 0.97, "env"),
        ]
        guarded = _primary_dominance_guard(env_lead)
        assert guarded[0].chunk_id == "fssai_1"
        assert [c.chunk_id for c in guarded] == ["fssai_1", "env_1", "env_2"]

    def test_noop_when_fssai_already_leads(self):
        fssai_lead = [_make_chunk("fssai_1", 0.95, "fssai"), _make_chunk("env_1", 0.98, "env")]
        guarded = _primary_dominance_guard(fssai_lead)
        assert guarded[0].chunk_id == "fssai_1"

    def test_noop_when_no_fssai_chunks(self):
        no_fssai = [_make_chunk("env_1", 0.99, "env"), _make_chunk("wb_1", 0.90, "wb_state")]
        guarded = _primary_dominance_guard(no_fssai)
        assert guarded[0].chunk_id == "env_1"

    def test_returns_new_list_does_not_mutate_input(self):
        env_lead = [_make_chunk("env_1", 0.99, "env"), _make_chunk("fssai_1", 0.95, "fssai")]
        original_ids = [c.chunk_id for c in env_lead]
        _primary_dominance_guard(env_lead)
        assert [c.chunk_id for c in env_lead] == original_ids


class TestPipelineFeatureGate:
    """run_retrieval_pipeline uses the multi-domain route only when gated."""

    def test_fallback_single_collection_when_flag_disabled(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.tasks import run_retrieval_pipeline
            from app.shared.config import cfg

            with mock.patch.object(cfg, "multi_domain_router", False):
                with mock.patch("app.rag.retrieval.factory.build_hybrid_retriever") as br:
                    br.return_value.retrieve.return_value = SearchResult(
                        query="solid waste disposal",
                        query_type="",
                        chunks=[],
                        total=0,
                        latency_ms=0,
                        source="hybrid",
                        error=None,
                    )
                    out = run_retrieval_pipeline(
                        "solid waste disposal",
                        collection_name=["fssai_legal_768", "env_legal_768"],
                        top_k=5,
                    )
            # The list was ignored; the pipeline built the default collection.
            build_arg = br.call_args[0][0]
            assert build_arg == DEFAULT_COLLECTION
            assert out["chunks"] == []
        finally:
            _teardown(ctx)

    def test_pipeline_single_collection_unchanged_when_flag_on(self):
        _app, ctx = _setup_test_env()
        try:
            from app.rag.tasks import run_retrieval_pipeline
            from app.shared.config import cfg

            assert cfg.multi_domain_router is True
            with mock.patch("app.rag.retrieval.factory.build_hybrid_retriever") as br:
                br.return_value.retrieve.return_value = SearchResult(
                    query="section 40 of FSS Act",
                    query_type="",
                    chunks=[],
                    total=0,
                    latency_ms=0,
                    source="hybrid",
                    error=None,
                )
                out = run_retrieval_pipeline(
                    "section 40 of FSS Act",
                    collection_name="fssai_legal_768",
                    top_k=5,
                )
            build_arg = br.call_args[0][0]
            assert build_arg == "fssai_legal_768"
            assert out["chunks"] == []
        finally:
            _teardown(ctx)
