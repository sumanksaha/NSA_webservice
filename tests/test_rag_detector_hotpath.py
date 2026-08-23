"""Hot-path wiring tests for the Phase 3 HallucinationDetector (2026-08-23).

``run_generation_pipeline`` now runs the claim-level detector over every
generated answer (when ``RAG_HALLUCINATION_DETECTOR`` is on, the default),
merging its verdict into the response under ``verification`` and escalating
claim-level hallucinations the heuristic sanitizer missed.

All pipeline entry points are monkeypatched — no Qdrant / network required.
"""

from __future__ import annotations

import pytest

from tests.test_rag_routes import _setup_test_env


def _chunk() -> dict:
    return {
        "chunk_id": "c1",
        "score": 0.9,
        "text": "The Food Safety and Standards Act, 2006 prohibits unsafe food.",
        "document_title": "FSS Act",
    }


@pytest.fixture()
def app_env():
    """App + pushed context so cfg resolves through Flask config."""
    app, _client, ctx = _setup_test_env()
    yield app
    ctx.pop()


def _patch_retrieval(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {"chunks": [_chunk()], "query_type": "general"},
    )


class TestDetectorHotPath:
    def test_verification_block_present_by_default(self, app_env, monkeypatch):
        """Every live answer carries a ``verification`` block (flag defaults on)."""
        _patch_retrieval(monkeypatch)
        from app.rag.tasks import run_generation_pipeline

        result = run_generation_pipeline(query="is unsafe food prohibited?")
        v = result["verification"]
        assert v["enabled"] is True
        assert set(v) >= {
            "detected",
            "groundedness_score",
            "claims_total",
            "claims_verified",
            "claims_unverified",
            "llm_verified",
            "confidence",
            "escalated_claims",
        }
        assert isinstance(v["claims_total"], int)

    def test_detector_escalates_claim_level_hallucinations(self, app_env, monkeypatch):
        """Claims flagged by the detector but missed by the sanitizer surface."""
        _patch_retrieval(monkeypatch)
        from app.rag.tasks import run_generation_pipeline
        from app.rag.verification.hallucination_detector import HallucinationReport

        def fake_detect(self, response_text, chunks, citations=None):
            return HallucinationReport(
                detected=True,
                groundedness_score=0.2,
                claims=[],
                verified_claims=[],
                unverified_claims=[],
                hallucinated_claims=["Unicorns regulate FSSAI"],
                llm_verified=False,
                confidence=0.9,
            )

        monkeypatch.setattr("app.rag.verification.HallucinationDetector.detect", fake_detect)

        result = run_generation_pipeline(query="who regulates food safety?")
        assert result["hallucination_detected"] is True
        assert "Unicorns regulate FSSAI" in result["hallucinated_claims"]
        assert result["verification"]["escalated_claims"] == 1
        assert result["verification"]["detected"] is True

    def test_detector_failure_does_not_break_query(self, app_env, monkeypatch):
        """A crashing detector degrades to an error note — answer still returned."""
        _patch_retrieval(monkeypatch)

        def boom(self, *args, **kwargs):
            raise RuntimeError("detector exploded")

        monkeypatch.setattr("app.rag.verification.HallucinationDetector.detect", boom)
        from app.rag.tasks import run_generation_pipeline

        result = run_generation_pipeline(query="q")
        assert "error" in result["verification"]
        assert result["answer"]  # generation output untouched

    def test_flag_off_skips_detection(self, app_env, monkeypatch):
        """RAG_HALLUCINATION_DETECTOR=false → no verification block."""
        _patch_retrieval(monkeypatch)
        app_env.config["RAG_HALLUCINATION_DETECTOR"] = False
        from app.rag.tasks import run_generation_pipeline

        result = run_generation_pipeline(query="q")
        assert result["verification"] is None
