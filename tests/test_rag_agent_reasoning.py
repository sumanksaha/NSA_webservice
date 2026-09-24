"""Tests for roadmap Phase 3 graph integration — reasoning/audit nodes + routing.

Seam S2: ``structured_reasoner_node`` / ``auditor_node`` /
``route_after_audit`` and the flag-gated ``build_graph`` topology.
LLM access goes through ``StructuredReasoner`` (monkeypatched here);
no network, no real model.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.rag.agent import nodes
from app.rag.agent.graph import build_graph, route_after_audit
from app.rag.agent.nodes import auditor_node
from app.rag.agent.nodes import reasoning as reasoning_module


class _FakeReasoner:
    """Stand-in for StructuredReasoner (same constructor + reason shape)."""

    instances: ClassVar[list[_FakeReasoner]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.contexts: list[str] = []
        _FakeReasoner.instances.append(self)

    def reason(self, question: str, context: str) -> object:
        from app.rag.generation.structured_reasoner import StructuredLegalArgument

        self.contexts.append(context)
        return StructuredLegalArgument(
            issue=question,
            applicable_provisions=["FSS_ACT::31"],
            derived_conclusion="A licence is required.",
            supporting_citations=["FSS_ACT::31"],
        )


def _chunks() -> list[dict]:
    return [
        {"chunk_id": "c1", "text": "Section 31 requires a licence.", "section_number": "31"},
        {"chunk_id": "c2", "text": "Provided that petty retailers are exempt.", "section_number": "31"},
    ]


def _patch_reasoner(monkeypatch) -> None:
    _FakeReasoner.instances.clear()
    monkeypatch.setattr(reasoning_module, "StructuredReasoner", _FakeReasoner)


class TestStructuredReasonerNode:
    def test_sets_argument_and_unit_evidence(self, monkeypatch):
        _patch_reasoner(monkeypatch)
        update = reasoning_module.structured_reasoner_node({"query": "Licence?", "chunks": _chunks()})
        assert update["structured_argument"]["derived_conclusion"] == "A licence is required."
        assert [e["id"] for e in update["legal_unit_evidence"]] == ["c1", "c2"]
        assert update["revision_count"] == 0

    def test_revision_pass_appends_defects_and_counts(self, monkeypatch):
        _patch_reasoner(monkeypatch)
        state = {
            "query": "Licence?",
            "chunks": _chunks(),
            "revision_count": 0,
            "audit_result": {
                "status": "FAIL",
                "defects": [
                    {
                        "defect_type": "missed_exception",
                        "explanation": "ex",
                        "required_correction": "check the proviso",
                    }
                ],
            },
        }
        update = reasoning_module.structured_reasoner_node(state)
        assert update["revision_count"] == 1
        assert "check the proviso" in _FakeReasoner.instances[-1].contexts[-1]


class TestAuditorNodeWiring:
    def test_node_writes_audit_result(self):
        from app.rag.generation.structured_reasoner import StructuredLegalArgument

        argument = StructuredLegalArgument(
            issue="Licence?",
            applicable_provisions=["FSS_ACT::31"],
            derived_conclusion="Required.",
            supporting_citations=["FSS_ACT::31"],
        ).model_dump()
        update = auditor_node({
            "structured_argument": argument,
            "legal_unit_evidence": [{"id": "FSS_ACT::31", "text": "Section 31 requires a licence."}],
        })
        assert update["audit_result"]["status"] == "PASS"

    def test_node_noop_without_argument(self):
        assert auditor_node({}) == {}

    def test_node_accepts_canonical_unit_citations(self):
        from app.rag.generation.structured_reasoner import StructuredLegalArgument

        argument = StructuredLegalArgument(
            issue="Licence?",
            applicable_provisions=["FSS_ACT::31"],
            exceptions_considered=[{"rule": "31", "exception": "none", "applies": False}],
            derived_conclusion="Required.",
            supporting_citations=["FSS_ACT::31"],
        ).model_dump()
        update = auditor_node({
            "structured_argument": argument,
            "legal_unit_evidence": [{"id": "uuid-1", "unit": "FSS_ACT::31", "text": "Section 31."}],
        })
        assert update["audit_result"]["status"] == "PASS"


class TestRouteAfterAudit:
    def test_pass_goes_to_generate(self):
        assert route_after_audit({"audit_result": {"status": "PASS"}, "revision_count": 0}) == "generate"

    def test_fail_with_budget_revises(self):
        state = {"audit_result": {"status": "FAIL", "defects": [{}]}, "revision_count": 0, "max_revisions": 1}
        assert route_after_audit(state) == "structured_reasoner"

    def test_fail_exhausted_goes_to_generate(self):
        state = {"audit_result": {"status": "FAIL", "defects": [{}]}, "revision_count": 1, "max_revisions": 1}
        assert route_after_audit(state) == "generate"

    def test_missing_audit_goes_to_generate(self):
        assert route_after_audit({}) == "generate"

    def test_missing_max_revisions_uses_default_cap(self):
        # Missing max_revisions falls back to DEFAULT_MAX_REVISIONS (1),
        # matching initial_state / Experiment D — not a looser cap of 2.
        state = {"audit_result": {"status": "FAIL", "defects": [{}]}, "revision_count": 1}
        assert route_after_audit(state) == "generate"
        state0 = {"audit_result": {"status": "FAIL", "defects": [{}]}, "revision_count": 0}
        assert route_after_audit(state0) == "structured_reasoner"


class TestReasoningTopology:
    def test_nodes_present_when_flags_on(self, monkeypatch):
        monkeypatch.setenv("ENABLE_STRUCTURED_REASONER", "true")
        monkeypatch.setenv("ENABLE_LEGAL_AUDITOR", "true")
        found = set(build_graph().get_graph().nodes.keys())
        assert {"structured_reasoner", "auditor"} <= found

    def test_nodes_absent_by_default(self, monkeypatch):
        monkeypatch.setenv("ENABLE_STRUCTURED_REASONER", "false")
        monkeypatch.setenv("ENABLE_LEGAL_AUDITOR", "false")
        found = set(build_graph().get_graph().nodes.keys())
        assert "structured_reasoner" not in found
        assert "auditor" not in found

    def test_reasoner_without_auditor_goes_straight_to_generate(self, monkeypatch):
        monkeypatch.setenv("ENABLE_STRUCTURED_REASONER", "true")
        monkeypatch.setenv("ENABLE_LEGAL_AUDITOR", "false")
        found = set(build_graph().get_graph().nodes.keys())
        assert "structured_reasoner" in found
        assert "auditor" not in found

    def test_reasoning_names_exported(self):
        assert nodes.structured_reasoner_node is reasoning_module.structured_reasoner_node
        assert callable(nodes.auditor_node)


class TestGenerateConsumesArgument:
    def test_argument_prepended_to_generation_query(self, monkeypatch):
        import app.rag.tasks as tasks
        from app.rag.agent.nodes.linear import generate_node

        captured = {}
        monkeypatch.setattr(
            tasks, "run_generation_pipeline", lambda query, **kw: (captured.update(query=query), {"answer": "A."})[1]
        )
        state = {
            "query": "Licence?",
            "chunks": [],
            "query_type": "general",
            "structured_argument": {
                "issue": "Licence?",
                "derived_conclusion": "A licence is required.",
                "supporting_citations": [],
            },
        }
        out = generate_node(state)
        assert out["answer"] == "A."
        assert "Structured reasoning" in captured["query"]
        assert "A licence is required." in captured["query"]

    def test_query_unchanged_without_argument(self, monkeypatch):
        import app.rag.tasks as tasks
        from app.rag.agent.nodes.linear import generate_node

        captured = {}
        monkeypatch.setattr(
            tasks, "run_generation_pipeline", lambda query, **kw: (captured.update(query=query), {"answer": "A."})[1]
        )
        generate_node({"query": "Licence?", "chunks": [], "query_type": "general"})
        assert captured["query"] == "Licence?"

    def test_fallback_skeleton_still_flows_through(self, monkeypatch):
        import app.rag.tasks as tasks
        from app.rag.agent.nodes.linear import generate_node

        captured = {}
        monkeypatch.setattr(
            tasks, "run_generation_pipeline", lambda query, **kw: (captured.update(query=query), {"answer": "A."})[1]
        )
        generate_node({
            "query": "Licence?",
            "chunks": [],
            "query_type": "general",
            "structured_argument": {"issue": "Licence?", "uncertainties": ["reasoning unavailable"]},
        })
        assert "Structured reasoning" in captured["query"]


class TestReasoningPathInvoke:
    """Stub-validated end-to-end regression (S4): no network, no Qdrant."""

    def _fake_pipelines(self, monkeypatch, chunks):
        import app.rag.tasks as tasks

        monkeypatch.setattr(
            tasks,
            "run_retrieval_pipeline",
            lambda query, **kw: {"chunks": chunks, "query_type": "general"},
        )

        def fake_generate(query, **kw):
            fake_generate.captured = query
            return {
                "answer": "Penalty applies.",
                "groundedness_score": 0.95,
                "hallucination_detected": False,
                "citations": [],
            }

        monkeypatch.setattr(tasks, "run_generation_pipeline", fake_generate)
        return fake_generate

    def test_invoke_pass_path(self, monkeypatch):
        from app.rag.agent.state import initial_state

        _patch_reasoner(monkeypatch)
        chunks = [{"chunk_id": "c1", "text": "Section 51 penalty for substandard food.", "section_number": "51"}]
        fake_generate = self._fake_pipelines(monkeypatch, chunks)
        graph = build_graph(structured_reasoner=True, legal_auditor=True)
        result = graph.invoke(initial_state("penalty for selling substandard food"))
        assert result["structured_argument"]["derived_conclusion"] == "A licence is required."
        assert result["audit_result"]["status"] == "PASS"
        assert result["revision_count"] == 0
        assert result["answer"] == "Penalty applies."
        assert "Structured reasoning" in fake_generate.captured

    def test_invoke_fail_revises_once_then_generates(self, monkeypatch):
        from app.rag.agent.state import initial_state

        _patch_reasoner(monkeypatch)
        chunks = [
            {"chunk_id": "c1", "text": "Section 51 penalty for substandard food.", "section_number": "51"},
            {"chunk_id": "c2", "text": "Provided that petty shops are exempt.", "section_number": "51"},
        ]
        self._fake_pipelines(monkeypatch, chunks)
        graph = build_graph(structured_reasoner=True, legal_auditor=True)
        result = graph.invoke(initial_state("penalty for selling substandard food"))
        assert result["audit_result"]["status"] == "FAIL"
        assert result["revision_count"] == 1  # capped: no infinite loop
        assert result["answer"] == "Penalty applies."
