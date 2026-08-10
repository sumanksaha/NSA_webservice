"""Tests for Phase 4 RAG evaluation — metrics, EvalRunner, EvalStorage.

Metrics and EvalRunner tests run without DB (pure functions).
EvalStorage tests require a DB via create_app().
"""

from __future__ import annotations

from app.rag.evaluation import (
    AnswerRelevanceMetric,
    CitationRecallMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    EvalRunner,
    EvalScore,
    EvalStorage,
    FaithfulnessMetric,
    GroundednessMetric,
)
from app.rag.evaluation.report import EvalReport, EvalSummary
from app.rag.retrieval.result import RetrievedChunk


def _chunks(n=3):
    return [
        RetrievedChunk(
            chunk_id=f"c{i}", score=0.9 - i * 0.1,
            text=f"Section {i+1} of the FSS Act, 2006 governs food licensing. "
            f"Food businesses must obtain a license under these provisions.",
            section_number=str(i + 1),
            document_title="FSS Act 2006", document_type="act", authority="FSSAI",
        )
        for i in range(n)
    ]


# --------------------------------------------------------------------------- #
# FaithfulnessMetric
# --------------------------------------------------------------------------- #
class TestFaithfulnessMetric:
    def test_all_claims_supported(self):
        chunks = _chunks(3)
        answer = "Section 1 requires a food business license. Section 2 governs penalties."
        score = FaithfulnessMetric().compute(answer, chunks, query="license")
        assert 0.0 <= score.score <= 1.0
        assert score.score > 0.3

    def test_no_chunks(self):
        score = FaithfulnessMetric().compute("some answer", [], query="q")
        assert score.score == 0.0

    def test_no_claims_trivial(self):
        chunks = _chunks(2)
        score = FaithfulnessMetric().compute("yes", chunks, query="q")
        assert score.score == 1.0

    def test_low_faithfulness(self):
        chunks = _chunks(2)
        answer = "Section 999 is about purple elephants and unicorns."
        score = FaithfulnessMetric().compute(answer, chunks, query="license")
        assert score.score < 0.5


# --------------------------------------------------------------------------- #
# AnswerRelevanceMetric
# --------------------------------------------------------------------------- #
class TestAnswerRelevanceMetric:
    def test_high_relevance(self):
        answer = "Section 55 of the FSS Act requires food businesses to obtain a license."
        query = "What does Section 55 say?"
        score = AnswerRelevanceMetric().compute(answer, query)
        assert score.score > 0.3

    def test_low_relevance(self):
        answer = "Purple elephants dance on Tuesdays."
        query = "What does Section 55 say?"
        score = AnswerRelevanceMetric().compute(answer, query)
        assert score.score < 0.5

    def test_with_expected_answer(self):
        answer = "Section 55 governs licensing."
        expected = "Section 55 deals with licensing requirements."
        score = AnswerRelevanceMetric().compute(answer, "irrelevant query", expected)
        assert score.score > 0.3

    def test_empty(self):
        score = AnswerRelevanceMetric().compute("", "query")
        assert score.score == 0.0


# --------------------------------------------------------------------------- #
# ContextPrecisionMetric
# --------------------------------------------------------------------------- #
class TestContextPrecisionMetric:
    def test_all_relevant(self):
        chunks = _chunks(3)
        score = ContextPrecisionMetric().compute("food business license", chunks)
        assert score.score > 0.5
        assert score.detail["total"] == 3

    def test_no_chunks(self):
        score = ContextPrecisionMetric().compute("query", [])
        assert score.score == 0.0

    def test_irrelevant_chunks(self):
        chunks = [
            RetrievedChunk(chunk_id="c0", score=0.9, text="about quantum physics and string theory"),
            RetrievedChunk(chunk_id="c1", score=0.8, text="discussing medieval poetry and knights"),
        ]
        score = ContextPrecisionMetric().compute("food safety licensing section 55", chunks)
        assert score.score < 0.5


# --------------------------------------------------------------------------- #
# ContextRecallMetric
# --------------------------------------------------------------------------- #
class TestContextRecallMetric:
    def test_full_recall(self):
        chunks = _chunks(3)
        relevant = ["c0", "c1", "c2"]
        score = ContextRecallMetric().compute(relevant, chunks)
        assert score.score == 1.0

    def test_missed_chunks(self):
        chunks = [_chunks(3)[0]]
        relevant = ["c0", "c1", "c2"]
        score = ContextRecallMetric().compute(relevant, chunks)
        assert score.score == round(1 / 3, 4)

    def test_no_expected(self):
        score = ContextRecallMetric().compute([], _chunks(2))
        assert score.score == 1.0


