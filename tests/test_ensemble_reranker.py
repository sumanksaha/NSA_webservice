"""Tests for the sec_act + CE ensemble reranker (CE_RERANK_REVIEW, 2026-08-14).

The ensemble ranks retrieval chunks with the deterministic sec_act legal
features as primary (query-detected section/Act vs payload stamps — the
strongest single reranker measured on the V5.5 P1 head) and scores only the
post-sec_act top-K head with the cross-encoder as a complementary second
opinion.  No external services required — the cross-encoder is injected via
the constructor, and the legal features are pure lexical detection.
"""

from __future__ import annotations

import os

from app.rag.retrieval.reranker import EnsembleReranker, Reranker
from app.rag.retrieval.result import RetrievedChunk


def _chunk(
    cid: str,
    score: float,
    text: str = "",
    section_number: str | None = None,
    act_name: str = "",
    document_title: str = "",
    hierarchy_level: int = 3,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        score=score,
        text=text or cid,
        section_number=section_number,
        act_name=act_name,
        document_title=document_title,
        hierarchy_level=hierarchy_level,
    )


class _MockCrossEncoder:
    """Cross-encoder stand-in: scores the head by a text -> score map.

    ``scores`` maps chunk **text** -> CE score so tests can control exactly
    which chunk the CE prefers (``predict`` receives ``(query, text)`` pairs).
    Records the number of pairs scored (the latency-bound assertion).
    """

    def __init__(self, scores: dict[str, float]):
        self.scores = scores
        self.scored_pairs = 0

    def predict(self, pairs):
        self.scored_pairs += len(pairs)
        return [self.scores.get(text, 0.0) for _query, text in pairs]


class TestEnsembleFeatures:
    """The sec_act feature half (deterministic, no encoder needed)."""

    def test_section_match_promotes_chunk(self):
        # c1 has a lower base score but matches query section 55; c2 is
        # higher-ranked by retrieval but is section 56.
        chunks = [
            _chunk("c2", score=0.9, text="Sec 56 penalties", section_number="56"),
            _chunk("c1", score=0.5, text="Sec 55 adulteration", section_number="55"),
        ]
        reranker = EnsembleReranker(encoder=None, ce_head=2)
        result = reranker.rerank("What does Section 55 say about adulteration?", chunks)
        assert result[0].chunk_id == "c1"

    def test_act_match_uses_act_name_or_document_title(self):
        # Query names the Indian Contract Act; c1's act_name matches, c2's
        # document_title contains the instrument (both should match).  c3 has
        # neither.
        chunks = [
            _chunk("c1", score=0.6, text="compensation", act_name="Indian Contract Act, 1872"),
            _chunk("c2", score=0.5, text="breach damages", document_title="Indian Contract Act, 1872"),
            _chunk("c3", score=0.9, text="pollution control", act_name="Environment (Protection) Act, 1986"),
        ]
        reranker = EnsembleReranker(encoder=None, ce_head=3)
        result = reranker.rerank("compensation under the Indian Contract Act", chunks)
        assert result[0].chunk_id == "c1"
        # c2 (act via document_title) must rank above c3 (wrong act, higher base)
        ids = [c.chunk_id for c in result]
        assert ids.index("c2") < ids.index("c3")

    def test_no_identifiers_keeps_base_order(self):
        # No section/act in the query -> features are zero; order follows the
        # base score (with the CE bonus applied, here no encoder).
        chunks = [
            _chunk("c1", score=0.9, text="general provision", section_number="55"),
            _chunk("c2", score=0.5, text="another provision", section_number="12"),
        ]
        reranker = EnsembleReranker(encoder=None, ce_head=2)
        result = reranker.rerank("what is the general rule?", chunks)
        assert result[0].chunk_id == "c1"

    def test_fallback_to_plain_reranker_class_still_works(self):
        # The plain Reranker is unchanged — regression guard.
        chunks = [
            _chunk("c1", score=0.82, text="Section 55 of the FSS Act penalties", section_number="55"),
            _chunk("c2", score=0.45, text="Section 56 punishment", section_number="56"),
        ]
        reranker = Reranker(encoder=None)
        result = reranker.rerank("section 55", chunks, top_k=2)
        assert result[0].chunk_id == "c1"


