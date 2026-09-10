"""Tests for the LangGraph agent nodes (M3, plan §6).

All pipeline calls are monkeypatched — no Qdrant, no network, no torch.
The stub-LLM env is pinned by the autouse ``_rag_stub_llm_env`` fixture in
``tests/conftest.py`` so ``expand_query_node`` (which reuses
``GroundedLLMClient``) stays offline and deterministic.
"""

from __future__ import annotations

from unittest import mock

from app.rag.agent.nodes import (
    GROUNDEDNESS_THRESHOLD,
    _query_for_retrieval,
    budget_gate_node,
    citation_quality_node,
    classify_node,
    evidence_node,
    evidence_sufficiency_node,
    execute_task_node,
    expand_query_node,
    finalize_node,
    generate_node,
    plan_node,
    plan_tasks_node,
    retrieve_node,
    targeted_retry_node,
    verify_node,
)
from app.rag.agent.state import initial_state


def _make_state(**overrides):
    state = initial_state("penalty for selling substandard food")
    state.update(overrides)
    return state  # type: ignore[return-value]


# ---------------------------------------------------------------------- #
# classify_node
# ---------------------------------------------------------------------- #


def test_classify_node_sets_query_type(monkeypatch):
    from app.rag.retrieval import QueryClassifier

    class FakeClassifier:
        def classify(self, query):
            return type("QT", (), {"value": "offence"})()

    monkeypatch.setattr(QueryClassifier, "classify", FakeClassifier().classify)
    out = classify_node(_make_state())
    assert out["query_type"] == "offence"
    assert out["audit_trail"][-1]["node"] == "classify"


def test_classify_node_falls_back_to_general(monkeypatch):
    from app.rag.retrieval import QueryClassifier

    def boom(self, query):
        raise RuntimeError("no classifier")

    monkeypatch.setattr(QueryClassifier, "classify", boom)
    out = classify_node(_make_state())
    assert out["query_type"] == "general"
    assert out["audit_trail"][-1]["detail"]["fallback"] is True


# ---------------------------------------------------------------------- #
# retrieve_node
# ---------------------------------------------------------------------- #


def test_retrieve_node_calls_pipeline_and_records(monkeypatch):
    import app.rag.tasks as tasks

    fake_chunks = [{"chunk_id": "c1", "score": 0.9, "text": "Sec 50"}]
    captured = {}

    def fake_run(query, **kwargs):
        captured["query"] = query
        return {
            "chunks": fake_chunks,
            "query_type": "offence",
            "retrieval_latency_ms": 42,
            "log_id": "log-1",
            "evidence_set": {"items": [{"evidence_type": "statute"}]},
        }

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    out = retrieve_node(_make_state())
    assert out["chunks"] == fake_chunks
    assert out["query_type"] == "offence"
    assert out["retrieval_latency_ms"] == 42
    assert out["log_id"] == "log-1"
    assert out["evidence_set"] == {"items": [{"evidence_type": "statute"}]}
    assert captured["query"] == "penalty for selling substandard food"
    assert out["audit_trail"][-1]["detail"]["chunk_count"] == 1


def test_retrieve_node_uses_expanded_query(monkeypatch):
    import app.rag.tasks as tasks

    captured = {}

    def fake_run(query, **kwargs):
        captured["query"] = query
        return {"chunks": [], "query_type": "offence", "retrieval_latency_ms": 0, "log_id": None}

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    retrieve_node(_make_state(expanded_query="penalty under section 50 FSS Act"))
    assert captured["query"] == "penalty under section 50 FSS Act"


def test_retrieve_node_keeps_query_type_when_empty(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {"chunks": [], "query_type": "", "retrieval_latency_ms": 0, "log_id": None},
    )
    out = retrieve_node(_make_state(query_type="prohibition"))
    assert out["query_type"] == "prohibition"


# ---------------------------------------------------------------------- #
# evidence_node
# ---------------------------------------------------------------------- #


