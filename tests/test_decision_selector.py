"""Unit tests for the decision-model selection seam (plan steps 1-2).

Decision model = ``typesafe/jev-1.13`` (typed decision model over
OpenRouter's Decisions API; ``typesafe/jev-router`` is banned and refused);
answer model = Laguna (unchanged).  Covers the decisions client contract
(state + noul questions, batched, clamped, missing-answer fail-closed,
unsanctioned-model refusal), the reservoir merge (chunk_id + legal-unit
dedupe, truncation), the pure selection walk (threshold → top-k → dedupe →
min-size backfill), the heuristic fallback, the retrieval-stage gating (off
by default, on under RAG_DECISION_SHADOW / RAG_DECISION_PATH), and the
generation wiring (no ``decision_trace`` key when flags off; selection +
trace forwarded when RAG_DECISION_PATH serves).

Everything runs offline: the Decisions-API seam is stubbed, the chat
service double unused by sanctioned models.
"""

from __future__ import annotations

import json

import pytest

from app.rag.generation import GroundedGenerationService
from app.rag.retrieval.decision_selector import (
    DecisionModelClient,
    decision_enabled,
    merge_reservoir,
    select_decision_set,
    select_from_scores,
)
from app.rag.retrieval.result import RetrievedChunk, SearchResult
from app.rag.retrieval.stages import POST_RETRIEVAL_STAGES
from app.rag.tasks import run_generation_pipeline
from app.shared.config import cfg

QUERY = "What is the penalty under Section 55?"
ACT = "Food Safety and Standards Act, 2006"
RESOLVED = "typesafe/jev-1.13"  # Decisions API: served model == requested


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _StubClient:
    """Offline DecisionModelClient double: fixed P(keep) per candidate index."""

    model = "typesafe/jev-1.13"

    def __init__(self, probs: dict[int, float], resolved: str | None = RESOLVED):
        self._probs = probs
        self._resolved = resolved
        self.calls = 0

    def decide_keep_scores(self, query: str, candidates: list[dict]) -> tuple[dict[int, float], dict]:
        self.calls += 1
        probs = {i: float(self._probs.get(i, 0.1)) for i in range(len(candidates))}
        return probs, {"model": self.model, "resolved_model": self._resolved, "tokens": 42}


class _FailingClient(_StubClient):
    def decide_keep_scores(self, query: str, candidates: list[dict]) -> tuple[dict[int, float], dict]:
        self.calls += 1
        raise RuntimeError("decision transport down")


def _noul_data(body: dict, values: dict[int, float], *, model: str = RESOLVED, usage: dict | None = None) -> dict:
    """Build a Decisions-API response for the posted questions."""
    answers = {qid: {"noul": float(values.get(int(qid[1:]), 0.0))} for qid in body["questions"]}
    data: dict = {"answers": answers, "model": model}
    if usage:
        data["usage"] = usage
    return data


def _install_decisions(monkeypatch, make_data):
    """Stub ``DecisionModelClient._post_decisions``; return captured bodies.

    *make_data* receives each posted body and returns the response dict
    (or raises, to simulate a transport/HTTP failure).
    """
    bodies: list[dict] = []

    def _post(self, body, headers, *, timeout):
        bodies.append(body)
        return make_data(body)

    monkeypatch.setattr(DecisionModelClient, "_post_decisions", _post)
    return bodies


def _chunk(cid: str, section: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        score=score,
        text=f"Section {section} of the {ACT} imposes a penalty for contravention.",
        section_number=section,
        act_name=ACT,
        document_title=ACT,
        document_type="act",
        authority="FSSAI",
    )


def _candidates(n: int) -> list[dict]:
    return [
        {
            "index": i,
            "chunk_id": f"c{i}",
            "section": str(10 + i),
            "act": ACT,
            "score": 1.0 - i * 0.05,
            "text": f"Section {10 + i} penalty text.",
        }
        for i in range(n)
    ]


