"""Tests for the LangGraph agent graph (M3 + M4, plan §6).

The graph is exercised end-to-end with ``run_retrieval_pipeline`` and
``run_generation_pipeline`` monkeypatched (no Qdrant / network / torch),
and the stub-LLM fixture keeps ``expand_query_node`` offline.
"""

from __future__ import annotations

import pytest

from app.rag.agent.graph import (
    GROUNDEDNESS_THRESHOLD,
    _route_after_plan,
    build_graph,
    route_after_verify,
    run_agent,
)
from app.rag.agent.nodes import GROUNDEDNESS_THRESHOLD as NODE_THRESHOLD
from app.rag.agent.state import initial_state

pytestmark = pytest.mark.usefixtures("_rag_stub_llm_env")


def _graph():
    return build_graph()


def _patch_pipeline(monkeypatch, groundedness, retrieve_chunks=None):
    """Patch both pipeline entry points on app.rag.tasks."""
    import app.rag.tasks as tasks

    chunks = retrieve_chunks or [{"chunk_id": "c1", "score": 0.9, "text": "Section 50 text"}]

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": chunks,
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "Section 50 prescribes the penalty.",
            "groundedness_score": groundedness,
            "hallucination_detected": groundedness < 0.5,
            "query_type": "offence",
        },
    )


# ---------------------------------------------------------------------- #
# Graph structure
# ---------------------------------------------------------------------- #


def test_graph_compiles():
    graph = _graph()
    assert graph is not None


def test_graph_has_expected_nodes():
    nodes = set(_graph().get_graph().nodes.keys())
    assert {
        "__start__",
        "classify",
        "plan",
        "retrieve",
        "multi_hop_retrieve",
        "generate",
        "verify",
        "citation_quality",
        "targeted_retry",
        "expand_query",
        "plan_tasks",
        "budget_gate",
        "execute_task",
        "evidence_sufficiency",
        "synthesize",
        "abstain",
        "finalize",
        "__end__",
    } <= nodes


# ---------------------------------------------------------------------- #
# Post-plan router (Phase 0: exactly one path per query)
# ---------------------------------------------------------------------- #


def test_route_after_plan_dag_for_multi_part():
    assert _route_after_plan({"query_plan": {"complexity": "multi_part"}}) == "plan_tasks"
    assert _route_after_plan({"query_plan": {"complexity": "multi_hop"}}) == "plan_tasks"


def test_route_after_plan_linear_for_simple():
    state = {"query_plan": {"complexity": "simple"}, "query_type": "general"}
    assert _route_after_plan(state) == "retrieve"


def test_route_after_plan_multi_hop_for_cross_reference():
    state = {"query_plan": {"complexity": "simple"}, "query_type": "cross_reference"}
    assert _route_after_plan(state) == "multi_hop_retrieve"


def test_graph_has_evidence_node_when_flag_on(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "true")
    nodes = set(_graph().get_graph().nodes.keys())
    assert "evidence" in nodes


def test_graph_without_evidence_node_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_EVIDENCE_SELECTOR", "false")
    nodes = set(_graph().get_graph().nodes.keys())
    assert "evidence" not in nodes


# ---------------------------------------------------------------------- #
# Conditional edge
# ---------------------------------------------------------------------- #


def test_route_after_verify_retries_when_low_groundedness():
    state = initial_state("q")
    state.update({"groundedness": 0.3, "retry_count": 0})
    assert route_after_verify(state) == "expand_query"


def test_route_after_verify_finalizes_when_grounded():
    state = initial_state("q")
    state.update({"groundedness": 0.95, "retry_count": 0})
    assert route_after_verify(state) == "finalize"


def test_route_after_verify_stops_at_max_retries():
    state = initial_state("q")
    state.update({"groundedness": 0.3, "retry_count": 2})
    assert route_after_verify(state) == "finalize"


def test_route_after_verify_threshold_boundary():
    state = initial_state("q")
    # Simplified: single GROUNDEDNESS_THRESHOLD = 0.7. At threshold → finalize.
    # Below threshold → retry.
    state.update({"groundedness": 0.70, "retry_count": 0})
    assert route_after_verify(state) == "finalize"
    state.update({"groundedness": 0.699, "retry_count": 0})
    assert route_after_verify(state) == "expand_query"


# ---------------------------------------------------------------------- #
# End-to-end flow
# ---------------------------------------------------------------------- #


def test_agent_flow_grounded_query(monkeypatch):
    _patch_pipeline(monkeypatch, groundedness=0.9)
    result = run_agent(initial_state("penalty for selling substandard food"))
    assert result["answer"] == "Section 50 prescribes the penalty."
    assert result["pipeline"] == "agent"
    assert result["agent"]["retry_count"] == 0
    assert result["agent"]["expanded_query"] is None
    # One full pass on the linear path (Phase 0: plan routes SIMPLE queries
    # to retrieve — no DAG nodes, exactly one generation call).
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert nodes_run == ["classify", "plan", "retrieve", "generate", "verify", "citation_quality"]
    assert "synthesize" not in nodes_run
    assert "plan_tasks" not in nodes_run


