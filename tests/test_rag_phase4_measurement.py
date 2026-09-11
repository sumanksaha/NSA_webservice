"""Phase 4 measurement tests: gold decomposition benchmark, coverage metrics,
RAGAS-style reference metrics wiring, and shared text matching.

Pure-function tests — no retrieval backend, no LLM (the planner is the only
dependency and it is deterministic regex-based).
"""

from __future__ import annotations

from app.rag.evaluation.benchmark import DecompositionBenchmark
from app.rag.evaluation.gold_dataset import GOLD_DECOMPOSITION
from app.rag.evaluation.metrics import CoverageMetrics
from app.rag.evaluation.ragas_metrics import (
    AnswerRelevanceMetric,
    ContextRecallMetric,
    FaithfulnessMetric,
    GroundednessMetric,
)
from app.rag.evaluation.textmatch import chunk_id, chunk_text, content_tokens, token_coverage
from app.rag.evidence_task import EvidenceRequirement, EvidenceTask


# --------------------------------------------------------------------------- #
# textmatch
# --------------------------------------------------------------------------- #
class TestTextMatch:
    def test_content_tokens_drops_stopwords_keeps_digits(self):
        toks = content_tokens("What is Section 12 of the Act?")
        assert "section" in toks
        assert "12" in toks  # digit-bearing short token survives
        assert "the" not in toks
        assert "what" not in toks

    def test_token_coverage_empty_needles(self):
        assert token_coverage([], "some text") == 0.0

    def test_token_coverage_full_and_zero(self):
        assert token_coverage(["penalty"], "the penalty applies here") == 1.0
        assert token_coverage(["zebra"], "the penalty applies here") == 0.0

    def test_chunk_accessors_dict_and_object(self):
        from app.rag.retrieval.result import RetrievedChunk

        obj = RetrievedChunk(chunk_id="c1", score=0.9, text="hello")
        assert chunk_text(obj) == "hello"
        assert chunk_id(obj) == "c1"
        assert chunk_text({"text": "world"}) == "world"
        assert chunk_id({"chunk_id": "c2"}) == "c2"
        assert chunk_text({}) == ""
        assert chunk_id({}) == ""


# --------------------------------------------------------------------------- #
# Gold decomposition benchmark
# --------------------------------------------------------------------------- #
class TestGoldBenchmark:
    def test_gold_dataset_is_wellformed(self):
        assert GOLD_DECOMPOSITION, "gold dataset must not be empty"
        for entry in GOLD_DECOMPOSITION:
            assert entry["query"]
            assert entry["gold_task_kinds"]
            for kind in entry["gold_task_kinds"]:
                assert kind in {r.value for r in EvidenceRequirement}
            # Every dependency target must itself be a gold kind
            for kind, deps in entry["gold_dependencies"].items():
                assert kind in entry["gold_task_kinds"]
                for dep in deps:
                    assert dep in entry["gold_task_kinds"]

    def test_from_gold_dataset_populates_entries(self):
        bench = DecompositionBenchmark.from_gold_dataset()
        assert len(bench.queries) == len(GOLD_DECOMPOSITION)
        assert bench.queries[0].gold_task_kinds == ["provision", "penalty"]

    def test_record_prediction_unknown_query_returns_none(self):
        bench = DecompositionBenchmark.from_gold_dataset()
        assert bench.record_prediction("not a gold query", None) is None

    def test_perfect_prediction_scores_full(self):
        bench = DecompositionBenchmark.from_gold_dataset()
        # Hand-built decomposition matching entry 5 ("What is Section 12?")
        decomp = type("D", (), {})()
        decomp.tasks = [
            EvidenceTask(
                task_id="T1",
                objective="locate provision",
                question="What is Section 12?",
                evidence_requirement=EvidenceRequirement.PROVISION,
                dependency=[],
            )
        ]
        entry = bench.record_prediction(GOLD_DECOMPOSITION[4]["query"], decomp)
        assert entry is not None
        report = bench.evaluate()
        # The direct_lookup class has a single entry scoring perfectly.
        assert report["per_query_class"]["direct_lookup"]["avg_recall"] == 1.0
        assert report["per_query_class"]["direct_lookup"]["avg_f1"] == 1.0

    def test_duplicate_kinds_score_as_multiset(self):
        bench = DecompositionBenchmark()
        bench.add_gold_entry(
            {
                "query": "q",
                "gold_task_kinds": ["condition", "condition"],
                "gold_dependencies": {"condition": []},
            }
        )
        decomp = type("D", (), {})()
        decomp.tasks = [
            EvidenceTask(
                task_id="T1",
                objective="o1",
                question="q1",
                evidence_requirement=EvidenceRequirement.CONDITION,
                dependency=[],
            )
        ]
        bench.record_prediction("q", decomp)
        report = bench.evaluate()
        # One of two gold conditions matched: recall 0.5, precision 1.0,
        # and the entry is under-decomposed.
        assert report["task_recall"] == 0.5
        assert report["task_precision"] == 1.0
        assert report["under_decomposition_rate"] == 1.0

    def test_dependency_accuracy_jaccard(self):
        bench = DecompositionBenchmark()
        bench.add_gold_entry(
            {
                "query": "q",
                "gold_task_kinds": ["provision", "penalty"],
                "gold_dependencies": {"provision": [], "penalty": ["provision"]},
            }
        )
        decomp = type("D", (), {})()
        decomp.tasks = [
            EvidenceTask(
                task_id="T1",
                objective="o",
                question="q1",
                evidence_requirement=EvidenceRequirement.PROVISION,
                dependency=[],
            ),
            EvidenceTask(
                task_id="T2",
                objective="o",
                question="q2",
                evidence_requirement=EvidenceRequirement.PENALTY,
                dependency=["T1"],
            ),
        ]
        bench.record_prediction("q", decomp)
        report = bench.evaluate()
        assert report["dependency_accuracy"] == 1.0
        assert report["exact_match_rate"] == 1.0

    def test_end_to_end_over_real_planner(self):
        """Score the actual deterministic planner against the gold set.

        This is the measurement the benchmark exists for — and, since the
        plural-keyword and comparative fixes, it doubles as a **regression
        gate**: every gold class must decompose exactly (recall/F1 1.0,
        dependency edges exact, no over/under-decomposition).

        Previously-measured gaps (now fixed in the planner):
        - multi_requirement queries dropped the penalty task
          ("penalties" never matched the substring keyword check);
        - comparative queries collapsed to a single provision task.
        """
        from app.rag.planning.query_planner import QueryPlanner

        planner = QueryPlanner()
        bench = DecompositionBenchmark.from_gold_dataset()
        for entry in GOLD_DECOMPOSITION:
            decomp = planner.plan(entry["query"])
            bench.record_prediction(entry["query"], decomp)

        report = bench.evaluate()
        assert report["total_queries"] == len(GOLD_DECOMPOSITION)
        assert report["task_recall"] == 1.0
        assert report["task_precision"] == 1.0
        assert report["decomposition_f1"] == 1.0
        assert report["dependency_accuracy"] == 1.0
        assert report["exact_match_rate"] == 1.0
        assert report["under_decomposition_rate"] == 0.0
        assert report["over_decomposition_rate"] == 0.0
        for cls in ("direct_lookup", "multi_requirement", "comparative"):
            assert report["per_query_class"][cls]["avg_recall"] == 1.0, cls
            assert report["per_query_class"][cls]["avg_f1"] == 1.0, cls

    def test_legacy_subquestion_api_still_evaluates(self):
        bench = DecompositionBenchmark()
        bench.add("q1", gold_subquestions=["s1"], query_class="general")
        bench.queries[0].decomposed_subquestions = ["s1"]
        bench.queries[0].answered_subquestions = 1
        report = bench.evaluate()
        assert report["legacy"]["decomposition_accuracy"] == 1.0
        assert report["legacy"]["subquestion_coverage"] == 1.0


