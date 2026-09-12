"""Characterization tests for ``run_generation_pipeline`` (2026-09-12).

Pinned before splitting the 291-line function into stage functions — these
lock the *current* observable behavior of the paths the existing
``tests/test_rag_generation.py`` suite does not cover:

1. Compound-query decomposition → per-sub-query retrieval → merge, dedup,
   score-sort, top-k, and ``sub_queries`` echo.
2. Hallucination-detector verification block (default-on): verification
   dict shape and hallucinated-claim escalation.
3. Citation validation block: verdict dict shape and invalid-citation
   escalation.

These are characterization tests: they assert what the code does today, not
what it ideally should do. Change them only alongside intentional behavior
changes.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.tasks import run_generation_pipeline

_CHUNK = {
    "chunk_id": "c1",
    "score": 0.9,
    "text": "Section 55 text",
    "section_number": "55",
    "document_title": "FSS Act",
    "document_type": "act",
    "authority": "FSSAI",
}


def _chunk(cid: str, score: float) -> dict:
    d = dict(_CHUNK)
    d["chunk_id"] = cid
    d["score"] = score
    return d


@pytest.fixture()
def no_kg(monkeypatch):
    """Keep both KG paths inert regardless of ambient env."""
    monkeypatch.delenv("RAG_KG_FUSION", raising=False)
    monkeypatch.delenv("RAG_KG_EXPANSION", raising=False)


class TestCompoundQueryDecomposition:
    def test_compound_query_merges_subquery_retrievals(self, no_kg, monkeypatch):
        """Compound query → one retrieval per sub-query, merged.

        Characterization note: ``sub_queries`` is written onto the internal
        ``retrieval_data`` but is *not* surfaced in the response dict — the
        merged metadata is discarded except latency and chunks.
        """
        calls: list[str] = []

        def _fake_retrieval(*, query, top_k, collection_name, filters, pipeline):
            calls.append(query)
            cid = "c_a" if "33" in query else "c_b"
            return {"chunks": [_chunk(cid, 0.8)], "query_type": "section_lookup", "retrieval_latency_ms": 1}

        monkeypatch.setattr("app.rag.tasks.run_retrieval_pipeline", _fake_retrieval)
        result = run_generation_pipeline(query="Section 33 and Section 38 penalties", chunks=None)
        assert sorted(calls) == ["Section 33 provisions", "Section 38 provisions"]
        assert result["sub_queries"] == ["Section 33 provisions", "Section 38 provisions"]
        ids = [c["chunk_id"] for c in result["retrieved_chunks"]]
        assert "c_a" in ids and "c_b" in ids

    def test_compound_merge_dedups_and_sorts_by_score(self, no_kg, monkeypatch):
        """Duplicate chunk across sub-queries kept once; output sorted desc."""
        seen: list[str] = []

        def _fake_retrieval(*, query, top_k, collection_name, filters, pipeline):
            seen.append(query)
            if "33" in query:
                return {"chunks": [_chunk("dup", 0.5), _chunk("only_a", 0.95)], "query_type": "x"}
            return {"chunks": [_chunk("dup", 0.5), _chunk("only_b", 0.2)], "query_type": "x"}

        monkeypatch.setattr("app.rag.tasks.run_retrieval_pipeline", _fake_retrieval)
        result = run_generation_pipeline(query="Section 33 and Section 38", chunks=None, top_k=10)
        ids = [c["chunk_id"] for c in result["retrieved_chunks"]]
        assert ids.count("dup") == 1
        scores = [c["score"] for c in result["retrieved_chunks"]]
        assert scores == sorted(scores, reverse=True)
        assert "only_a" == ids[0]

    def test_compound_topk_truncates_merged_pool(self, no_kg, monkeypatch):
        def _fake_retrieval(*, query, top_k, collection_name, filters, pipeline):
            cid = "c_a" if "33" in query else "c_b"
            return {"chunks": [_chunk(cid, 0.8)], "query_type": "x"}

        monkeypatch.setattr("app.rag.tasks.run_retrieval_pipeline", _fake_retrieval)
        result = run_generation_pipeline(query="Section 33 and Section 38", chunks=None, top_k=1)
        assert len(result["retrieved_chunks"]) == 1

    def test_single_query_does_not_run_compound_path(self, no_kg, monkeypatch):
        """A simple query hits retrieval exactly once (no decomposition)."""
        calls: list[str] = []

        def _fake_retrieval(*, query, top_k, collection_name, filters, pipeline):
            calls.append(query)
            return {"chunks": [_chunk("c1", 0.9)], "query_type": "x"}

        monkeypatch.setattr("app.rag.tasks.run_retrieval_pipeline", _fake_retrieval)
        result = run_generation_pipeline(query="Section 55 penalties", chunks=None)
        assert calls == ["Section 55 penalties"]
        assert "sub_queries" not in result  # simple query: decomposition never ran
        # And with pre-provided chunks (no retrieval at all) the key is absent too.
        result2 = run_generation_pipeline(query="Section 55 penalties", chunks=[_CHUNK])
        assert calls == ["Section 55 penalties"]  # no second retrieval
        assert "sub_queries" not in result2


class TestHallucinationVerification:
    def test_verification_block_present_by_default(self, no_kg):
        """Detector is default-on: verification dict present with its shape.

        ``citation_validation`` only appears when the answer carries
        citations — the default stub answer cites nothing.
        """
        result = run_generation_pipeline(query="Section 55?", chunks=[_CHUNK])
        v = result["verification"]
        assert v is not None
        assert v["enabled"] is True
        for key in ("detected", "groundedness_score", "claims_total", "claims_verified", "claims_unverified"):
            assert key in v
        assert "citation_validation" not in v  # no citations → block skipped

    def test_detector_failure_is_best_effort(self, no_kg):
        """A detector crash degrades to an error dict; the query still answers."""
        with patch("app.rag.verification.HallucinationDetector") as boom:
            boom.side_effect = RuntimeError("detector down")
            result = run_generation_pipeline(query="Section 55?", chunks=[_CHUNK])
        assert result["verification"] == {"enabled": True, "error": "detector down"}
        assert result["answer"]

    def test_detector_escalates_missed_claims(self, no_kg, monkeypatch):
        """Claims the sanitizer missed are appended, flagged, and counted."""
        import app.rag.tasks as tasks_mod

        fake_report = SimpleNamespace(
            detected=True,
            groundedness_score=0.42,
            claims=["unsupported claim"],
            verified_claims=[],
            unverified_claims=["unsupported claim"],
            hallucinated_claims=["unsupported claim"],
            llm_verified=False,
            confidence=0.3,
        )

        class _FakeDetector:
            def detect(self, answer, chunks, citations=None):
                return fake_report

        monkeypatch.setattr("app.rag.verification.HallucinationDetector", _FakeDetector)
        result = run_generation_pipeline(query="Section 55?", chunks=[_CHUNK])
        v = result["verification"]
        assert v["escalated_claims"] == 1
        assert "unsupported claim" in result["hallucinated_claims"]
        assert result["hallucination_detected"] is True


class TestCitationValidation:
    @staticmethod
    def _citing_llm(monkeypatch):
        """Force a citing answer — the citation block needs citations to run.

        The pipeline constructs ``GroundedGenerationService()`` itself, so
        patch ``GroundedLLMClient`` in the service module's namespace.
        """
        client = GroundedLLMClient(stub_response="Section 55 text [1]")
        monkeypatch.setattr(
            "app.rag.generation.grounded_service.GroundedLLMClient",
            lambda *a, **k: client,
        )

    def test_citation_validation_runs_on_citing_answer(self, no_kg, monkeypatch):
        """Citations present → ``citation_validation`` verdict in the dict."""
        self._citing_llm(monkeypatch)
        result = run_generation_pipeline(query="Section 55?", chunks=[_CHUNK])
        v = result["verification"]
        assert v["citation_validation"]["enabled"] is True
        for key in ("score", "valid", "invalid", "section_mismatches", "detail"):
            assert key in v["citation_validation"]

    def test_invalid_citation_escalates(self, no_kg, monkeypatch):
        """A citation that maps to no retrieved chunk is escalated."""
        self._citing_llm(monkeypatch)
        import app.rag.verification.citation_validator as cv_mod

        fake_report = SimpleNamespace(
            score=0.0,
            valid=[],
            invalid=["[99]"],
            section_mismatches=[],
            detail=[{"chunk_id": "ghost", "status": "invalid"}],
        )

        monkeypatch.setattr(cv_mod.CitationValidator, "validate", lambda self, c, ch: fake_report)
        result = run_generation_pipeline(query="Section 55?", chunks=[_CHUNK])
        v = result["verification"]
        assert v["citation_validation"]["invalid"] == 1
        assert any("citation ghost" in claim for claim in result["hallucinated_claims"])
        assert result["hallucination_detected"] is True

    def test_citation_validation_failure_is_best_effort(self, no_kg, monkeypatch):
        self._citing_llm(monkeypatch)
        import app.rag.verification.citation_validator as cv_mod

        def _boom(self, citations, chunks):
            raise RuntimeError("validator down")

        monkeypatch.setattr(cv_mod.CitationValidator, "validate", _boom)
        result = run_generation_pipeline(query="Section 55?", chunks=[_CHUNK])
        v = result["verification"]
        assert v["citation_validation"]["enabled"] is True
        assert "error" in v["citation_validation"]
        assert result["answer"]