# --------------------------------------------------------------------------- #
# CitationRecallMetric
# --------------------------------------------------------------------------- #
class TestCitationRecallMetric:
    def test_all_cited_in_retrieved(self):
        chunks = _chunks(3)
        cited = ["c0", "c1"]
        score = CitationRecallMetric().compute(cited, chunks)
        assert score.score == 1.0

    def test_cited_not_retrieved(self):
        chunks = _chunks(1)
        cited = ["c0", "ghost"]
        score = CitationRecallMetric().compute(cited, chunks)
        assert score.score == 0.5

    def test_no_citations(self):
        score = CitationRecallMetric().compute([], _chunks(2))
        assert score.score == 1.0


# --------------------------------------------------------------------------- #
# GroundednessMetric
# --------------------------------------------------------------------------- #
class TestGroundednessMetric:
    def test_fully_grounded(self):
        chunks = _chunks(3)
        answer = "Section 1 requires a license. Section 2 sets penalties."
        score = GroundednessMetric().compute(answer, chunks)
        assert score.score > 0.5

    def test_not_grounded(self):
        chunks = _chunks(2)
        answer = "Section 999 is about purple elephants."
        score = GroundednessMetric().compute(answer, chunks)
        assert score.score < 0.5

    def test_no_claims(self):
        chunks = _chunks(2)
        score = GroundednessMetric().compute("yes", chunks)
        assert score.score == 1.0

    def test_no_chunks(self):
        score = GroundednessMetric().compute("answer", [])
        assert score.score == 0.0


# --------------------------------------------------------------------------- #
# EvalRunner
# --------------------------------------------------------------------------- #
def _make_pipeline(answer="Section 55 requires a license.", chunk_ids=None, cited_ids=None):
    if chunk_ids is None:
        chunk_ids = ["c0", "c1", "c2"]
    if cited_ids is None:
        cited_ids = ["c0", "c1"]

    def pipeline(query):
        return {
            "answer": answer,
            "retrieved_chunks": [
                RetrievedChunk(
                    chunk_id=cid, score=0.9, text=f"Section {i+1} of the FSS Act governs licensing.",
                    section_number=str(i + 1), document_title="FSS Act", document_type="act",
                )
                for i, cid in enumerate(chunk_ids)
            ],
            "cited_chunk_ids": cited_ids,
            "query_type": "section_lookup",
        }
    return pipeline


class TestEvalRunner:
    def test_evaluate_one(self):
        pipeline = _make_pipeline()
        runner = EvalRunner(pipeline_fn=pipeline)
        result = runner.evaluate_one(
            query="What does Section 55 say?",
            expected_answer="Section 55 requires licensing.",
            expected_citations=["c0"],
            query_type="section_lookup",
        )
        assert result["query"] == "What does Section 55 say?"
        assert result["answer"]
        assert "faithfulness" in result["metrics"]
        assert "groundedness" in result["metrics"]
        assert result["retrieval_mrr"] > 0

    def test_evaluate_one_empty_chunks(self):
        def pipeline(query):
            return {"answer": "No relevant info.", "retrieved_chunks": [], "cited_chunk_ids": []}
        runner = EvalRunner(pipeline_fn=pipeline)
        result = runner.evaluate_one("q", "expected", ["c0"])
        assert result["metrics"]["context_precision"] == 0.0

    def test_mrr_first_rank(self):
        pipeline = _make_pipeline()
        runner = EvalRunner(pipeline_fn=pipeline)
        result = runner.evaluate_one("q", "expected", ["c0"])
        assert result["retrieval_mrr"] == 1.0

    def test_mrr_not_found(self):
        def pipeline(query):
            return {"answer": "a", "retrieved_chunks": _chunks(2)[:1], "cited_chunk_ids": []}
        runner = EvalRunner(pipeline_fn=pipeline)
        result = runner.evaluate_one("q", "expected", ["ghost"])
        assert result["retrieval_mrr"] == 0.0

    def test_all_metrics_computed(self):
        pipeline = _make_pipeline()
        runner = EvalRunner(pipeline_fn=pipeline)
        result = runner.evaluate_one("q", "expected answer about Section 55", ["c0"])
        expected_metrics = {
            "faithfulness", "answer_relevance", "context_precision",
            "context_recall", "citation_recall", "groundedness",
        }
        assert set(result["metrics"].keys()) == expected_metrics

    def test_batch_evaluation(self):
        pipeline = _make_pipeline()
        runner = EvalRunner(pipeline_fn=pipeline)
        entries = [
            {"query": "What does Section 1 say?", "expected_answer": "License req.",
             "expected_citations": ["c0"], "query_type": "section_lookup"},
            {"query": "Section 2?", "expected_answer": "Penalties.",
             "expected_citations": ["c1"], "query_type": "section_lookup"},
        ]
        report = runner.evaluate_batch(entries, persist=False)
        assert report["eval_run_id"]
        assert len(report["results"]) == 2
        summary = report["summary"]
        assert summary["total"] == 2
        assert summary["errors"] == 0
        assert "faithfulness_avg" in summary
        assert "mrr_avg" in summary

    def test_batch_handles_errors(self):
        def bad_pipeline(query):
            raise RuntimeError("Qdrant is down")
        runner = EvalRunner(pipeline_fn=bad_pipeline)
        entries = [{"query": "q1", "expected_answer": "a1", "expected_citations": []}]
        report = runner.evaluate_batch(entries, persist=False)
        assert report["summary"]["errors"] == 1
        assert "error" in report["results"][0]

    def test_summary_metrics(self):
        pipeline = _make_pipeline()
        runner = EvalRunner(pipeline_fn=pipeline)
        entries = [
            {"query": "q", "expected_answer": "a", "expected_citations": ["c0"]},
            {"query": "q2", "expected_answer": "a2", "expected_citations": ["c0"]},
        ]
        report = runner.evaluate_batch(entries, persist=False)
        summary = report["summary"]
        assert summary["total"] == 2
        for name in ["faithfulness", "answer_relevance", "context_precision",
                      "context_recall", "citation_recall", "groundedness"]:
            assert f"{name}_avg" in summary

    def test_metric_detail_and_explanations(self):
        pipeline = _make_pipeline()
        runner = EvalRunner(pipeline_fn=pipeline)
        result = runner.evaluate_one("q", "expected", ["c0"])
        assert "metric_details" in result
        assert "metric_explanations" in result
        assert "faithfulness" in result["metric_explanations"]