def test_evidence_node_passes_through_none_when_absent():
    """When no evidence_set is in state, None passes through."""
    out = evidence_node(_make_state(chunks=[{"chunk_id": "c1"}]))
    assert out["evidence_set"] is None
    assert out["audit_trail"][-1]["detail"]["evidence_set"] is False


def test_evidence_node_passes_through_evidence_set():
    """evidence_node forwards the evidence_set set by retrieve_node."""
    out = evidence_node(
        _make_state(
            chunks=[{"chunk_id": "c1"}],
            evidence_set={"items": [{"evidence_type": "statute"}]},
        )
    )
    assert out["evidence_set"] == {"items": [{"evidence_type": "statute"}]}
    assert out["audit_trail"][-1]["detail"]["evidence_set"] is True


def test_evidence_node_does_not_recompute_select_evidence_set(monkeypatch):
    """Consolidation check: evidence_node must NOT call select_evidence_set.

    The evidence selector already ran inside run_retrieval_pipeline's
    apply_stages — the node is a pure pass-through now.
    """
    import app.rag.retrieval.evidence_selector as es_mod

    monkeypatch.setattr(es_mod, "select_evidence_set", mock.Mock(side_effect=AssertionError("should not be called")))
    out = evidence_node(_make_state(evidence_set={"items": []}))
    assert out["evidence_set"] == {"items": []}
    assert not es_mod.select_evidence_set.called


# ---------------------------------------------------------------------- #
# generate_node
# ---------------------------------------------------------------------- #


def test_generate_node_calls_pipeline(monkeypatch):
    import app.rag.tasks as tasks

    fake_response = {
        "answer": "Section 50 prescribes the penalty.",
        "groundedness_score": 0.82,
        "hallucination_detected": False,
        "query_type": "offence",
    }
    captured = {}

    def fake_run(query, **kwargs):
        captured["query"] = query
        captured["chunks"] = kwargs.get("chunks")
        return fake_response

    monkeypatch.setattr(tasks, "run_generation_pipeline", fake_run)
    chunks = [{"chunk_id": "c1"}]
    out = generate_node(_make_state(chunks=chunks, query_type="offence"))
    assert out["answer"] == "Section 50 prescribes the penalty."
    assert out["groundedness"] == 0.82
    assert out["hallucination_detected"] is False
    assert out["response"] == fake_response
    assert captured["chunks"] == chunks
    assert out["audit_trail"][-1]["node"] == "generate"


def test_generate_node_defaults_on_empty(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {},
    )
    out = generate_node(_make_state())
    assert out["answer"] == ""
    assert out["groundedness"] == 0.0
    assert out["hallucination_detected"] is False


# ---------------------------------------------------------------------- #
# verify_node
# ---------------------------------------------------------------------- #


def test_verify_node_passes_through():
    out = verify_node(_make_state(groundedness=0.95, hallucination_detected=False))
    assert out["groundedness"] == 0.95
    assert out["hallucination_detected"] is False


# ---------------------------------------------------------------------- #
# expand_query_node
# ---------------------------------------------------------------------- #


def test_expand_query_node_increments_retry_count():
    out = expand_query_node(_make_state(retry_count=1))
    assert out["retry_count"] == 2
    assert out["audit_trail"][-1]["node"] == "expand_query"
    assert out["audit_trail"][-1]["detail"]["retry"] == 2


def test_expand_query_node_stub_llm_returns_something():
    """Stub LLM returns a canned text — must not raise, query survives."""
    out = expand_query_node(_make_state())
    assert out["retry_count"] == 1
    # Stub returns the canned "Based on the provided context..." text.
    assert isinstance(out["expanded_query"], str) and out["expanded_query"]