def _stage(name: str):
    return next(s for s in POST_RETRIEVAL_STAGES if s.name == name)


def _spy_generate(monkeypatch, captured: dict) -> None:
    """Capture the evidence_set kwarg of GroundedGenerationService.generate."""
    original = GroundedGenerationService.generate

    def spy(self, query, chunks, query_type="", **kwargs):
        captured["evidence_set"] = kwargs.get("evidence_set")
        return original(self, query, chunks, query_type=query_type, **kwargs)

    monkeypatch.setattr(GroundedGenerationService, "generate", spy)


@pytest.fixture(autouse=True)
def _pinned_flags(monkeypatch):
    """Pin the decision flags so a developer's .env cannot leak into tests."""
    monkeypatch.setattr(cfg, "decision_path", False, raising=False)
    monkeypatch.setattr(cfg, "decision_shadow", False, raising=False)
    monkeypatch.setattr(cfg, "decision_threshold", 0.5, raising=False)
    monkeypatch.setattr(cfg, "decision_top_k", 6, raising=False)
    monkeypatch.setattr(cfg, "decision_reservoir", 50, raising=False)
    monkeypatch.setattr(cfg, "decision_model", "typesafe/jev-1.13", raising=False)
    # Decisions calls need a key present; the unconfigured test deletes it.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")


