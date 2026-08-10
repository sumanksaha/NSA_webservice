"""Dedicated batch evaluation tests (§6.2 test_eval_batch.py).

Tests :class:`EvalRunner.evaluate_batch` over a multi-query dataset with
a stub pipeline, verifying metric aggregation, MRR, error handling, and
summary statistics.
"""

from __future__ import annotations

from app.rag.evaluation import EvalRunner
from app.rag.retrieval.result import RetrievedChunk


def _chunk(cid, score, text, section=None):
    return RetrievedChunk(
        chunk_id=cid, score=score, text=text, section_number=section,
        document_title="FSS Act", document_type="act", authority="FSSAI",
    )


def _pipeline_factory(chunks, answer="Section 55 requires a food business license."):
    """Build a pipeline callable that uses the given chunks + answer."""
    def pipeline(query):
        return {
            "answer": answer,
            "retrieved_chunks": chunks,
            "cited_chunk_ids": [c.chunk_id for c in chunks if c.section_number],
            "query_type": "section_lookup",
        }
    return pipeline


class TestEvalBatch:
    def _dataset(self):
        chunks = [
            _chunk("c0", 0.95, "Section 55 of the FSS Act, 2006 requires food businesses to obtain a license.", "55"),
            _chunk("c1", 0.85, "Section 3(1)(a) imposes penalties for non-compliance.", "3(1)(a)"),
            _chunk("c2", 0.70, "The FSSAI is the regulatory authority under the Act.", None),
        ]
        return chunks, [
            {"query": "What does Section 55 say?",
             "expected_answer": "Section 55 requires licensing.",
             "expected_citations": ["c0"],
             "query_type": "section_lookup"},
            {"query": "What are the penalties under Section 3?",
             "expected_answer": "Section 3(1)(a) imposes penalties.",
             "expected_citations": ["c1"],
             "query_type": "section_lookup"},
            {"query": "Who is the regulatory authority?",
             "expected_answer": "FSSAI is the authority.",
             "expected_citations": ["c2"],
             "query_type": "general_qa"},
            {"query": "What does Section 55 say about licensing?",
             "expected_answer": "Licensing is required under Section 55.",
             "expected_citations": ["c0"],
             "query_type": "section_lookup"},
            {"query": "Is there a central authority?",
             "expected_answer": "Yes, FSSAI.",
             "expected_citations": ["c2"],
             "query_type": "general_qa"},
        ]

    def test_batch_over_five_queries(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        assert report["summary"]["total"] == 5
        assert report["summary"]["errors"] == 0
        assert len(report["results"]) == 5

    def test_all_metrics_populated(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        for result in report["results"]:
            assert "error" not in result
            assert "faithfulness" in result["metrics"]
            assert "answer_relevance" in result["metrics"]
            assert "context_precision" in result["metrics"]
            assert "context_recall" in result["metrics"]
            assert "citation_recall" in result["metrics"]
            assert "groundedness" in result["metrics"]

    def test_mrr_in_results(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        for result in report["results"]:
            assert "retrieval_mrr" in result
            assert 0.0 <= result["retrieval_mrr"] <= 1.0

    def test_mrr_zero_for_unretrieved(self):
        chunks, entries = self._dataset()
        # Modify one entry to expect a chunk not in retrieved set
        entries[0]["expected_citations"] = ["ghost"]
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        # First query expected "ghost" which isn't retrieved => MRR = 0
        assert report["results"][0]["retrieval_mrr"] == 0.0

    def test_summary_averages(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        summary = report["summary"]
        for name in ["faithfulness", "answer_relevance", "context_precision",
                      "context_recall", "citation_recall", "groundedness"]:
            key = f"{name}_avg"
            assert key in summary
            avg = summary[key]
            assert avg is not None
            assert 0.0 <= avg <= 1.0

    def test_avg_latency(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        assert report["summary"]["latency_avg_ms"] >= 0

    def test_error_isolation(self):
        """One failing query doesn't break the batch."""
        chunks, entries = self._dataset()

        def flaky_pipeline(query):
            if "authority" in query:
                raise RuntimeError("transient failure")
            return _pipeline_factory(chunks)(query)

        runner = EvalRunner(pipeline_fn=flaky_pipeline)
        report = runner.evaluate_batch(entries, persist=False)
        assert report["summary"]["total"] == 5
        assert report["summary"]["errors"] == 2  # 2 queries contain "authority"
        assert report["summary"]["errors"] + sum(
            1 for r in report["results"] if "error" not in r
        ) == 5

    def test_eval_run_id_passthrough(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, eval_run_id="my-run-123", persist=False)
        assert report["eval_run_id"] == "my-run-123"

    def test_generated_run_id_if_none(self):
        chunks, entries = self._dataset()
        runner = EvalRunner(pipeline_fn=_pipeline_factory(chunks))
        report = runner.evaluate_batch(entries, persist=False)
        assert report["eval_run_id"]  # non-empty
        import re as _re
        # Should be a UUID-like string
        assert _re.match(r"[0-9a-f]{8}-[0-9a-f]{4}", report["eval_run_id"])

    def test_grounded_answer_scores_high(self):
        """When answer matches chunks, faithfulness/groundedness should be > 0.4."""
        chunks = [
            _chunk("c0", 0.95, "Section 55 of the FSS Act requires a food business license.", "55"),
        ]
        runner = EvalRunner(
            pipeline_fn=_pipeline_factory(
                chunks,
                answer="Based on the context, Section 55 requires a food business license [1].",
            )
        )
        result = runner.evaluate_one(
            query="What does Section 55 say?",
            expected_answer="Section 55 requires licensing.",
            expected_citations=["c0"],
        )
        assert result["metrics"]["faithfulness"] > 0.4
        assert result["metrics"]["groundedness"] > 0.4