def test_agent_flow_retries_then_succeeds(monkeypatch):
    """Low groundedness on the first pass, high on the retry."""
    import app.rag.tasks as tasks

    calls = {"n": 0}

    def fake_gen(query, **kw):
        calls["n"] += 1
        g = 0.4 if calls["n"] == 1 else 0.85
        return {
            "answer": f"answer {calls['n']}",
            "groundedness_score": g,
            "hallucination_detected": g < 0.5,
            "query_type": "offence",
        }

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [{"chunk_id": "c1", "score": 0.9, "text": "Sec 50"}],
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(tasks, "run_generation_pipeline", fake_gen)

    result = run_agent(initial_state("penalty for selling substandard food"))
    assert result["agent"]["retry_count"] == 1
    assert result["agent"]["expanded_query"]  # stub LLM rewrote the query
    assert result["answer"] == "answer 2"
    assert result["agent"]["groundedness"] == 0.85
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    # First pass + expand_query + second retrieve + second generate.
    assert nodes_run.count("generate") == 2
    assert nodes_run.count("expand_query") == 1
    assert nodes_run.index("expand_query") > nodes_run.index("generate")


def test_agent_flow_exhausts_retries(monkeypatch):
    """Persistently low groundedness → stops after max_retries."""
    import app.rag.tasks as tasks

    calls = {"n": 0}

    def fake_gen(query, **kw):
        calls["n"] += 1
        return {
            "answer": f"weak answer {calls['n']}",
            "groundedness_score": 0.2,
            "hallucination_detected": True,
            "query_type": "offence",
        }

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {
            "chunks": [{"chunk_id": "c1", "score": 0.9, "text": "Sec 50"}],
            "query_type": "offence",
            "retrieval_latency_ms": 10,
            "log_id": "log-1",
        },
    )
    monkeypatch.setattr(tasks, "run_generation_pipeline", fake_gen)

    result = run_agent(initial_state("penalty for selling substandard food"))
    # Default max_retries=2 → up to 3 generation passes (initial + 2 retries).
    assert calls["n"] == 3
    assert result["agent"]["retry_count"] == 2
    assert result["agent"]["groundedness"] == 0.2
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert nodes_run.count("expand_query") == 2
    assert nodes_run.count("generate") == 3


def test_threshold_constant_shared():
    assert GROUNDEDNESS_THRESHOLD == NODE_THRESHOLD == 0.7


# ---------------------------------------------------------------------- #
# DAG path (Phase 0: plan → plan_tasks → budget_gate → execute_task →
# evidence_sufficiency → synthesize → verify)
# ---------------------------------------------------------------------- #


def _patch_task_pipeline(monkeypatch, per_task_chunks=2, groundedness=0.9):
    """Patch retrieval to return per-task chunks; generation to be grounded."""
    import app.rag.tasks as tasks

    def fake_retrieve(query, **kw):
        evidence_tasks = kw.get("evidence_tasks") or []
        task_id = evidence_tasks[0].task_id if evidence_tasks else "T0"
        return {
            "chunks": [
                {"chunk_id": f"{task_id}-c{i}", "score": 0.9, "text": f"evidence for {task_id}"}
                for i in range(per_task_chunks)
            ],
            "query_type": "offence",
            "retrieval_latency_ms": 5,
            "log_id": "log-1",
        }

    monkeypatch.setattr(tasks, "run_retrieval_pipeline", fake_retrieve)
    monkeypatch.setattr(
        tasks,
        "run_generation_pipeline",
        lambda query, **kw: {
            "answer": "synthesized answer",
            "groundedness_score": groundedness,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )


def test_agent_dag_flow_multi_part_query(monkeypatch):
    """A MULTI_PART query runs the DAG path: per-task evidence → one synthesis."""
    _patch_task_pipeline(monkeypatch, per_task_chunks=2)

    # Two evidence types (penalty + definition) → the planner yields a
    # MULTI_PART decomposition and the plan router takes the DAG path.
    query = "penalty for selling substandard food and define misbranded food"
    result = run_agent(initial_state(query))

    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert "plan_tasks" in nodes_run
    assert "budget_gate" in nodes_run
    assert "execute_task" in nodes_run
    assert "evidence_sufficiency" in nodes_run
    assert "synthesize" in nodes_run
    # Exactly one generation call — the linear `generate` never runs here.
    assert nodes_run.count("generate") == 0
    assert nodes_run.count("synthesize") == 1
    exec_entry = next(e for e in result["agent"]["audit_trail"] if e["node"] == "execute_task")
    assert exec_entry["detail"]["tasks_completed"] == 2
    assert result["answer"] == "synthesized answer"


def test_agent_dag_path_abstains_without_evidence(monkeypatch):
    """No evidence on any task + exhausted retry budget → explicit abstention."""
    import app.rag.tasks as tasks

    monkeypatch.setattr(
        tasks,
        "run_retrieval_pipeline",
        lambda query, **kw: {"chunks": [], "query_type": "offence", "retrieval_latency_ms": 0, "log_id": None},
    )

    query = "penalty for selling substandard food and define misbranded food"
    result = run_agent(initial_state(query, max_retries=0))

    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert "abstain" in nodes_run
    assert "synthesize" not in nodes_run
    # finalize_node surfaces the abstention answer (Phase 0 fix).
    assert result["abstained"] is True
    assert result["answer"].startswith("INSUFFICIENT EVIDENCE")
    assert result["pipeline"] == "agent"
