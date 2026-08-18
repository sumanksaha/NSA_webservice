"""Tests for Phase 3+4 integration — end-to-end hallucination detection
and evaluation with the generation pipeline.

Tests the full chain: QueryClassifier → HybridRetriever (stub) →
GroundedGenerationService → HallucinationDetector → EvalRunner.
"""

from __future__ import annotations

from app.rag.evaluation import EvalRunner
from app.rag.generation import GroundedGenerationService
from app.rag.generation.llm_client import GroundedLLMClient
from app.rag.retrieval.result import RetrievedChunk
from app.rag.tasks import run_generation_pipeline
from app.rag.verification import HallucinationDetector


def _stub_chunks():
    """Chunks that match the stub LLM's canned response."""
    return [
        RetrievedChunk(
            chunk_id="c0", score=0.95,
            text="Section 55 of the FSS Act, 2006 requires every food "
            "business to obtain a license from the Food Safety Officer.",
            section_number="55",
            document_title="FSS Act 2006", document_type="act", authority="FSSAI",
        ),
        RetrievedChunk(
            chunk_id="c1", score=0.85,
            text="Section 3(1)(a) imposes a penalty of 100% of turnover "
            "or Rs. 50,000 for violations.",
            section_number="3(1)(a)",
            document_title="FSS Act 2006", document_type="act", authority="FSSAI",
        ),
        RetrievedChunk(
            chunk_id="c2", score=0.70,
            text="The FSSAI is the regulatory authority under the Act.",
            section_number=None,
            document_title="FSS Act 2006", document_type="act", authority="FSSAI",
        ),
    ]


class TestGenerationPlusVerification:
    @staticmethod
    def _force_stub(monkeypatch) -> None:
        """Pin stub LLM mode regardless of the local .env (a real key with
        RAG_USE_STUB_LLM=false would otherwise make live API calls)."""
        monkeypatch.setenv("RAG_USE_STUB_LLM", "true")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def test_still_response_not_hallucinated(self, monkeypatch):
        """A response citing real sections should not be flagged."""
        self._force_stub(monkeypatch)
        chunks = _stub_chunks()
        stub_response = (
            "Based on the provided context, Section 55 of the FSS Act "
            "requires food businesses to obtain a license [1]. "
            "The FSSAI is the regulatory authority [3]."
        )
        client = GroundedLLMClient(stub_response=stub_response)
        service = GroundedGenerationService(llm_client=client)
        rag_response = service.generate("What does Section 55 say?", chunks)

        detector = HallucinationDetector()
        report = detector.detect(
            rag_response.answer, chunks, citations=rag_response.citations
        )
        assert not report.detected
        assert report.groundedness_score > 0.3

    def test_hallucinated_section_detected(self, monkeypatch):
        """A response citing a non-existent section should be flagged."""
        self._force_stub(monkeypatch)
        chunks = _stub_chunks()
        stub_response = (
            "Section 999 of the FSS Act imposes a penalty of 10000 gold coins [1]."
        )
        client = GroundedLLMClient(stub_response=stub_response)
        service = GroundedGenerationService(llm_client=client)
        rag_response = service.generate("What does Section 999 say?", chunks)

        detector = HallucinationDetector()
        report = detector.detect(
            rag_response.answer, chunks, citations=rag_response.citations
        )
        assert report.detected
        assert report.groundedness_score < 0.5
        assert len(report.hallucinated_claims) >= 1

    def test_empty_chunks_detection(self, monkeypatch):
        self._force_stub(monkeypatch)
        _stub_chunks()
        stub_response = "Section 55 requires a license."
        client = GroundedLLMClient(stub_response=stub_response)
        service = GroundedGenerationService(llm_client=client)
        rag_response = service.generate("q", [])
        # Empty chunks => empty response
        detector = HallucinationDetector()
        report = detector.detect(rag_response.answer, [])
        assert report.detected


class TestEvalRunnerWithStubPipeline:
    def test_full_eval_batch(self):
        chunks = _stub_chunks()

        def pipeline(query):
            return {
                "answer": "Section 55 requires a license. The FSSAI is the authority.",
                "retrieved_chunks": chunks,
                "cited_chunk_ids": ["c0", "c2"],
                "query_type": "section_lookup",
            }

        entries = [
            {"query": "What does Section 55 say?",
             "expected_answer": "Section 55 requires licensing.",
             "expected_citations": ["c0"],
             "query_type": "section_lookup"},
            {"query": "Who is the regulatory authority?",
             "expected_answer": "FSSAI is the authority.",
             "expected_citations": ["c2"],
             "query_type": "general_qa"},
        ]
        runner = EvalRunner(pipeline_fn=pipeline)
        report = runner.evaluate_batch(entries, persist=False)

        assert report["summary"]["total"] == 2
        assert report["summary"]["errors"] == 0
        for name in ["faithfulness", "groundedness", "answer_relevance",
                      "context_precision", "context_recall", "citation_recall"]:
            assert f"{name}_avg" in report["summary"]

    def test_mrr_computation(self):
        chunks = _stub_chunks()
        def pipeline(query):
            return {"answer": "Section 55 requires a license.", "retrieved_chunks": chunks,
                    "cited_chunk_ids": ["c0"]}
        runner = EvalRunner(pipeline_fn=pipeline)
        # c0 is rank 1 in retrieved chunks (sorted by score already)
        result = runner.evaluate_one("q", "expected", ["c0"], "section_lookup")
        assert result["retrieval_mrr"] == 1.0


class TestRunGenerationPipelineIntegration:
    def test_pipeline_returns_all_fields(self, monkeypatch):
        # Pin stub mode: run_generation_pipeline builds its own LLM client and
        # must never hit the real API in tests, whatever .env says.
        monkeypatch.setenv("RAG_USE_STUB_LLM", "true")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        chunks = [c.to_dict() for c in _stub_chunks()]
        result = run_generation_pipeline(
            query="What does Section 55 say?",
            chunks=chunks,
            query_type="section_lookup",
        )
        assert result["query"] == "What does Section 55 say?"
        assert result["answer"]
        assert result["llm_model"].startswith("stub")
        assert result["answer"]
        assert result["groundedness_score"] >= 0.0
        assert "token_usage" in result
        assert "citations" in result
        assert "debug" in result
        assert "hallucination_detected" in result