class TestEnsembleCEHead:
    """The bounded CE second opinion."""

    def test_ce_scores_only_head_not_all_chunks(self):
        chunks = [_chunk(f"c{i}", score=float(100 - i), text=f"provision {i}") for i in range(10)]
        ce = _MockCrossEncoder({f"provision {i}": 0.9 for i in range(10)})
        reranker = EnsembleReranker(encoder=ce, ce_head=4)
        reranker.rerank("query", chunks, top_k=5)
        assert ce.scored_pairs == 4  # latency bound: only the head is scored

    def test_ce_can_promote_feature_less_chunk_within_head(self):
        # No legal features in the query; the CE strongly prefers "gamma", so
        # the head (base top-4) re-orders by the CE bonus.
        chunks = [
            _chunk("c1", score=0.95, text="alpha"),
            _chunk("c2", score=0.90, text="beta"),
            _chunk("c3", score=0.85, text="gamma"),
            _chunk("c4", score=0.80, text="delta"),
        ]
        ce = _MockCrossEncoder({"alpha": 0.1, "beta": 0.2, "gamma": 0.9, "delta": 0.3})
        reranker = EnsembleReranker(encoder=ce, ce_head=4, ce_weight=1.0)
        result = reranker.rerank("generic question", chunks)
        assert result[0].chunk_id == "c3"

    def test_ce_bonus_cannot_override_section_match(self):
        # c2 matches the query section (feature +2.0) but the CE hates it;
        # c1 has no feature match but the CE loves it.  The feature must win
        # (sec_act primary per the review).
        chunks = [
            _chunk("c1", score=0.9, text="text about food safety generally"),
            _chunk("c2", score=0.5, text="Section 55 penalties", section_number="55"),
        ]
        ce = _MockCrossEncoder({"text about food safety generally": 1.0, "Section 55 penalties": 0.0})
        reranker = EnsembleReranker(encoder=ce, ce_head=2, ce_weight=1.0)
        result = reranker.rerank("What does Section 55 say about adulteration?", chunks)
        assert result[0].chunk_id == "c2"

    def test_ce_failure_degrades_to_features(self):
        class _FailingEncoder:
            def predict(self, pairs):
                raise RuntimeError("encoder crashed")

        chunks = [
            _chunk("c2", score=0.9, text="Sec 56", section_number="56"),
            _chunk("c1", score=0.5, text="Sec 55 adulteration", section_number="55"),
        ]
        reranker = EnsembleReranker(encoder=_FailingEncoder(), ce_head=2)
        result = reranker.rerank("What does Section 55 say about adulteration?", chunks)
        # Feature ranking survives the CE failure.
        assert result[0].chunk_id == "c1"
        assert len(result) == 2

    def test_empty_and_top_k(self):
        reranker = EnsembleReranker(encoder=None, ce_head=2)
        assert reranker.rerank("query", []) == []
        chunks = [_chunk("c1", score=0.9, text="a"), _chunk("c2", score=0.8, text="b")]
        result = reranker.rerank("query", chunks, top_k=1)
        assert len(result) == 1


class TestEnsembleExactMatch:
    """The exact_match feature (sec AND act match -> +1.0 bonus)."""

    def test_exact_match_boost_promotes_over_section_only(self):
        # c1: section match only (sec=55, wrong act) -> +2.0
        # c2: exact match (sec=55, right act) -> +2.0 + 1.5 + 1.0 = +4.5
        # c3: act match only -> +1.5
        # Even though c3 has highest base score, c2 (exact) should win.
        chunks = [
            _chunk("c3", score=0.9, text="breach damages", act_name="Indian Contract Act, 1872"),
            _chunk(
                "c1",
                score=0.5,
                text="Sec 55 adulteration",
                section_number="55",
                act_name="Environment (Protection) Act, 1986",
            ),
            _chunk(
                "c2",
                score=0.3,
                text="Sec 55 penalty",
                section_number="55",
                act_name="Food Safety and Standards Act, 2006",
            ),
        ]
        reranker = EnsembleReranker(encoder=None, ce_head=3)
        result = reranker.rerank("Section 55 of the Food Safety and Standards Act", chunks)
        assert result[0].chunk_id == "c2"  # exact match wins despite low base

    def test_exact_match_does_not_harm_section_only_winner(self):
        # c1 has both sec+act match (exact), c2 has sec only.
        # c1 should still win because exact adds +1.0 on top of sec.
        chunks = [
            _chunk(
                "c2",
                score=0.85,
                text="Sec 55 general",
                section_number="55",
                act_name="Environment (Protection) Act, 1986",
            ),
            _chunk(
                "c1", score=0.80, text="Sec 55 FSS", section_number="55", act_name="Food Safety and Standards Act, 2006"
            ),
        ]
        reranker = EnsembleReranker(encoder=None, ce_head=2)
        result = reranker.rerank("Section 55 of the Food Safety and Standards Act", chunks)
        assert result[0].chunk_id == "c1"

    def test_exact_disabled_when_no_identifiers(self):
        # No section/act in query -> all features zero, no exact bonus.
        chunks = [
            _chunk(
                "c1",
                score=0.9,
                text="provision alpha",
                section_number="55",
                act_name="Food Safety and Standards Act, 2006",
            ),
            _chunk("c2", score=0.8, text="provision beta"),
        ]
        reranker = EnsembleReranker(encoder=None, ce_head=2)
        result = reranker.rerank("general question about food safety", chunks)
        assert result[0].chunk_id == "c1"  # base score order preserved