def test_expand_query_node_keeps_query_on_llm_failure(monkeypatch):
    import app.rag.generation.llm_client as llm_mod

    class FailingClient:
        def call(self, *args, **kwargs):
            return type("R", (), {"success": False, "text": ""})()

    monkeypatch.setattr(llm_mod, "GroundedLLMClient", lambda *a, **k: FailingClient())
    out = expand_query_node(_make_state())
    assert out["expanded_query"] == "penalty for selling substandard food"
    assert out["retry_count"] == 1


# ---------------------------------------------------------------------- #
# finalize_node
# ---------------------------------------------------------------------- #


def test_finalize_node_merges_agent_metadata():
    state = _make_state(
        query_type="offence",
        chunks=[{"chunk_id": "c1"}],
        groundedness=0.9,
        hallucination_detected=False,
        retry_count=1,
        expanded_query="expanded q",
        response={"answer": "ans", "query_type": "offence"},
    )
    state["audit_trail"].append({"node": "classify", "latency_ms": 1, "detail": {}})
    out = finalize_node(state)
    resp = out["response"]
    assert resp["pipeline"] == "agent"
    assert resp["agent"]["retry_count"] == 1
    assert resp["agent"]["expanded_query"] == "expanded q"
    assert resp["agent"]["groundedness"] == 0.9
    assert len(resp["agent"]["audit_trail"]) == 1
    assert resp["retrieved_chunks"] == [{"chunk_id": "c1"}]


def test_finalize_node_fills_defaults_from_state():
    out = finalize_node(_make_state(query="q?", query_type="general"))
    resp = out["response"]
    assert resp["query"] == "q?"
    assert resp["query_type"] == "general"
    assert resp["retrieved_chunks"] == []
    assert resp["agent"]["retry_count"] == 0


# ---------------------------------------------------------------------- #
# Threshold sanity
# ---------------------------------------------------------------------- #


def test_groundedness_threshold_is_0_7():
    assert GROUNDEDNESS_THRESHOLD == 0.7


# ---------------------------------------------------------------------- #
# citation_quality_node
# ---------------------------------------------------------------------- #


def test_citation_quality_all_citations_retrieved():
    """All cited chunks are in the retrieved set → quality OK."""
    state = _make_state(
        chunks=[{"chunk_id": "c1"}, {"chunk_id": "c2"}, {"chunk_id": "c3"}],
        response={
            "citations": [
                {"chunk_id": "c1", "section": "50", "confidence": 0.9},
                {"chunk_id": "c2", "section": "55", "confidence": 0.8},
            ],
        },
    )
    out = citation_quality_node(state)
    assert out["citation_quality_ok"] is True
    assert out["missing_citations"] == []
    # audit trail records the node
    assert any(e["node"] == "citation_quality" for e in out["audit_trail"])


def test_citation_quality_missing_citations():
    """Answer cites a chunk that was never retrieved → quality FAILS."""
    state = _make_state(
        chunks=[{"chunk_id": "c1"}, {"chunk_id": "c2"}],
        response={
            "citations": [
                {"chunk_id": "c1", "section": "50", "confidence": 0.9},
                {"chunk_id": "c99", "section": "99", "confidence": 0.9},
            ],
        },
    )
    out = citation_quality_node(state)
    assert out["citation_quality_ok"] is False
    assert out["missing_citations"] == ["c99"]


def test_citation_quality_no_citations():
    """No citations in the response → quality OK (nothing to check)."""
    state = _make_state(
        chunks=[{"chunk_id": "c1"}],
        response={"answer": "No citations mentioned"},
    )
    out = citation_quality_node(state)
    assert out["citation_quality_ok"] is True
    assert out["missing_citations"] == []


def test_citation_quality_empty_chunks():
    """No retrieved chunks but answer cites something → all citations missing."""
    state = _make_state(
        chunks=[],
        response={
            "citations": [{"chunk_id": "c1", "section": "50"}],
        },
    )
    out = citation_quality_node(state)
    assert out["citation_quality_ok"] is False
    assert out["missing_citations"] == ["c1"]