# --------------------------------------------------------------------------- #
# Chat client contract
# --------------------------------------------------------------------------- #
class TestDecisionModelClient:
    def test_scores_every_candidate_and_reports_decisions_transport(self, monkeypatch):
        bodies = _install_decisions(
            monkeypatch,
            lambda body: _noul_data(body, {0: 0.9, 1: 0.25}, usage={"input_tokens": 60, "output_tokens": 17}),
        )

        probs, info = DecisionModelClient().decide_keep_scores(QUERY, _candidates(2))

        assert probs == {0: 0.9, 1: 0.25}
        assert info == {"model": "typesafe/jev-1.13", "resolved_model": RESOLVED, "tokens": 77, "transport": "decisions"}
        assert len(bodies) == 1
        assert bodies[0]["model"] == "typesafe/jev-1.13"

    def test_state_and_questions_carry_query_candidates_and_rules(self, monkeypatch):
        bodies = _install_decisions(monkeypatch, lambda body: _noul_data(body, {}))

        DecisionModelClient().decide_keep_scores(QUERY, _candidates(2))

        body = bodies[0]
        assert body["state"]["query"] == QUERY
        assert len(body["state"]["candidates"]) == 2
        assert body["state"]["candidates"][1]["section"] == "11"
        q0 = body["questions"]["c0"]
        assert q0["type"] == "noul"
        # question keys are not sent to the model — instructions point at the path
        assert "candidates[0]" in q0["instructions"]
        assert set(q0["criteria"]) == {"true", "false"}

    def test_scores_clamped_to_unit_interval(self, monkeypatch):
        _install_decisions(monkeypatch, lambda body: _noul_data(body, {0: 1.7, 1: -0.3}))
        probs, _ = DecisionModelClient().decide_keep_scores(QUERY, _candidates(2))
        assert probs == {0: 1.0, 1: 0.0}

    def test_missing_noul_answer_fails_closed(self, monkeypatch):
        def make(body):
            data = _noul_data(body, {0: 0.9})
            data["answers"].pop("c1")
            return data

        _install_decisions(monkeypatch, make)
        with pytest.raises(ValueError, match="missing noul answer for candidate 1"):
            DecisionModelClient().decide_keep_scores(QUERY, _candidates(2))

    def test_http_error_propagates_for_the_fallback(self, monkeypatch):
        def make(body):
            raise RuntimeError("HTTP 422: request_token_limit_exceeded")

        _install_decisions(monkeypatch, make)
        with pytest.raises(RuntimeError, match="422"):
            DecisionModelClient().decide_keep_scores(QUERY, _candidates(2))

    def test_unconfigured_transport_raises(self, monkeypatch):
        bodies = _install_decisions(monkeypatch, lambda body: _noul_data(body, {}))
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("AI_ASSISTANT_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="not configured"):
            DecisionModelClient().decide_keep_scores(QUERY, _candidates(2))
        assert bodies == []  # no key → no request

    def test_empty_candidates_skip_the_call(self, monkeypatch):
        bodies = _install_decisions(monkeypatch, lambda body: _noul_data(body, {}))
        probs, info = DecisionModelClient().decide_keep_scores(QUERY, [])
        assert probs == {}
        assert info == {}
        assert bodies == []

    def test_large_pool_is_batched_and_mapped_back(self, monkeypatch):
        bodies = _install_decisions(monkeypatch, lambda body: _noul_data(body, {}))

        probs, _ = DecisionModelClient().decide_keep_scores(QUERY, _candidates(60))

        assert set(probs) == set(range(60))
        assert [len(b["state"]["candidates"]) for b in bodies] == [50, 10]
        # batch-local path in instructions, original index in the question key
        assert "candidates[0]" in bodies[1]["questions"]["c50"]["instructions"]

    def test_unsanctioned_model_refuses_to_construct(self):
        """The jev-router ban fails closed: no client, no request, no spend."""
        with pytest.raises(ValueError, match="sanctioned"):
            DecisionModelClient(model="typesafe/jev-router")


# --------------------------------------------------------------------------- #
# Selection contract
# --------------------------------------------------------------------------- #
class TestSelectDecisionSet:
    def test_threshold_filters_and_ranks_by_probability(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _StubClient({0: 0.9, 1: 0.8, 2: 0.2}),
        )
        chunks = [_chunk("c0", "55"), _chunk("c1", "56"), _chunk("c2", "57")]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert trace["source"] == "decision_model"
        assert trace["kept"] == ["c0", "c1"]  # c2 below threshold 0.5
        assert trace["abstain"] is False
        assert trace["model"] == "typesafe/jev-1.13"
        assert trace["resolved_model"] == RESOLVED
        assert trace["tokens"] == 42
        assert trace["keep_scores"]["c2"] == pytest.approx(0.2)
        # Confidence carries the model's P(keep), not the CE score.
        assert evidence["items"][0]["chunk_id"] == "c0"
        assert evidence["items"][0]["confidence"] == pytest.approx(0.9)
        assert evidence["total_pool"] == 3

    def test_same_unit_deduped_in_reservoir_before_scoring(self, monkeypatch):
        """c0/c1 are one legal unit (same act + section) — the merge keeps
        the best-scored member and only that one is ever scored."""
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _StubClient({0: 0.95, 1: 0.9, 2: 0.85}),
        )
        chunks = [_chunk("c0", "31"), _chunk("c1", "31"), _chunk("c2", "92")]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert trace["kept"] == ["c0", "c2"]
        assert trace["deduped"] == 1  # c1 collapsed into c0's unit
        assert trace["scored"] == 2  # only the reservoir was scored
        assert trace["dropped_duplicates"] == []  # nothing left for the walk to drop
        assert [it["chunk_id"] for it in evidence["items"]] == ["c0", "c2"]

    def test_reservoir_truncation_limits_what_is_scored(self, monkeypatch):
        monkeypatch.setattr(cfg, "decision_reservoir", 3, raising=False)
        client = _StubClient({i: 0.9 for i in range(3)})
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: client,
        )
        chunks = [_chunk(f"c{i}", str(10 + i)) for i in range(8)]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert trace["pool"] == 8  # full input pool
        assert trace["scored"] == 3  # reservoir is what got scored
        assert trace["reservoir_limit"] == 3
        assert trace["kept"] == ["c0", "c1", "c2"]  # head of the ranked pool only
        assert evidence["total_pool"] == 8  # full pool still reported

    def test_top_k_caps_the_selection(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _StubClient({i: 0.9 for i in range(9)}),
        )
        chunks = [_chunk(f"c{i}", str(10 + i)) for i in range(9)]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert len(evidence["items"]) == 6  # cfg.decision_top_k pinned to 6
        assert trace["top_k"] == 6

    def test_all_below_threshold_backfills_min_size_and_flags_abstain(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _StubClient({i: 0.1 for i in range(4)}),
        )
        chunks = [_chunk(f"c{i}", str(10 + i)) for i in range(4)]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert trace["abstain"] is True  # nothing cleared τ…
        assert len(evidence["items"]) == 2  # …but the prompt never goes empty
        assert evidence["items"][0]["confidence"] == pytest.approx(0.1)

    def test_client_failure_fails_closed_to_heuristic(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _FailingClient({}),
        )
        chunks = [_chunk("c0", "55"), _chunk("c1", "56")]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert trace["source"] == "heuristic"
        assert "decision transport down" in trace["fallback_reason"]
        assert evidence is not None and evidence["items"]  # still a usable set

    def test_unsanctioned_config_fails_closed_to_heuristic(self, monkeypatch):
        """A banned RAG_DECISION_MODEL (jev-router) never serves or spends."""
        monkeypatch.setattr(cfg, "decision_model", "typesafe/jev-router", raising=False)
        chunks = [_chunk("c0", "55"), _chunk("c1", "56")]
        evidence, trace = select_decision_set(QUERY, chunks)

        assert trace["source"] == "heuristic"
        assert "not a sanctioned decision model" in trace["fallback_reason"]
        assert evidence is not None and evidence["items"]  # still a usable set

    def test_empty_pool_returns_none_without_a_call(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: pytest.fail("must not call the decision model on an empty pool"),
        )
        evidence, trace = select_decision_set(QUERY, [])

        assert evidence is None
        assert trace["source"] == "empty"

    def test_trace_is_json_serializable_for_the_audit_log(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _StubClient({0: 0.9, 1: 0.8}),
        )
        chunks = [_chunk("c0", "55"), _chunk("c1", "56")]
        evidence, trace = select_decision_set(QUERY, chunks)

        json.dumps(trace)
        json.dumps({**trace, "evidence_set": evidence})

    def test_candidate_snippets_are_truncated(self, monkeypatch):
        seen: dict = {}

        class _Capture(_StubClient):
            def decide_keep_scores(self, query, candidates):
                seen["candidates"] = candidates
                return super().decide_keep_scores(query, candidates)

        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _Capture({0: 0.9}),
        )
        long_chunk = RetrievedChunk(
            chunk_id="c0", score=1.0, text="x" * 5000, section_number="55", act_name=ACT
        )
        select_decision_set(QUERY, [long_chunk])

        assert len(seen["candidates"][0]["text"]) == 400
        assert seen["candidates"][0]["chunk_id"] == "c0"