class TestHierarchyPreference:
    """hierarchy_level 3-5 (section/subsection/clause) get a small boost over
    level 1-2 (document root / chapter headers)."""

    def test_higher_hierarchy_wins_when_scores_tied(self):
        # Same base score, but c2 is at section level (3) vs c1 at document
        # root (1).  c2 should win from the +0.2 hierarchy boost.
        # Use a mock CE with equal scores (CE bonus = 0 via minmax) so only
        # the feature scores decide.
        chunks = [
            _chunk("c1", score=0.5, text="document root", hierarchy_level=1),
            _chunk("c2", score=0.5, text="section text", hierarchy_level=3),
        ]
        ce = _MockCrossEncoder({"document root": 0.0, "section text": 0.0})
        reranker = EnsembleReranker(encoder=ce, ce_head=2, ce_weight=1.0)
        result = reranker.rerank("generic question", chunks)
        assert result[0].chunk_id == "c2"

    def test_level_1_no_boost_preserved_by_base_score(self):
        # c1 at level 1 needs enough base score to overcome the hierarchy
        # gap (0.2).  With base score 0.8 vs 0.5 at level 3, c1 still wins.
        chunks = [
            _chunk("c1", score=0.8, text="root", hierarchy_level=1),
            _chunk("c2", score=0.5, text="section", hierarchy_level=3),
        ]
        ce = _MockCrossEncoder({"root": 0.0, "section": 0.0})
        reranker = EnsembleReranker(encoder=ce, ce_head=2, ce_weight=1.0)
        result = reranker.rerank("generic question", chunks)
        assert result[0].chunk_id == "c1"

    def test_hierarchy_boost_compounds_with_sec_match(self):
        # c1: sec match (level 1) -> +2.0 + 0.0
        # c2: no sec match (level 3) -> 0.0 + 0.2
        # sec_match should dominate (2.0 > 0.2)
        chunks = [
            _chunk("c1", score=0.3, text="root sec match", section_number="55", hierarchy_level=1),
            _chunk("c2", score=0.3, text="section no sec", section_number="99", hierarchy_level=3),
        ]
        ce = _MockCrossEncoder({"root sec match": 0.0, "section no sec": 0.0})
        reranker = EnsembleReranker(encoder=ce, ce_head=2, ce_weight=1.0)
        result = reranker.rerank("Section 55 of the FSS Act", chunks)
        assert result[0].chunk_id == "c1"  # sec_match wins over hierarchy