def test_citation_quality_accepts_dag_evidence_chunks():
    """Citations synthesized from per-task evidence are not flagged missing."""
    state = _make_state(
        chunks=[],  # DAG path: linear chunks empty
        evidence={"T1": [{"chunk_id": "T1-c0"}], "T2": [{"chunk_id": "T2-c0"}]},
        response={"citations": [{"chunk_id": "T1-c0"}, {"chunk_id": "T2-c0"}]},
    )
    out = citation_quality_node(state)
    assert out["citation_quality_ok"] is True
    assert out["missing_citations"] == []


# ---------------------------------------------------------------------- #
# plan_node (Phase 0: serialized query plan)
# ---------------------------------------------------------------------- #


def test_plan_node_builds_serialized_plan():
    import json

    out = plan_node(_make_state(query="penalty for selling substandard food"))
    plan = out["query_plan"]
    assert plan["complexity"] in ("simple", "multi_part", "multi_hop")
    assert plan["total_tasks"] == len(plan["tasks"])
    assert plan["tasks"], "planner should produce at least one task"
    assert all(isinstance(t, dict) and t.get("task_id") for t in plan["tasks"])
    # Serialized plan must be JSON-safe (checkpointing readiness).
    json.dumps(plan)
    assert out["subquestions"] == [t["task_id"] for t in plan["tasks"]]
    assert out["audit_trail"][-1]["node"] == "plan"


# ---------------------------------------------------------------------- #
# plan_tasks_node / execute_task_node / budget_gate_node (DAG path)
# ---------------------------------------------------------------------- #


def _task_dict(task_id, dep=()):
    return {
        "task_id": task_id,
        "objective": "determine_penalty",
        "question": f"question for {task_id}",
        "evidence_requirement": "penalty",
        "dependency": list(dep),
    }


def test_plan_tasks_node_builds_order_from_query_plan():
    state = _make_state(
        query_plan={"complexity": "multi_part", "tasks": [_task_dict("T1"), _task_dict("T2", dep=("T1",))]},
    )
    out = plan_tasks_node(state)
    assert out["tasks"].keys() == {"T1", "T2"}
    assert out["task_order"] == ["T1", "T2"]
    assert out["dag_valid"] is True


def test_plan_tasks_node_falls_back_to_external_evidence_tasks():
    from app.rag.evidence_task import EvidenceRequirement, EvidenceTask

    task = EvidenceTask(
        task_id="T1",
        objective="determine_penalty",
        question="penalty?",
        evidence_requirement=EvidenceRequirement.PENALTY,
    )
    out = plan_tasks_node(_make_state(evidence_tasks=[task]))
    assert out["task_order"] == ["T1"]
    assert out["tasks"]["T1"]["objective"] == "determine_penalty"


def test_plan_tasks_node_skips_unparsable_tasks():
    state = _make_state(query_plan={"complexity": "multi_part", "tasks": [{"objective": "no id"}]})
    out = plan_tasks_node(state)
    assert out["tasks"] == {}
    assert out["task_order"] == []
    assert out["dag_valid"] is True


def test_execute_task_node_runs_ready_tasks(monkeypatch):
    import app.rag.tasks as tasks

    def fake_run(query, **kw):
        return {"chunks": [{"chunk_id": f"c-{query}"}], "query_type": "offence"}

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    state = _make_state(
        tasks={"T1": _task_dict("T1"), "T2": _task_dict("T2")},
        task_order=["T1", "T2"],
    )
    out = execute_task_node(state)
    assert sorted(out["evidence"].keys()) == ["T1", "T2"]
    assert out["tasks_completed"] == 2
    assert out["budget"]["consumed_tasks"] == 2
    assert out["budget"]["consumed_retrieval_rounds"] == 1
    assert out["budget"]["consumed_documents"] == 2