# --------------------------------------------------------------------------- #
# Reservoir merge + pure selection walk
# --------------------------------------------------------------------------- #
class TestMergeReservoir:
    def test_chunk_id_duplicates_collapse(self):
        chunks = [_chunk("c0", "55"), _chunk("c0", "55"), _chunk("c1", "56")]
        out = merge_reservoir(chunks, 50)
        assert [c.chunk_id for c in out] == ["c0", "c1"]

    def test_legal_unit_duplicates_keep_best_scored(self):
        chunks = [_chunk("c0", "31", score=0.9), _chunk("c1", "31", score=0.8), _chunk("c2", "92", score=0.7)]
        out = merge_reservoir(chunks, 50)
        assert [c.chunk_id for c in out] == ["c0", "c2"]

    def test_truncates_to_limit_preserving_rank_order(self):
        chunks = [_chunk(f"c{i}", str(10 + i), score=1.0 - i * 0.05) for i in range(6)]
        out = merge_reservoir(chunks, 4)
        assert [c.chunk_id for c in out] == ["c0", "c1", "c2", "c3"]

    def test_unparseable_identity_falls_back_to_chunk_id(self):
        """Chunks with no parseable legal unit are keyed by chunk_id — never merged."""
        bare = [
            RetrievedChunk(chunk_id=f"c{i}", score=1.0 - i * 0.1, text="no identity fields here at all")
            for i in range(3)
        ]
        out = merge_reservoir(bare, 50)
        assert [c.chunk_id for c in out] == ["c0", "c1", "c2"]


