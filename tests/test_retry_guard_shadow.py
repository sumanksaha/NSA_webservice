"""SPEC-3 production shadow hook — regression lock.

The linear path regenerates after a retry and the second run overwrites
``state["answer"]``, so the pre-retry arm does not survive to
``finalize_node``. ``capture_arm`` preserves it; ``observe_retry_arms``
compares the two.

The load-bearing property is that this is **observational only**. The adopt
rule keys on ``binary_correct`` / ``answer_correctness``, which are computed
against a gold reference and do not exist on the serving path. Production
therefore records the comparison and never changes the served answer.

No network, no LLM, no Qdrant.
"""
from __future__ import annotations

from app.rag.agent.retry_guard import (
    OBSERVABLE_METRICS,
    capture_arm,
    observe_retry_arms,
    select_arm,
)

_RESULT = {
    "answer": "x" * 120,
    "groundedness_score": 0.8,
    "hallucination_detected": False,
    "citations": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
}
_CLAIMS = {"claim_groundedness": 0.75}


class TestCaptureArm:
    def test_first_generation_is_the_baseline(self):
        update = capture_arm({"retry_count": 0}, _RESULT, _CLAIMS)
        assert update["retry_guard_baseline"]["groundedness_score"] == 0.8
        assert update["retry_guard_retry"] is None

    def test_later_generation_becomes_the_retry_arm(self):
        update = capture_arm({"retry_count": 1}, _RESULT, _CLAIMS)
        assert update["retry_guard_retry"]["groundedness_score"] == 0.8
        assert "retry_guard_baseline" not in update

    def test_capture_is_overwritten_not_appended(self):
        first = capture_arm({"retry_count": 0}, _RESULT, _CLAIMS)
        second = capture_arm({"retry_count": 2}, _RESULT, _CLAIMS)
        merged = {**first, **second}
        assert merged["retry_guard_baseline"]["groundedness_score"] == 0.8
        assert merged["retry_guard_retry"]["groundedness_score"] == 0.8

    def test_capture_survives_missing_optional_fields(self):
        update = capture_arm({"retry_count": 0}, {"answer": "short"}, None)
        assert update["retry_guard_baseline"]["n_citations"] == 0
        assert update["retry_guard_baseline"]["answer_length"] == 5


class TestObserveRetryArms:
    def test_no_pair_is_reported_as_unavailable(self):
        assert observe_retry_arms(None, None)["available"] is False
        assert observe_retry_arms({"groundedness_score": 1.0}, None)["available"] is False

    def test_never_adopts(self):
        obs = observe_retry_arms(
            {"groundedness_score": 0.1, "claim_groundedness": 0.1},
            {"groundedness_score": 0.9, "claim_groundedness": 0.9},
        )
        assert obs["available"] is True
        assert obs["adopted"] is False
        assert "shadow_only" in obs["reason"]

    def test_reports_observable_deltas(self):
        obs = observe_retry_arms(
            {"groundedness_score": 0.4, "claim_groundedness": 0.5, "n_citations": 2, "answer_length": 100},
            {"groundedness_score": 0.6, "claim_groundedness": 0.4, "n_citations": 5, "answer_length": 140},
        )
        assert set(obs["deltas"]) == set(OBSERVABLE_METRICS)
        assert obs["deltas"]["groundedness_score"] == 0.2
        assert obs["deltas"]["claim_groundedness"] == -0.1
        assert obs["deltas"]["n_citations"] == 3

    def test_flags_observably_worse_retry(self):
        obs = observe_retry_arms(
            {"groundedness_score": 0.8, "claim_groundedness": 0.8},
            {"groundedness_score": 0.3, "claim_groundedness": 0.3},
        )
        assert obs["observably_worse"] is True

    def test_breadth_growth_alone_is_not_worse(self):
        obs = observe_retry_arms(
            {"groundedness_score": 0.8, "claim_groundedness": 0.8, "n_citations": 2},
            {"groundedness_score": 0.8, "claim_groundedness": 0.8, "n_citations": 5},
        )
        assert obs["observably_worse"] is False

    def test_hallucination_delta_is_reported(self):
        obs = observe_retry_arms(
            {"hallucination_detected": False},
            {"hallucination_detected": True},
        )
        assert obs["hallucination_delta"] == 1