# ---------------------------------------------------------------------- #
# Wave-based parallel DAG execution (Phase 1)
# ---------------------------------------------------------------------- #


def test_execute_task_node_runs_waves_in_dependency_order(monkeypatch):
    """T2 depends on T1 → two waves, and T2's worker sees T1's evidence
    (via cross-reference mining, the only worker-visible dependency input)."""
    import app.rag.tasks as tasks

    calls: list[tuple[str | None, str]] = []

    def fake_run(query, **kw):
        evidence_tasks = kw.get("evidence_tasks") or []
        tid = evidence_tasks[0].task_id if evidence_tasks else None
        calls.append((tid, query))
        if tid == "T1":
            return {"chunks": [{"chunk_id": "c1", "text": "as prescribed under section 18 of the Act"}]}
        if query.endswith("section 18"):
            return {"chunks": [{"chunk_id": "c3", "text": "section 18 content"}]}
        return {"chunks": [{"chunk_id": "c2", "text": "penalty applies"}]}

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    state = _make_state(
        tasks={"T1": _task_dict("T1"), "T2": _task_dict("T2", dep=("T1",))},
        task_order=["T1", "T2"],
    )
    out = execute_task_node(state)
    assert out["tasks_completed"] == 2
    waves = out["audit_trail"][-1]["detail"]["waves"]
    assert [w["ready"] for w in waves] == [["T1"], ["T2"]]
    # T2's first query is its question; a later one is the cross-reference
    # expansion mined from T1's wave-1 evidence.
    t2_queries = [q for tid, q in calls if tid == "T2"]
    assert t2_queries[0] == "question for T2"
    assert any(q.endswith("section 18") for q in t2_queries[1:])
    assert out["task_results"]["T2"]["status"] == "completed"
    assert out["task_results"]["T2"]["cross_references"]


def test_execute_task_node_respects_parallelism_flag(monkeypatch):
    """``cfg.task_parallelism`` disabled → sequential fallback, same results."""
    import app.rag.tasks as tasks
    from app.shared.config import cfg

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", lambda query, **kw: {"chunks": [{"chunk_id": "c"}]})
    monkeypatch.setattr(cfg, "task_parallelism", False, raising=False)
    state = _make_state(
        tasks={"T1": _task_dict("T1"), "T2": _task_dict("T2")},
        task_order=["T1", "T2"],
    )
    out = execute_task_node(state)
    assert out["tasks_completed"] == 2
    assert out["audit_trail"][-1]["detail"]["parallel"] is False


def test_execute_task_node_defers_beyond_task_budget(monkeypatch):
    """Tasks beyond ``max_tasks`` are deferred for the retry round, not failed."""
    import app.rag.tasks as tasks

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", lambda query, **kw: {"chunks": [{"chunk_id": "c"}]})
    state = _make_state(
        tasks={"T1": _task_dict("T1"), "T2": _task_dict("T2"), "T3": _task_dict("T3")},
        task_order=["T1", "T2", "T3"],
        budget={"max_tasks": 2},
    )
    out = execute_task_node(state)
    assert out["tasks_completed"] == 2
    assert out["budget"]["consumed_tasks"] == 2
    assert sorted(out["audit_trail"][-1]["detail"]["deferred"]) == ["T3"]
    # Deferred ≠ failed: no task_results entry, ready to run on the retry round.
    assert "T3" not in out["task_results"]


def test_execute_task_node_marks_unreachable_tasks_failed(monkeypatch):
    """A task whose dependency can never complete fails explicitly."""
    state = _make_state(
        tasks={"T2": _task_dict("T2", dep=("T9",))},
        task_order=["T2"],
    )
    out = execute_task_node(state)
    assert out["task_results"]["T2"]["status"] == "failed"
    assert out["task_results"]["T2"]["failure_reason"] == "UNMET_DEPENDENCIES"
    assert out["tasks_completed"] == 0