class TestSelectFromScores:
    def test_applies_threshold_and_top_k_from_scores(self):
        chunks = [_chunk(f"c{i}", str(10 + i)) for i in range(4)]
        evidence, core = select_from_scores(QUERY, chunks, {0: 0.9, 1: 0.8, 2: 0.2, 3: 0.1},
                                            threshold=0.5, top_k=6)
        assert core["kept"] == ["c0", "c1"]
        assert core["abstain"] is False
        assert core["keep_scores"]["c2"] == pytest.approx(0.2)
        assert [it["chunk_id"] for it in evidence["items"]] == ["c0", "c1"]

    def test_partial_score_maps_ignore_missing_indices(self):
        """A cached score map may miss candidates — the walk degrades, not crashes."""
        chunks = [_chunk(f"c{i}", str(10 + i)) for i in range(3)]
        evidence, core = select_from_scores(QUERY, chunks, {0: 0.9, 2: 0.7}, threshold=0.5, top_k=6)
        assert core["kept"] == ["c0", "c2"]

    def test_out_of_range_indices_are_dropped(self):
        chunks = [_chunk("c0", "55")]
        _evidence, core = select_from_scores(QUERY, chunks, {0: 0.9, 7: 0.99}, threshold=0.5, top_k=6)
        assert core["kept"] == ["c0"]
        assert "7" not in core["keep_scores"]

    def test_all_below_threshold_backfills_min_size(self):
        chunks = [_chunk(f"c{i}", str(10 + i)) for i in range(4)]
        evidence, core = select_from_scores(QUERY, chunks, {i: 0.1 for i in range(4)},
                                            threshold=0.5, top_k=6)
        assert core["abstain"] is True
        assert len(evidence["items"]) == 2

    def test_empty_probs_keep_min_size_from_nothing_is_empty(self):
        chunks = [_chunk("c0", "55"), _chunk("c1", "56")]
        evidence, core = select_from_scores(QUERY, chunks, {}, threshold=0.5, top_k=6)
        assert core["kept"] == []
        assert evidence["items"] == []


# --------------------------------------------------------------------------- #
# Retrieval stage gating
# --------------------------------------------------------------------------- #
class TestDecisionStageGating:
    def test_disabled_by_default(self):
        assert cfg.decision_path is False
        assert cfg.decision_shadow is False
        assert decision_enabled() is False
        assert _stage("decision_trace").is_enabled() is False

    def test_enabled_under_shadow(self, monkeypatch):
        monkeypatch.setattr(cfg, "decision_shadow", True, raising=False)
        assert decision_enabled() is True
        assert _stage("decision_trace").is_enabled() is True

    def test_enabled_under_served_path(self, monkeypatch):
        monkeypatch.setattr(cfg, "decision_path", True, raising=False)
        assert decision_enabled() is True
        assert _stage("decision_trace").is_enabled() is True

    def test_env_keys_reach_the_cfg_accessors(self, monkeypatch):
        """.env.example parity: the documented keys must actually gate.

        ``monkeypatch.setenv`` only reaches ``cfg`` when no instance
        attribute shadows the accessor (the autouse pin above sets one),
        so drop it first and let ``__getattr__`` resolve the environment.
        """
        monkeypatch.delattr(cfg, "decision_shadow", raising=False)
        monkeypatch.setenv("RAG_DECISION_SHADOW", "true")
        assert decision_enabled() is True

    def test_enrich_embeds_evidence_set_and_logs(self, monkeypatch):
        monkeypatch.setattr(
            "app.rag.retrieval.decision_selector.DecisionModelClient",
            lambda *a, **k: _StubClient({0: 0.9, 1: 0.8, 2: 0.2}),
        )
        stage = _stage("decision_trace")
        assert stage.output_key == "decision_trace"
        assert stage.default is None
        assert stage.isolate is True

        result = SearchResult(
            query=QUERY,
            query_type="general",
            chunks=[_chunk("c0", "55"), _chunk("c1", "56"), _chunk("c2", "57")],
            total=3,
            latency_ms=1,
            source="test",
        )
        trace = stage.enrich(QUERY, result)

        assert trace["source"] == "decision_model"
        assert trace["kept"] == ["c0", "c1"]
        assert [it["chunk_id"] for it in trace["evidence_set"]["items"]] == ["c0", "c1"]
        json.dumps(trace)  # RetrievalLogger must be able to persist it


