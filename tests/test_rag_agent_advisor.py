"""Tests for the deterministic FSO strategic advisory selector (ADR-0003).

Offline, no Qdrant / LLM / torch. Expected Acts are spec literals from
docs/FSO_GAME_THEORY_ADVISORY_BLUEPRINT.md §6, not recomputed from the code.
"""

from __future__ import annotations


def _selector():
    from app.rag.advisor.selector import DeterministicActSelector

    return DeterministicActSelector()


def test_fail_closed_no_anchor():
    out = _selector().select_act(retrieved_sections=[])
    assert out["fso_act"] is None
    assert out["abstain_reason"] == "insufficient_statutory_grounding"


def test_unknown_section_abstains():
    out = _selector().select_act(retrieved_sections=["99"])
    assert out["fso_act"] is None
    assert out["abstain_reason"] == "insufficient_statutory_grounding"


def test_mixed_known_unknown_anchors_on_known():
    out = _selector().select_act(retrieved_sections=["99", "52"])
    assert out["fso_act"] is not None
    assert out["abstain_reason"] is None
    assert "Section 52" in out["fso_act"]["statutory_anchor"]


def test_substandard_without_lab_samples_first():
    out = _selector().select_act(retrieved_sections=["51"], lab_report_available=False)
    assert out["abstain_reason"] is None
    assert out["fso_act"]["action"] == "Sample & Lab-Test"
    assert out["fso_act"]["escalation_level"] == "SAMPLE_LAB_TEST"
    assert "Section 51" in out["fso_act"]["statutory_anchor"]


def test_unlicensed_operation_prosecutes():
    out = _selector().select_act(retrieved_sections=["63"])
    assert out["fso_act"]["action"] == "Prosecution / Licence Action u/s 63/64"
    assert out["fso_act"]["escalation_level"] == "PROSECUTION"
    assert "Section 63" in out["fso_act"]["statutory_anchor"]


def test_subsequent_offence_anchors_on_highest_severity():
    out = _selector().select_act(retrieved_sections=["51", "64"], has_prior_violations=True)
    assert out["fso_act"]["escalation_level"] == "PROSECUTION"
    assert "Section 64" in out["fso_act"]["statutory_anchor"]


def test_prior_notice_gate_emits_improvement_notice():
    # §55 requires prior notice; without a lab report the Act is §32 even
    # though no §32 chunk was retrieved (allowed-§32 decision).
    out = _selector().select_act(retrieved_sections=["55"], lab_report_available=False)
    assert out["fso_act"]["escalation_level"] == "IMPROVEMENT_NOTICE"
    assert "Improvement Notice" in out["fso_act"]["action"]


def test_repeat_offender_escalates_substandard():
    out = _selector().select_act(retrieved_sections=["51"], has_prior_violations=True)
    assert out["fso_act"]["escalation_level"] == "PROSECUTION"


def test_zero_schedule_anchor_floors_at_improvement_notice():
    # §32 carries no penalty schedule — the only grounded section is the
    # notice procedure itself, so the Act is the notice, never a penalty.
    out = _selector().select_act(retrieved_sections=["32"])
    assert out["abstain_reason"] is None
    assert out["fso_act"]["escalation_level"] == "IMPROVEMENT_NOTICE"
    assert "Section 32" in out["fso_act"]["statutory_anchor"]


def test_selection_is_deterministic():
    sel = _selector()
    first = sel.select_act(retrieved_sections=["51", "55"])
    second = sel.select_act(retrieved_sections=["51", "55"])
    assert first == second
    # Two converging anchors: base 0.7 + 0.1 agreement bonus.
    assert first["fso_act"]["confidence"] == 0.8


def test_confidence_scales_with_evidence():
    sel = _selector()
    thin = sel.select_act(retrieved_sections=["51"])["fso_act"]["confidence"]
    assert thin == 0.7
    with_lab = sel.select_act(retrieved_sections=["51"], lab_report_available=True)["fso_act"]["confidence"]
    assert with_lab == 0.8
    converging = sel.select_act(retrieved_sections=["51", "52", "55"], lab_report_available=True)["fso_act"][
        "confidence"
    ]
    assert converging == 1.0


def test_optionality_index_spot_values():
    import pytest

    from app.rag.advisor.ladder import ACTION_PROFILES, EscalationLevel

    sel = _selector()
    sample = ACTION_PROFILES[EscalationLevel.SAMPLE_LAB_TEST]
    assert sel.optionality_score(sample) == pytest.approx(0.85)
    prosecution = ACTION_PROFILES[EscalationLevel.PROSECUTION]
    assert sel.optionality_score(prosecution) == pytest.approx(-0.5)