def test_execute_task_node_does_not_reexecute_completed_tasks(monkeypatch):
    """Retry-round semantics: completed tasks keep their evidence and are
    not re-retrieved; only unfinished tasks run."""
    import app.rag.tasks as tasks

    calls: list[str] = []

    def fake_run(query, **kw):
        calls.append(query)
        return {"chunks": [{"chunk_id": "c2"}], "query_type": "offence"}

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_run)
    state = _make_state(
        tasks={"T1": _task_dict("T1"), "T2": _task_dict("T2")},
        task_order=["T1", "T2"],
        task_results={"T1": {"status": "completed", "confidence": 1.0, "evidence_count": 1}},
        evidence={"T1": [{"chunk_id": "old-t1"}]},
    )
    out = execute_task_node(state)
    assert len(calls) == 1  # only T2 re-ran
    assert out["evidence"]["T1"] == [{"chunk_id": "old-t1"}]  # preserved
    assert out["tasks_completed"] == 2


def test_cross_reference_queries_skip_sections_already_in_question():
    from app.rag.agent.nodes import _cross_reference_queries

    dep = [{"chunk_id": "c1", "text": "subject to section 12 and section 18 of the Act"}]
    queries = _cross_reference_queries("penalty under section 12", dep)
    assert queries == ["penalty under section 12, section 18"]


def test_answer_contract_verification_helpers():
    """Phase 1 §13: the canonical contract carries verification helpers."""
    from app.rag.evidence_task import AnswerContract

    contract = AnswerContract(required_fields=["penalty", "section"], optional_fields=["note"])
    assert contract.is_satisfied({"penalty": "1 lakh", "section": "12"})
    assert not contract.is_satisfied({"penalty": "", "section": "12"})
    assert contract.missing_fields({"penalty": None, "section": "12"}) == ["penalty"]


def test_evidence_contract_module_reexports_canonical_contract():
    """The dedupe: evidence_contract is a re-export shim of evidence_task."""
    from app.rag.evidence_contract import AnswerContract as Reexported
    from app.rag.evidence_task import AnswerContract as Canonical

    assert Reexported is Canonical


def test_execute_task_node_skips_unmet_dependencies(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {"chunks": [{"chunk_id": "c"}], "query_type": "offence"},
    )
    state = _make_state(
        tasks={"T2": _task_dict("T2", dep=("T9",))},
        task_order=["T2"],
    )
    out = execute_task_node(state)
    assert out["evidence"] == {}
    assert out["tasks_completed"] == 0
    # Phase 1: unmet dependencies are an explicit failure, not a silent skip.
    assert out["task_results"]["T2"]["failure_reason"] == "UNMET_DEPENDENCIES"
    assert out["audit_trail"][-1]["detail"]["failed"] == ["T2"]


def test_budget_gate_node_reports_exhaustion():
    exhausted = budget_gate_node(_make_state(budget={"max_tasks": 10, "consumed_tasks": 10}))
    assert exhausted["budget_exhausted"] is True

    fresh = budget_gate_node(_make_state(budget={"max_tasks": 10, "consumed_tasks": 3}))
    assert fresh["budget_exhausted"] is False


# ---------------------------------------------------------------------- #
# evidence_sufficiency_node (Phase 0: no more any() TypeError)
# ---------------------------------------------------------------------- #


def test_evidence_sufficiency_sufficient():
    state = _make_state(
        tasks={"T1": _task_dict("T1"), "T2": _task_dict("T2")},
        evidence={
            "T1": [{"chunk_id": "c1", "score": 0.9, "text": "penalty is Rs. 500 and section 12 applies"}],
            "T2": [{"chunk_id": "c2", "score": 0.9, "text": "section 12 defines the offence"}],
        },
        citation_quality_ok=True,
        hallucination_detected=False,
    )
    out = evidence_sufficiency_node(state)
    assert out["evidence_sufficient"] is True
    assert out["evidence_coverage"] == 1.0
    assert out["abstain_required"] is False
    # Phase 2: rubric verdicts + live signals land on state.
    assert len(out["task_sufficiency"]) == 2
    assert out["has_conflicts"] is False
    assert out["authority_score"] >= 0.5