# --------------------------------------------------------------------------- #
# CoverageMetrics — evidence-aware task matching
# --------------------------------------------------------------------------- #
def _task(question: str, entities: list[str] | None = None) -> EvidenceTask:
    return EvidenceTask(
        task_id="T1",
        objective="objective text",
        question=question,
        evidence_requirement=EvidenceRequirement.PENALTY,
        entities=entities or [],
    )


class TestCoverageMetricsEvidenceAware:
    def test_task_matched_by_question_text(self):
        cov = CoverageMetrics()
        cov.update(
            evidence_tasks=[_task("What penalty applies to late filing?")],
            chunks=[{"chunk_id": "c1", "text": "The penalty for late filing of annual return is ₹500 per day."}],
        )
        assert cov.task_coverage == 1.0
        assert cov.missing == []

    def test_task_not_matched_by_unrelated_chunk(self):
        cov = CoverageMetrics()
        cov.update(
            evidence_tasks=[_task("What penalty applies to late filing?")],
            chunks=[{"chunk_id": "c1", "text": "Medieval poetry and knights in shining armour."}],
        )
        assert cov.task_coverage == 0.0
        assert cov.missing == ["T1"]

    def test_chunk_without_entity_metadata_still_matches(self):
        """Regression: the old entity-overlap heuristic could never match a
        chunk with no ``entities`` metadata; text matching does not care."""
        cov = CoverageMetrics()
        cov.update(
            evidence_tasks=[_task("What penalty applies to late filing?", entities=["annual return"])],
            chunks=[{"chunk_id": "c1", "text": "Late filing attracts a monetary penalty under the Act."}],
        )
        assert cov.task_coverage == 1.0

    def test_requirement_coverage_unchanged(self):
        cov = CoverageMetrics()
        cov.update(
            evidence_tasks=[],
            retrieval_plan={"T1": ["lexical"]},
            chunks=[],
        )
        assert cov.requirement_coverage == 1.0


# --------------------------------------------------------------------------- #
# RAGAS-style reference metrics (smoke — full contract in test_eval_framework)
# --------------------------------------------------------------------------- #
class TestRagasMetricsSmoke:
    def test_faithfulness_supported_and_unsupported(self):
        chunks = [{"chunk_id": "c1", "text": "Section 55 requires a food business license."}]
        good = FaithfulnessMetric().compute("Section 55 requires a license.", chunks)
        bad = FaithfulnessMetric().compute("Section 999 is about purple elephants.", chunks)
        assert good.score > bad.score
        assert good.score > 0.0
        assert bad.score < 0.5

    def test_answer_relevance_expected_answer_mode(self):
        score = AnswerRelevanceMetric().compute(
            "Section 55 governs licensing.", "irrelevant query", "Section 55 deals with licensing."
        )
        assert score.score > 0.3
        assert score.detail["mode"] == "expected"

    def test_context_recall_missing_reported(self):
        score = ContextRecallMetric().compute(["c0", "ghost"], [{"chunk_id": "c0", "text": "t"}])
        assert score.score == 0.5
        assert score.detail["missing"] == ["ghost"]

    def test_groundedness_no_chunks_zero(self):
        assert GroundednessMetric().compute("answer", []).score == 0.0