def test_floor_act_is_the_optionality_argmax():
    """The floor table and the optionality argmax provably coincide.

    For every admissible floor, the floor act has the highest optionality
    score — so selecting the floor act directly is exactly the argmax, not
    a shortcut. Fails loudly if payoff retuning ever breaks the ordering.
    """
    from app.rag.advisor.ladder import ACTION_PROFILES, EscalationLevel

    sel = _selector()
    # Only SAMPLE..PROSECUTION are reachable floors (INSPECT_WARN is the
    # ladder's bottom rung but no evidence pattern floors at it).
    reachable = [
        EscalationLevel.SAMPLE_LAB_TEST,
        EscalationLevel.IMPROVEMENT_NOTICE,
        EscalationLevel.PENALTY_DIRECTION,
        EscalationLevel.PROSECUTION,
    ]
    for floor in reachable:
        admissible = {lvl: prof for lvl, prof in ACTION_PROFILES.items() if lvl >= floor}
        argmax = max(admissible.values(), key=sel.optionality_score)
        assert argmax.level == floor


def test_payload_carries_both_bases_and_citation():
    out = _selector().select_act(retrieved_sections=["52"])
    act = out["fso_act"]
    assert act["game_theory_basis"]
    assert act["talebian_basis"]
    assert act["citations"] == ["Section 52"]


def test_compute_fso_advisory_convenience_wrapper():
    from app.rag.advisor import compute_fso_advisory

    out = compute_fso_advisory(["56"])
    assert out["abstain_reason"] is None
    assert out["fso_act"] is not None


# ---------------------------------------------------------------------------
# Section extraction (node seam)
# ---------------------------------------------------------------------------


def _chunks():
    return [
        {"chunk_id": "c1", "section_number": "51", "text": "sub-standard food"},
        {"chunk_id": "c2", "text": "direction under Section 55 of the Act"},
        {"chunk_id": "c3", "text": "no legal marker here"},
    ]


def test_extract_sections_prefers_field_then_text():
    from app.rag.agent.nodes.advisory import extract_sections

    assert extract_sections(_chunks()) == ["51", "55"]


def test_extract_sections_verified_only_drops_hallucinated_citations():
    from app.rag.agent.nodes.advisory import extract_sections

    response = {"citations": [{"chunk_id": "c99", "section_number": "63"}]}
    assert extract_sections(_chunks(), response, verified_only=True) == ["51", "55"]
    assert extract_sections(_chunks(), response, verified_only=False) == ["51", "55", "63"]


def test_extract_sections_accepts_dag_evidence_chunks():
    from app.rag.agent.nodes import fso_advisory_node
    from app.rag.agent.state import initial_state

    state = initial_state("q")
    state.update({
        "chunks": [],
        "evidence": {"T1": [{"chunk_id": "t1", "section_number": "52", "text": "x"}]},
        "response": {"citations": []},
    })
    out = fso_advisory_node(state)  # type: ignore[arg-type]
    assert out["extracted_sections"] == ["52"]
    assert out["fso_act"]["escalation_level"] == "SAMPLE_LAB_TEST"


# ---------------------------------------------------------------------------
# Authoritative (post-verification) node
# ---------------------------------------------------------------------------


def test_post_node_abstains_on_empty_evidence():
    from app.rag.agent.nodes import fso_advisory_node
    from app.rag.agent.state import initial_state

    state = initial_state("q")
    out = fso_advisory_node(state)  # type: ignore[arg-type]
    assert out["fso_act"] is None
    assert out["advisory_abstain_reason"] == "insufficient_statutory_grounding"


def test_post_node_abstains_when_citation_gate_failed():
    from app.rag.agent.nodes import fso_advisory_node
    from app.rag.agent.state import initial_state

    state = initial_state("q")
    state.update({
        "chunks": [{"chunk_id": "c1", "text": "generic food safety text"}],
        "citation_quality_ok": False,
        "response": {"citations": [{"chunk_id": "c99", "section_number": "63"}]},
    })
    out = fso_advisory_node(state)  # type: ignore[arg-type]
    assert out["fso_act"] is None
    assert out["audit_trail"][-1]["detail"]["reason"] == "citation_gate"


def test_finalize_attaches_fso_act_additively():
    from app.rag.agent.nodes import finalize_node
    from app.rag.agent.state import initial_state

    state = initial_state("penalty for substandard food?")
    state.update({
        "query_type": "offence",
        "answer": "Section 51 applies.",
        "groundedness": 0.9,
        "fso_advisory_enabled": True,
        "fso_act": {"action": "Sample & Lab-Test", "escalation_level": "SAMPLE_LAB_TEST"},
        "extracted_sections": ["51"],
        "response": {"answer": "Section 51 applies.", "citations": []},
    })
    resp = finalize_node(state)["response"]  # type: ignore[arg-type]
    assert resp["answer"] == "Section 51 applies."
    assert resp["fso_act"]["escalation_level"] == "SAMPLE_LAB_TEST"
    assert "extracted_sections" not in resp["agent"]  # state-only telemetry