class TestDynamicCESkipping:
    """CE is skipped when the sec_act head is already decisive (exact matches)."""

    def test_ce_skipped_when_head_all_exact(self):
        ce = _MockCrossEncoder({"": 0.0})
        # All chunks in the head match both section + act -> CE should be skipped
        chunks = [
            _chunk(
                "c1", score=0.95, text="Sec 55 FSS", section_number="55", act_name="Food Safety and Standards Act, 2006"
            ),
            _chunk(
                "c2",
                score=0.85,
                text="Sec 55 FSS too",
                section_number="55",
                act_name="Food Safety and Standards Act, 2006",
            ),
            _chunk(
                "c3",
                score=0.75,
                text="Sec 55 FSS again",
                section_number="55",
                act_name="Food Safety and Standards Act, 2006",
            ),
        ]
        reranker = EnsembleReranker(encoder=ce, ce_head=3, ce_weight=1.0)
        reranker.rerank("Section 55 of the Food Safety and Standards Act", chunks)
        assert ce.scored_pairs == 0  # CE skipped

    def test_ce_runs_when_head_not_all_exact(self):
        ce = _MockCrossEncoder({"": 0.0})
        # c1 has exact match, c2 has section-only (no act), c3 has neither
        chunks = [
            _chunk(
                "c1", score=0.95, text="Sec 55 FSS", section_number="55", act_name="Food Safety and Standards Act, 2006"
            ),
            _chunk(
                "c2", score=0.85, text="Sec 55 EPA", section_number="55", act_name="Environment (Protection) Act, 1986"
            ),
            _chunk("c3", score=0.75, text="generic provision"),
        ]
        reranker = EnsembleReranker(encoder=ce, ce_head=3, ce_weight=1.0)
        reranker.rerank("Section 55 of the Food Safety and Standards Act", chunks)
        assert ce.scored_pairs == 3  # CE runs - head not all exact

    def test_ce_runs_when_no_identifiers_in_query(self):
        ce = _MockCrossEncoder({"alpha": 1.0, "beta": 0.5})
        chunks = [
            _chunk("c1", score=0.9, text="alpha", section_number="55", act_name="Food Safety and Standards Act, 2006"),
            _chunk("c2", score=0.8, text="beta", section_number="56", act_name="Food Safety and Standards Act, 2006"),
        ]
        reranker = EnsembleReranker(encoder=ce, ce_head=2, ce_weight=1.0)
        # Query has no section/act -> CE always runs
        reranker.rerank("general food safety question", chunks)
        assert ce.scored_pairs == 2

    def test_ce_skipping_disabled_by_flag(self):
        ce = _MockCrossEncoder({"": 0.0})
        chunks = [
            _chunk(
                "c1", score=0.9, text="Sec 55 FSS", section_number="55", act_name="Food Safety and Standards Act, 2006"
            ),
            _chunk(
                "c2", score=0.8, text="Sec 55 FSS", section_number="55", act_name="Food Safety and Standards Act, 2006"
            ),
        ]
        reranker = EnsembleReranker(encoder=ce, ce_head=2, ce_weight=1.0)
        reranker.skip_ce_when_confident = False
        reranker.rerank("Section 55 of the Food Safety and Standards Act", chunks)
        assert ce.scored_pairs == 2  # CE runs even though head is all exact


class TestEnsemblePipelineWiring:
    """run_retrieval_pipeline builds the right reranker behind the flag."""

    def test_ensemble_enabled_by_default_returns_ensemble(self, monkeypatch):
        from app.rag.tasks import _build_reranker

        monkeypatch.delenv("RAG_ENSEMBLE_RERANK", raising=False)
        monkeypatch.delenv("RAG_RERANKER_MODEL", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker, EnsembleReranker)

    def test_flag_off_returns_plain_reranker(self, monkeypatch):
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv("RAG_ENSEMBLE_RERANK", "false")
        monkeypatch.delenv("RAG_RERANKER_MODEL", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker, Reranker)

    def test_model_name_honoured(self, monkeypatch):
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv("RAG_RERANKER_MODEL", "custom/legal-ce")
        monkeypatch.delenv("RAG_ENSEMBLE_RERANK", raising=False)
        reranker = _build_reranker()
        assert isinstance(reranker, EnsembleReranker)
        assert reranker.model_name == "custom/legal-ce"

    def test_config_wins_over_env(self, monkeypatch):
        from app.rag.tasks import _build_reranker

        monkeypatch.setenv("RAG_ENSEMBLE_RERANK", "false")
        import app.rag.tasks as tasks_mod

        # Simulate Flask config winning when an app context exists.
        monkeypatch.setattr(tasks_mod, "_ensemble_rerank_enabled", lambda: True)
        reranker = _build_reranker()
        assert isinstance(reranker, EnsembleReranker)

    def test_pipeline_uses_ensemble_and_still_runs(self, monkeypatch):
        """Full run_retrieval_pipeline with fakes — ensemble path executes."""
        import app.rag.retrieval as retrieval_mod
        from app.rag.retrieval.result import SearchResult
        from app.rag.tasks import run_retrieval_pipeline

        recorded = {}

        class FakeClassifier:
            @staticmethod
            def classify(query):
                return (
                    retrieval_mod.QueryType.SECTION_LOOKUP if "Section" in query else retrieval_mod.QueryType.GENERAL_QA
                )

        class FakeParser:
            @staticmethod
            def parse(query, qtype):
                return {}

        class FakeHybrid:
            def __init__(self, dense, sparse, reranker=None):
                recorded["reranker"] = reranker

            def retrieve(self, query, top_k=10, filters=None, identifier_query=None, query_type=None):
                recorded["identifier_query"] = identifier_query
                return SearchResult(query=query, query_type="", chunks=[_chunk("c1", 0.9)], total=1, latency_ms=1)

        class FakeLogger:
            def log(self, **kw):
                return None

        monkeypatch.setattr(retrieval_mod, "HybridRetriever", FakeHybrid)
        monkeypatch.setattr(retrieval_mod, "QueryClassifier", FakeClassifier)
        monkeypatch.setattr(retrieval_mod, "QueryParser", FakeParser)
        monkeypatch.setattr("app.rag.retrieval.logger.RetrievalLogger", FakeLogger)
        monkeypatch.setattr("app.rag.tasks._identifier_route_enabled", lambda: True)
        monkeypatch.setattr("app.rag.tasks._ensemble_rerank_enabled", lambda: True)

        result = run_retrieval_pipeline("What does Section 55 say about adulteration?", top_k=5)
        assert isinstance(recorded["reranker"], EnsembleReranker)
        assert result["identifier"]["form"] == "section"

        # Parallel legal-structure layer (default flags: legal_identities on,
        # evidence_set/expansion off)
        assert "legal_identities" in result
        assert isinstance(result["legal_identities"], list)
        assert "evidence_set" in result
        assert "expanded_candidates" in result
        # Evidence selector is off by default
        assert result["evidence_set"] is None