def test_evidence_sufficiency_insufficient_with_bad_signals():
    """Regression: the old any(not x, y) call raised TypeError on this path."""
    state = _make_state(
        tasks={"T1": _task_dict("T1")},
        evidence={"T1": [{"chunk_id": "c1"}]},
        citation_quality_ok=False,
        hallucination_detected=True,
    )
    out = evidence_sufficiency_node(state)
    assert out["evidence_sufficient"] is False
    assert out["abstain_required"] is False


def test_evidence_sufficiency_abstains_on_empty():
    out = evidence_sufficiency_node(_make_state(tasks={}, evidence={}))
    assert out["abstain_required"] is True
    assert out["evidence_sufficient"] is False


def test_evidence_sufficiency_abstains_on_exhausted_budget():
    state = _make_state(
        tasks={"T1": _task_dict("T1")},
        evidence={},
        retry_count=2,
        max_retries=2,
    )
    out = evidence_sufficiency_node(state)
    assert out["budget_exhausted"] is True
    assert out["abstain_required"] is True


# ---------------------------------------------------------------------- #
# Phase 2: claim verification + rubric-driven signals on state
# ---------------------------------------------------------------------- #


def test_generate_node_persists_claim_verdicts(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "Section 50 prescribes the penalty.",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )
    state = _make_state(
        chunks=[{"chunk_id": "c1", "score": 0.9, "text": "Section 50 text"}],
    )
    out = generate_node(state)
    assert out["claims"], "grounded answer must carry extractable claims"
    assert out["claim_groundedness"] == 1.0
    assert out["unverified_claims"] == []
    assert out["audit_trail"][-1]["detail"]["claims"] >= 1


def test_generate_node_without_claims_is_noop(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "ok",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )
    out = generate_node(_make_state(chunks=[{"chunk_id": "c1", "score": 0.9, "text": "t"}]))
    assert "claims" not in out  # no verifiable claims → no claim keys


def test_evidence_sufficiency_surfaces_conflict_signals():
    """Two chunks asserting different amounts for section 12 → live conflict."""
    state = _make_state(
        tasks={"T1": _task_dict("T1")},
        evidence={
            "T1": [
                {"chunk_id": "c1", "score": 0.9, "text": "fine of Rs. 500", "section_number": "12"},
                {"chunk_id": "c2", "score": 0.9, "text": "fine of Rs. 1000", "section_number": "12"},
            ]
        },
    )
    out = evidence_sufficiency_node(state)
    assert out["has_conflicts"] is True
    assert "EVIDENCE_CONTRADICTION" in out["diagnosis_failures"]
    assert out["evidence_sufficient"] is False
    verdict = out["task_sufficiency"][0]
    assert "contradiction" in verdict["failures"]


def test_targeted_retry_consumes_rubric_failures():
    """Rubric failure codes flow straight into the retry diagnosis."""
    state = _make_state(
        diagnosis_failures=["EVIDENCE_CONTRADICTION", "TEMPORAL_INVALIDITY"],
        has_conflicts=True,
    )
    out = targeted_retry_node(state)
    assert out["targeted_query"]
    assert out["retry_count"] == 1
    detail = out["audit_trail"][-1]["detail"]
    assert "EVIDENCE_CONTRADICTION" in detail["failures"]
    assert "TEMPORAL_INVALIDITY" in detail["failures"]


def test_verify_claims_helper_is_deterministic():
    from app.rag.agent.nodes import _verify_claims

    chunks = [{"chunk_id": "c1", "score": 0.9, "text": "Section 50 text"}]
    ok = _verify_claims("Section 50 prescribes the penalty.", chunks)
    assert ok is not None and ok["claim_groundedness"] == 1.0
    bad = _verify_claims("weak answer 1", chunks)
    assert bad is not None and bad["claim_groundedness"] == 0.0
    assert _verify_claims("ok", chunks) is None


