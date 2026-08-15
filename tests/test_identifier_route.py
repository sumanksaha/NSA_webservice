"""Identifier-route tests — detection + hybrid fusion + pipeline wiring.

The identifier route (2026-08-13) is the production form of the V5/V5.5
evaluation lever: when a query names an Act and/or section number, a lexical
"{Act} section {N}" query is run through the sparse retriever as a parallel
additive arm and RRF-fused with the dense + sparse results.  These tests
cover the detector module, the hybrid-retriever fusion, and the
run_retrieval_pipeline wiring (all offline with stubs — no Qdrant /
sentence-transformers required).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.retrieval.identifier import detect_act, detect_section, identifier_query  # noqa: E402
from app.rag.retrieval.result import RetrievedChunk, SearchResult  # noqa: E402


# --------------------------------------------------------------------------- #
# Detector unit tests
# --------------------------------------------------------------------------- #

class TestDetectAct:
    def test_canonical_name(self):
        assert detect_act("What does the Indian Contract Act, 1872 say about compensation?") == (
            "Indian Contract Act, 1872"
        )

    def test_alias(self):
        assert detect_act("Section 55 of the FSS Act") == "Food Safety and Standards Act, 2006"

    def test_alias_water_act(self):
        assert detect_act("Powers under the water act") == "Water (Prevention and Control of Pollution) Act, 1974"

    def test_longest_match_wins(self):
        # "Plastic Waste Management (Amendment) Rules, 2022" must win over the
        # shorter "Plastic Waste Management Rules".
        q = "Changes under the Plastic Waste Management (Amendment) Rules, 2022"
        assert detect_act(q) == "Plastic Waste Management (Amendment) Rules, 2022"

    def test_no_act_returns_none(self):
        assert detect_act("What is adulteration?") is None


class TestDetectSection:
    def test_section(self):
        assert detect_section("What does Section 55 say?") == ("55", None)

    def test_sec_abbreviation(self):
        assert detect_section("Sec. 32 of the Act") == ("32", None)

    def test_u_s(self):
        assert detect_section("u/s 55") == ("55", None)

    def test_subsection(self):
        assert detect_section("Section 55(2) of the FSS Act") == ("55", "2")

    def test_no_section(self):
        assert detect_section("What is adulteration?") == (None, None)


class TestIdentifierQuery:
    def test_act_plus_section(self):
        q = "What is the penalty under Section 73 of the Indian Contract Act?"
        query, meta = identifier_query(q)
        assert query == "Indian Contract Act, 1872 section 73"
        assert meta["form"] == "act+section"

    def test_act_alone(self):
        query, meta = identifier_query("What does the Sale of Goods Act say about delivery?")
        assert query == "Sale of Goods Act, 1930"
        assert meta["form"] == "act"

    def test_section_alone(self):
        query, meta = identifier_query("What does Section 55 say?")
        assert query == "section 55"
        assert meta["form"] == "section"

    def test_none(self):
        query, meta = identifier_query("What is adulteration?")
        assert query is None
        assert meta["form"] == "none"


# --------------------------------------------------------------------------- #
# HybridRetriever identifier-arm fusion
# --------------------------------------------------------------------------- #

def _chunk(cid: str, text: str = "") -> RetrievedChunk:
    return RetrievedChunk(chunk_id=cid, score=1.0, text=text or cid)


class _FakeDense:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    def search(self, query, top_k=10, filters=None, **kw):
        self.calls.append(("dense", query, filters))
        return SearchResult(query=query, query_type="", chunks=self._chunks[:top_k], total=len(self._chunks))


class _FakeSparse:
    def __init__(self, by_query):
        self._by_query = by_query
        self.calls = []

    def retrieve(self, query, top_k=10, filters=None, **kw):
        self.calls.append(("sparse", query, filters))
        return SearchResult(
            query=query, query_type="", chunks=self._by_query.get(query, [])[:top_k],
            total=len(self._by_query.get(query, [])),
        )


class TestHybridIdentifierArm:
    def _make(self, ident_chunks):
        dense = _FakeDense([_chunk("d1"), _chunk("d2")])
        sparse = _FakeSparse({"q": [_chunk("s1"), _chunk("s2")], "contract act section 73": ident_chunks})
        from app.rag.retrieval.hybrid_retriever import HybridRetriever

        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        return hybrid, dense, sparse

    def test_identifier_chunk_surfaces_in_fused_result(self):
        hybrid, _d, _s = self._make([_chunk("i1"), _chunk("i2")])
        result = hybrid.retrieve("q", top_k=10, identifier_query="contract act section 73")
        ids = [c.chunk_id for c in result.chunks]
        assert "i1" in ids
        assert "i2" in ids
        # identifier arm ran without payload filters (lexical value)
        assert ("sparse", "contract act section 73", None) in _s.calls

    def test_identifier_arm_reranks_above_tail(self):
        hybrid, _d, _s = self._make([_chunk("i1")])
        # dense and sparse both return "d1" so agreement keeps it at rank 1,
        # but a unique identifier chunk must still enter the fused list.
        result = hybrid.retrieve("q", top_k=10, identifier_query="contract act section 73")
        ids = [c.chunk_id for c in result.chunks]
        assert "i1" in ids
        assert ids[0] in ("d1", "s1")

    def test_no_identifier_query_keeps_old_behaviour(self):
        hybrid, dense, sparse = self._make([_chunk("i1")])
        result = hybrid.retrieve("q", top_k=10)
        ids = [c.chunk_id for c in result.chunks]
        assert "i1" not in ids  # identifier arm not queried
        sparse_calls = [c for c in sparse.calls if c[0] == "sparse"]
        assert all(q == "q" for _, q, _ in sparse_calls)

    def test_identifier_arm_failure_is_best_effort(self):
        from app.rag.retrieval.hybrid_retriever import HybridRetriever

        class _BoomSparse(_FakeSparse):
            def retrieve(self, query, top_k=10, filters=None, **kw):
                if query != "q":
                    raise RuntimeError("sparse unavailable")
                return super().retrieve(query, top_k=top_k, filters=filters)

        dense = _FakeDense([_chunk("d1")])
        sparse = _BoomSparse({"q": [_chunk("s1")]})
        hybrid = HybridRetriever(dense=dense, sparse=sparse)
        result = hybrid.retrieve("q", top_k=10, identifier_query="boom query")
        # fused result still present — identifier failure degrades gracefully
        ids = [c.chunk_id for c in result.chunks]
        assert "d1" in ids and "s1" in ids


# --------------------------------------------------------------------------- #
# run_retrieval_pipeline wiring
# --------------------------------------------------------------------------- #

class TestPipelineIdentifier:
    def _patch(self, monkeypatch):
        """Replace the pipeline's lazily-imported classes with recorders."""
        import app.rag.retrieval as retrieval_mod
        from app.rag.tasks import _flag_enabled

        recorded = {}

        class FakeClassifier:
            @staticmethod
            def classify(query):
                return retrieval_mod.QueryType.SECTION_LOOKUP if "Section" in query else retrieval_mod.QueryType.GENERAL_QA

        class FakeParser:
            @staticmethod
            def parse(query, qtype):
                return {}

        class FakeReranker:
            def rerank(self, query, chunks, top_k=None):
                return chunks[:top_k] if top_k is not None else chunks

        class FakeHybrid:
            def __init__(self, dense, sparse, reranker=None):
                pass

            def retrieve(self, query, top_k=10, filters=None, identifier_query=None):
                recorded["query"] = query
                recorded["identifier_query"] = identifier_query
                return SearchResult(
                    query=query, query_type="", chunks=[_chunk("c1")], total=1, latency_ms=1
                )

        class FakeLogger:
            def log(self, **kw):
                return None

        monkeypatch.setattr(retrieval_mod, "HybridRetriever", FakeHybrid)
        monkeypatch.setattr(retrieval_mod, "QueryClassifier", FakeClassifier)
        monkeypatch.setattr(retrieval_mod, "QueryParser", FakeParser)
        monkeypatch.setattr(retrieval_mod, "Reranker", FakeReranker)
        monkeypatch.setattr("app.rag.retrieval.logger.RetrievalLogger", FakeLogger)
        monkeypatch.setattr("app.rag.tasks._identifier_route_enabled", lambda: True)
        return recorded

    def test_identifier_meta_and_query_reach_hybrid(self, monkeypatch):
        from app.rag.tasks import run_retrieval_pipeline

        recorded = self._patch(monkeypatch)
        result = run_retrieval_pipeline(
            "What is the penalty under Section 73 of the Indian Contract Act?", top_k=5
        )
        assert recorded["identifier_query"] == "Indian Contract Act, 1872 section 73"
        assert result["identifier"]["form"] == "act+section"
        assert result["identifier"]["section"] == "73"

    def test_section_only_query(self, monkeypatch):
        from app.rag.tasks import run_retrieval_pipeline

        recorded = self._patch(monkeypatch)
        run_retrieval_pipeline("What does Section 55 say?", top_k=5)
        assert recorded["identifier_query"] == "section 55"

    def test_no_identifiers_disables_arm(self, monkeypatch):
        from app.rag.tasks import run_retrieval_pipeline

        recorded = self._patch(monkeypatch)
        result = run_retrieval_pipeline("What is adulteration?", top_k=5)
        assert recorded["identifier_query"] is None
        assert result["identifier"]["form"] == "none"

    def test_flag_off_disables_arm(self, monkeypatch):
        from app.rag.tasks import run_retrieval_pipeline

        recorded = self._patch(monkeypatch)
        monkeypatch.setattr("app.rag.tasks._identifier_route_enabled", lambda: False)
        run_retrieval_pipeline("What is the penalty under Section 73 of the Indian Contract Act?", top_k=5)
        assert recorded["identifier_query"] is None
        assert recorded["query"] == "What is the penalty under Section 73 of the Indian Contract Act?"