# --------------------------------------------------------------------------- #
# EvalReport / EvalSummary
# --------------------------------------------------------------------------- #
class TestEvalReport:
    def test_report_dict(self):
        report = EvalReport(
            eval_run_id="r1",
            results=[{"query": "q", "metrics": {"faithfulness": 0.8}}],
            summary=EvalSummary(total=1, errors=0, metric_averages={"faithfulness": 0.8}),
        )
        d = report.to_dict()
        assert d["eval_run_id"] == "r1"
        assert len(d["results"]) == 1
        assert d["summary"]["total"] == 1

    def test_summary_dict(self):
        s = EvalSummary(total=5, errors=1, mrr_avg=0.8, latency_avg_ms=100.0, passed=4)
        d = s.to_dict()
        assert d["total"] == 5
        assert d["errors"] == 1
        assert d["mrr_avg"] == 0.8

    def test_eval_score(self):
        score = EvalScore("faithfulness", 0.85, "explanation", detail={"x": 1})
        assert score.score == 0.85
        assert score.name == "faithfulness"


# --------------------------------------------------------------------------- #
# EvalStorage — DB required
# --------------------------------------------------------------------------- #
class TestEvalStorage:
    def _setup(self):
        from app import create_app
        from app.extensions import db
        from app.models import FSO, User
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        ctx = app.app_context()
        ctx.push()
        db.drop_all()
        db.create_all()
        user = User(username="evaluser", password_hash="pbkdf2:sha256$test$dummy")
        db.session.add(user)
        db.session.add(FSO(fso_name="Test Officer"))
        db.session.commit()
        return ctx

    def _teardown(self, ctx):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        ctx.pop()

    def test_save_and_retrieve_result(self):
        ctx = self._setup()
        try:
            storage = EvalStorage(actor="test-agent")
            result = storage.save_result(
                eval_run_id="run-1",
                query="What is Section 55?",
                expected_answer="Licensing requirements.",
                expected_citations=["c0"],
                actual_answer="Section 55 governs licensing.",
                actual_citations=["c0"],
                metrics={"faithfulness": 0.9, "groundedness": 0.85},
                retrieval_mrr=1.0,
                latency_ms=150,
            )
            assert result is not None
            assert result.faithfulness_score == 0.9
            assert result.avg_score == 0.875
            assert result.passed is True

            retrieved = storage.list_results("run-1")
            assert len(retrieved) == 1
            assert retrieved[0].faithfulness_score == 0.9
        finally:
            self._teardown(ctx)

    def test_save_dataset_entry(self):
        ctx = self._setup()
        try:
            storage = EvalStorage()
            entry = storage.save_dataset_entry(
                name="test_suite",
                query="What does Section 55 say?",
                query_type="section_lookup",
                expected_answer="Licensing requirements.",
                expected_section="55",
                expected_citations=["c0", "c1"],
                difficulty="hard",
            )
            assert entry is not None
            assert entry.difficulty == "hard"
            assert entry.expected_section == "55"

            listed = storage.list_dataset("test_suite")
            assert len(listed) == 1
        finally:
            self._teardown(ctx)

    def test_passed_threshold(self):
        ctx = self._setup()
        try:
            storage = EvalStorage()
            result = storage.save_result(
                eval_run_id="run-2",
                query="q",
                metrics={"faithfulness": 0.3},
            )
            assert result.passed is False
        finally:
            self._teardown(ctx)

    def test_avg_score_none_on_empty_metrics(self):
        ctx = self._setup()
        try:
            storage = EvalStorage()
            result = storage.save_result(
                eval_run_id="run-3", query="q", metrics={},
            )
            assert result.avg_score is None
            assert result.passed is False
        finally:
            self._teardown(ctx)