# ---------------------------------------------------------------------- #
# targeted_retry_node / finalize_node (Phase 0 fixes)
# ---------------------------------------------------------------------- #


def test_targeted_retry_node_sets_targeted_query():
    state = _make_state(missing_citations=["c99"], groundedness=0.9)
    out = targeted_retry_node(state)
    assert out["targeted_query"]
    assert out["retry_count"] == 1
    assert out["audit_trail"][-1]["detail"]["failures"]


def test_query_for_retrieval_prefers_targeted_query():
    assert _query_for_retrieval({"targeted_query": "t", "expanded_query": "e", "query": "q"}) == "t"
    assert _query_for_retrieval({"expanded_query": "e", "query": "q"}) == "e"
    assert _query_for_retrieval({"query": "q"}) == "q"


def test_finalize_node_surfaces_abstain_answer():
    """The abstain path has no response dict — finalize must copy state answer."""
    state = _make_state(answer="INSUFFICIENT EVIDENCE: …", abstained=True, response={})
    out = finalize_node(state)
    resp = out["response"]
    assert resp["answer"].startswith("INSUFFICIENT EVIDENCE")
    assert resp["abstained"] is True
    assert resp["pipeline"] == "agent"


# ---------------------------------------------------------------------- #
# Phase 3: budget-aware routing economics wiring
# ---------------------------------------------------------------------- #


def test_plan_node_records_routing_decision_and_tier_budget():
    """DAG-worthy plans route to decomposition and get the moderate tier."""
    import json

    query = "penalty for selling substandard food and define misbranded food"
    out = plan_node(_make_state(query=query))
    decision = out["routing_decision"]
    assert decision["strategy"] == "decomposition"
    assert decision["tier"] == "moderate"
    assert decision["pinned"] is False
    json.dumps(decision)  # checkpoint-safe
    assert out["budget"]["max_llm_calls"] == 8  # moderate tier applied
    assert out["audit_trail"][-1]["detail"]["strategy"] == "decomposition"


def test_plan_node_direct_override_keeps_identifier_lookups_cheap():
    """A single-identifier lookup plans as MULTI_PART but routes DIRECT."""
    out = plan_node(_make_state(query="What is the penalty under Section 12?"))
    decision = out["routing_decision"]
    assert decision["strategy"] == "direct"
    assert decision["tier"] == "direct"
    assert out["budget"]["max_tasks"] == 0  # no DAG work funded
    assert out["budget"]["max_llm_calls"] == 4


def test_plan_node_preserves_smaller_explicit_caps():
    """Tier application is shrink-only: explicit caps are never raised."""
    out = plan_node(_make_state(budget={"max_llm_calls": 2, "consumed_llm_calls": 1}))
    assert out["budget"]["max_llm_calls"] == 2
    assert out["budget"]["consumed_llm_calls"] == 1


def test_retrieve_node_consumes_round_and_document_budget(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [{"chunk_id": f"c{i}"} for i in range(3)],
            "query_type": "offence",
            "retrieval_latency_ms": 5,
            "log_id": "log-1",
        },
    )
    out = retrieve_node(_make_state())
    assert out["budget"]["consumed_retrieval_rounds"] == 1
    assert out["budget"]["consumed_documents"] == 3


def test_generate_node_consumes_llm_budget(monkeypatch):
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "Section 50 prescribes the penalty.",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
        },
    )
    out = generate_node(_make_state())
    assert out["budget"]["consumed_llm_calls"] == 1


def test_expand_query_node_consumes_llm_budget():
    out = expand_query_node(_make_state())
    assert out["budget"]["consumed_llm_calls"] == 1
    assert out["retry_count"] == 1