# --------------------------------------------------------------------------- #
# Generation wiring (run_generation_pipeline)
# --------------------------------------------------------------------------- #
class TestGenerationWiring:
    @staticmethod
    def _payload(cid: str, section: str, score: float) -> dict:
        return {
            "chunk_id": cid,
            "score": score,
            "text": f"Section {section} penalty provisions apply to food business operators.",
            "section_number": section,
            "document_title": ACT,
            "document_type": "act",
            "authority": "FSSAI",
        }

    def test_flags_off_wire_shape_unchanged(self, monkeypatch):
        """Baseline: no decision_trace key, no evidence_set forwarded."""
        captured: dict = {}
        _spy_generate(monkeypatch, captured)
        chunks = [self._payload("c0", "55", 0.9), self._payload("c1", "56", 0.8)]

        out = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")

        assert "decision_trace" not in out
        assert captured.get("evidence_set") is None

    def test_decision_path_forwards_selection_and_trace(self, monkeypatch):
        """RAG_DECISION_PATH on: selection reaches generation, trace rides back."""
        monkeypatch.setattr(cfg, "decision_path", True, raising=False)
        # Isolate the standard grounded path (food/KG are covered elsewhere).
        monkeypatch.setattr(cfg, "food_answer_mode", False, raising=False)
        monkeypatch.setattr("app.rag.retrieval.decision_selector.DecisionModelClient",
                            lambda *a, **k: _StubClient({0: 0.9, 1: 0.8, 2: 0.15}))
        captured: dict = {}
        _spy_generate(monkeypatch, captured)
        chunks = [self._payload("c0", "55", 0.9), self._payload("c1", "56", 0.8),
                  self._payload("c2", "57", 0.7)]

        out = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")

        trace = out["decision_trace"]
        assert trace["source"] == "decision_model"
        assert trace["kept"] == ["c0", "c1"]
        assert trace["resolved_model"] == RESOLVED
        es = captured.get("evidence_set")
        assert es is not None
        assert [it["chunk_id"] for it in es["items"]] == ["c0", "c1"]

    def test_shadow_only_never_serves(self, monkeypatch):
        """RAG_DECISION_SHADOW on, path off: nothing served at generation."""
        monkeypatch.setattr(cfg, "decision_shadow", True, raising=False)
        monkeypatch.setattr("app.rag.retrieval.decision_selector.DecisionModelClient",
                            lambda *a, **k: _StubClient({0: 0.9, 1: 0.8, 2: 0.15}))
        captured: dict = {}
        _spy_generate(monkeypatch, captured)
        chunks = [self._payload("c0", "55", 0.9), self._payload("c1", "56", 0.8)]

        out = run_generation_pipeline(query="Section 55?", chunks=chunks, query_type="section_lookup")

        # Shadow gating happens in retrieval; with caller-provided chunks the
        # generation stage must not select either — evidence_set stays None.
        assert captured.get("evidence_set") is None
        assert "decision_trace" not in out