class TestNodeRoundTrip:
    """End-to-end through the real nodes: capture on retry, observe at finalize.

    The unit tests above pin the helpers; this pins the *wiring*, which is what
    actually makes the comparison possible. A retry regenerates and overwrites
    the answer, so without ``capture_arm`` there is no pair at finalize.
    """

    def _pipeline(self, answers):
        from app.rag import tasks

        calls = {"n": 0}

        def fake_run(query, **kwargs):
            i = min(calls["n"], len(answers) - 1)
            calls["n"] += 1
            return answers[i]

        tasks.run_generation_pipeline = fake_run
        return calls

    def test_finalize_reports_observation_without_changing_the_answer(self, monkeypatch):
        from app.rag import tasks
        from app.rag.agent.nodes.linear import finalize_node, generate_node

        baseline_answer = "Section 31 requires a licence before operating. [1]"
        retry_answer = "Section 31 requires a licence. [1] See also Section 32. [2]"
        self._pipeline(
            [
                {
                    "answer": baseline_answer,
                    "groundedness_score": 0.9,
                    "hallucination_detected": False,
                    "citations": [{"chunk_id": "c1"}],
                },
                {
                    "answer": retry_answer,
                    "groundedness_score": 0.4,
                    "hallucination_detected": True,
                    "citations": [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
                },
            ],
        )
        monkeypatch.setattr(tasks, "run_generation_pipeline", tasks.run_generation_pipeline)

        base_state = {"query": "q?", "query_type": "general", "chunks": [], "retry_count": 0}
        first = generate_node(base_state)
        state = {**base_state, **first}
        # Retry regenerates with retry_count incremented (targeted_retry_node).
        second = generate_node({**state, "retry_count": 1})
        state = {**state, **second}

        # The retry answer is what the pipeline serves.
        assert state["answer"] == retry_answer

        out = finalize_node(state)
        response = out["response"]

        # Observation is present and explicitly non-adopting.
        guard = response.get("retry_guard")
        assert guard is not None, "shadow observation missing from the response"
        assert guard["adopted"] is False
        assert guard["available"] is True
        assert guard["reason"].startswith("shadow_only")
        # Retry got worse here, and the hook says so.
        assert guard["observably_worse"] is True
        assert guard["deltas"]["groundedness_score"] == -0.5
        assert guard["deltas"]["n_citations"] == 1
        assert guard["hallucination_delta"] == 1
        # Decisively: the served answer is still the retry answer — the hook
        # observes, it does not gate.
        assert response["answer"] == retry_answer

    def test_no_observation_block_when_no_retry_ran(self, monkeypatch):
        from app.rag import tasks
        from app.rag.agent.nodes.linear import finalize_node, generate_node

        self._pipeline(
            [
                {
                    "answer": "Only one generation here.",
                    "groundedness_score": 0.9,
                    "hallucination_detected": False,
                    "citations": [{"chunk_id": "c1"}],
                },
            ],
        )
        monkeypatch.setattr(tasks, "run_generation_pipeline", tasks.run_generation_pipeline)
        state = {"query": "q?", "query_type": "general", "chunks": [], "retry_count": 0}
        state = {**state, **generate_node(state)}
        out = finalize_node(state)
        # No retry means no pair; the block must be omitted, not a broken stub.
        assert "retry_guard" not in out["response"]


class TestAdoptRuleNeedsGold:
    """Guard the reason adoption is unavailable on the serving path."""

    def test_observable_metrics_exclude_gold_referenced_scores(self):
        assert "binary_correct" not in OBSERVABLE_METRICS
        assert "answer_correctness" not in OBSERVABLE_METRICS

    def test_select_arm_still_works_for_eval(self):
        baseline = {"binary_correct": 0, "answer_correctness": 0.2, "citation_precision": 0.0, "groundedness_score": 0.8}
        retry = {"binary_correct": 1, "answer_correctness": 0.6, "citation_precision": 0.4, "groundedness_score": 0.9}
        assert select_arm(baseline, retry)["selected_arm"] == "retry"
