"""End-to-end tracer test for the FSO strategic advisory agent (ADR-0003).

Proves the whole vertical slice: a full ``run_agent`` pass over the real
compiled graph with **synthetic retrieval** (``run_retrieval_pipeline``
monkeypatched to hand back fixed § chunks) and the **stub LLM** (the real
``run_generation_pipeline`` running through ``GroundedLLMClient`` stub mode,
pinned by the autouse ``_rag_stub_llm_env`` fixture in ``tests/conftest.py``).
No Qdrant, no network, no torch.

The three observable contracts:

* advisory **on** + grounded section-55 chunk → ``response["fso_act"]``
  carries the section-55 statutory anchor;
* advisory **off** → ``response`` has no ``fso_act`` (backwards compatible);
* no grounded section → ``fso_act`` is ``None`` with an abstain reason while
  the grounded legal answer is still returned.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("_rag_stub_llm_env")


def _patch_retrieval(monkeypatch, chunks):
    """Hand the graph synthetic chunks; generation still runs (stub LLM)."""
    from app.rag import tasks

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


def _section_55_chunks():
    return [
        {
            "chunk_id": "c55",
            "score": 0.9,
            "section_number": "55",
            "text": "Section 55 — failure to comply with a direction of the Food Safety Officer.",
        },
    ]


def _no_section_chunks():
    return [
        {
            "chunk_id": "c0",
            "score": 0.9,
            "text": "General hygienic practice guidance with no statutory section marker.",
        },
    ]


def _nodes_run(result):
    return [e["node"] for e in result["agent"]["audit_trail"]]


# ---------------------------------------------------------------------------
# Advisory on: the whole slice returns a grounded Act
# ---------------------------------------------------------------------------


def test_advisory_on_section_55_query_returns_act(monkeypatch):
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    _patch_retrieval(monkeypatch, _section_55_chunks())
    result = run_agent(
        initial_state("What action may the FSO take on a Section 55 direction?"),
        fso_advisor=True,
    )

    act = result["fso_act"]
    assert act is not None
    assert "Section 55" in act["statutory_anchor"]
    assert act["game_theory_basis"]
    assert act["talebian_basis"]
    assert act["citations"] == ["Section 55"]
    # The grounded legal answer is unchanged by the advisory.
    assert result["answer"]
    assert result["pipeline"] == "agent"
    nodes_run = _nodes_run(result)
    assert "fso_advisory" in nodes_run
    assert nodes_run.index("fso_advisory") > nodes_run.index("citation_quality")


# ---------------------------------------------------------------------------
# Advisory off: backwards compatible — no fso_act anywhere
# ---------------------------------------------------------------------------


def test_advisory_off_section_55_query_has_no_act(monkeypatch):
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    _patch_retrieval(monkeypatch, _section_55_chunks())
    result = run_agent(
        initial_state("What action may the FSO take on a Section 55 direction?"),
        fso_advisor=False,
    )

    assert "fso_act" not in result
    assert "advisory_abstain_reason" not in result
    assert "fso_advisory" not in _nodes_run(result)
    assert result["answer"]


# ---------------------------------------------------------------------------
# No grounded section: abstain, but still answer
# ---------------------------------------------------------------------------


def test_advisory_on_no_section_query_abstains(monkeypatch):
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    _patch_retrieval(monkeypatch, _no_section_chunks())
    result = run_agent(
        initial_state("What does the Act say about hygienic practice in general?"),
        fso_advisor=True,
    )

    assert result["fso_act"] is None
    assert result["advisory_abstain_reason"] == "insufficient_statutory_grounding"
    # Abstention from the advisory never withholds the grounded answer.
    assert result["answer"]
    assert result["pipeline"] == "agent"