class TestLegalStructureLayerInPipeline:
    """The parallel legal-structure layer is wired behind feature flags."""

    def test_evidence_selector_flag_off_by_default(self):
        from app.rag.tasks import _evidence_selector_enabled

        assert _evidence_selector_enabled() is False

    def test_evidence_selector_flag_on(self, monkeypatch):
        monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "true")
        # Need to reimport or call with env — the function reads env directly
        # so we need to use a monkeypatched Flask config approach.
        # Since _evidence_selector_enabled falls back to env when no app
        # context, setting the env var should work.
        import importlib

        import app.rag.tasks as t

        importlib.reload(t)
        assert t._evidence_selector_enabled() is True

    def test_legal_identity_flag_on_by_default(self, monkeypatch):
        from app.rag.retrieval.legal_identity import _legal_identity_enabled

        # Default is True
        assert _legal_identity_enabled() is True

    def test_reference_expansion_flag_off_by_default(self):
        from app.rag.retrieval.reference_graph import _reference_expansion_enabled

        # Need env set for test — in tasks.py it calls reference_graph's function
        # which reads ENABLE_REFERENCE_EXPANSION env var (default false)
        old = os.environ.pop("ENABLE_REFERENCE_EXPANSION", None)
        try:
            assert _reference_expansion_enabled() is False
        finally:
            if old is not None:
                os.environ["ENABLE_REFERENCE_EXPANSION"] = old

    def test_pipeline_returns_legal_identities(self, monkeypatch):
        """When legal identity is enabled, pipeline returns identity dicts."""
        import app.rag.retrieval as retrieval_mod
        from app.rag.retrieval.result import SearchResult
        from app.rag.tasks import run_retrieval_pipeline

        class FakeClassifier:
            @staticmethod
            def classify(query):
                return retrieval_mod.QueryType.SECTION_LOOKUP

        class FakeParser:
            @staticmethod
            def parse(query, qtype):
                return {}

        class FakeHybrid:
            def __init__(self, *a, **kw):
                pass

            def retrieve(self, query, **kw):
                from app.rag.retrieval.result import RetrievedChunk

                chunk = RetrievedChunk(
                    chunk_id="c1",
                    score=0.9,
                    text="Section 55 adulteration",
                    section_number="55",
                    act_name="Food Safety and Standards Act, 2006",
                    document_title="Food Safety and Standards Act, 2006",
                )
                return SearchResult(query=query, query_type="", chunks=[chunk], total=1, latency_ms=1)

        class FakeLogger:
            def log(self, **kw):
                return None

        monkeypatch.setattr(retrieval_mod, "HybridRetriever", FakeHybrid)
        monkeypatch.setattr(retrieval_mod, "QueryClassifier", FakeClassifier)
        monkeypatch.setattr(retrieval_mod, "QueryParser", FakeParser)
        monkeypatch.setattr("app.rag.retrieval.logger.RetrievalLogger", FakeLogger)
        monkeypatch.setattr("app.rag.tasks._identifier_route_enabled", lambda: False)
        monkeypatch.setattr("app.rag.tasks._ensemble_rerank_enabled", lambda: False)

        result = run_retrieval_pipeline("Section 55", top_k=5)
        assert "legal_identities" in result
        assert isinstance(result["legal_identities"], list)
        # Legal identity should be parsed (default enabled)
        assert len(result["legal_identities"]) >= 1