def test_finalize_omits_fso_act_when_disabled():
    from app.rag.agent.nodes import finalize_node
    from app.rag.agent.state import initial_state

    resp = finalize_node(initial_state("q?"))["response"]  # type: ignore[arg-type]
    assert "fso_act" not in resp
    assert "advisory_abstain_reason" not in resp


# ---------------------------------------------------------------------------
# Graph integration (both gates)
# ---------------------------------------------------------------------------


def _patch_grounded_pipeline(monkeypatch):
    import app.rag.tasks as tasks

    chunks = [{"chunk_id": "c1", "score": 0.9, "text": "Section 51 text", "section_number": "51"}]
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
            "answer": "Section 51 prescribes the penalty.",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )


def test_graph_topology_contains_gate_when_on():
    from app.rag.agent.graph import build_graph

    graph = build_graph(fso_advisor=True)
    nodes = set(graph.get_graph().nodes.keys())
    assert "fso_advisory" in nodes
    assert "fso_advisory_hint" not in nodes


def test_graph_topology_omits_gate_when_off():
    from app.rag.agent.graph import build_graph

    graph = build_graph(fso_advisor=False)
    nodes = set(graph.get_graph().nodes.keys())
    assert "fso_advisory" not in nodes


def test_end_to_end_attaches_act(monkeypatch):
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    _patch_grounded_pipeline(monkeypatch)
    result = run_agent(initial_state("penalty for selling substandard food"), fso_advisor=True)
    assert result["fso_act"]["escalation_level"] == "SAMPLE_LAB_TEST"
    assert "Section 51" in result["fso_act"]["statutory_anchor"]
    assert result["answer"] == "Section 51 prescribes the penalty."
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert "fso_advisory" in nodes_run
    # Authoritative Act runs post-verification.
    assert nodes_run.index("fso_advisory") > nodes_run.index("citation_quality")


def test_end_to_end_dag_path_gate_after_quality(monkeypatch):
    """DAG queries route execute_task → evidence_sufficiency (no hint hop)."""
    import app.rag.tasks as tasks
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    chunks = [
        {
            "chunk_id": "c1",
            "score": 0.9,
            "text": "Section 52 misbranded food penalty text with sufficient detail",
            "section_number": "52",
        }
    ]
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
            "answer": "Section 52 prescribes the penalty for misbranded food.",
            "groundedness_score": 0.9,
            "hallucination_detected": False,
            "query_type": "offence",
        },
    )
    result = run_agent(
        initial_state("penalty for selling substandard food and define misbranded food"),
        fso_advisor=True,
    )
    assert result["fso_act"]["escalation_level"] == "SAMPLE_LAB_TEST"
    assert "Section 52" in result["fso_act"]["statutory_anchor"]
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert nodes_run.index("evidence_sufficiency") == nodes_run.index("execute_task") + 1
    assert nodes_run.index("fso_advisory") > nodes_run.index("citation_quality")


def test_no_hint_routing_helper():
    from app.rag.agent import graph

    assert not hasattr(graph, "_route_after_hint")


# ---------------------------------------------------------------------------
# Service + route validation
# ---------------------------------------------------------------------------


def test_service_rejects_non_bool_advisory_flags():
    from app.rag.agent.service import resume_agent_query, run_agent_query

    assert run_agent_query(query="q", fso_advisor="yes")[0] == 400
    assert run_agent_query(query="q", is_repeat_offender="yes")[0] == 400
    assert run_agent_query(query="q", has_lab_report=1)[0] == 400
    assert resume_agent_query(thread_id="t", approved=True, hitl=True, fso_advisor="yes")[0] == 400


def test_extract_sections_reads_nested_payload_and_identity():
    from app.rag.agent.nodes.advisory import extract_sections

    chunks = [
        {"chunk_id": "c1", "payload": {"section_number": "55"}},
        {"chunk_id": "c2", "legal_identity": {"section": "56"}},
        {"chunk_id": "c3", "chunk_text": "penalty under Section 58 of the Act"},
    ]
    assert extract_sections(chunks) == ["55", "56", "58"]


def test_route_rejects_non_bool_advisory_body():
    from tests.test_rag_routes import _setup_test_env

    _, client, ctx = _setup_test_env()
    try:
        resp = client.post(
            "/api/rag/query/agent",
            json={"query": "q", "use_agent": True, "fso_advisory": "yes"},
        )
        assert resp.status_code == 400
        resp = client.post(
            "/api/rag/query/agent",
            json={"query": "q", "use_agent": True, "is_repeat_offender": "yes"},
        )
        assert resp.status_code == 400
    finally:
        ctx.pop()


def test_end_to_end_no_act_when_off(monkeypatch):
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    _patch_grounded_pipeline(monkeypatch)
    result = run_agent(initial_state("penalty for selling substandard food"), fso_advisor=False)
    assert "fso_act" not in result
    nodes_run = [e["node"] for e in result["agent"]["audit_trail"]]
    assert "fso_advisory" not in nodes_run
    assert "fso_advisory_hint" not in nodes_run
