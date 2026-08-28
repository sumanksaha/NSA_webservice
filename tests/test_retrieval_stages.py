"""Unit tests for the post-retrieval enrichment stage registry (stages.py).

The registry replaces the 3 inline ``if cfg.X and result.chunks:`` blocks in
``run_retrieval_pipeline`` with a data-driven ordered list.  These tests use
*fake* stages (not the real enrichers) so they stay offline and fast.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.retrieval.result import SearchResult
from app.rag.retrieval.stages import (
    POST_RETRIEVAL_STAGES,
    RetrievalStage,
    apply_stages,
)


def _result(chunks: list | None = None):
    """Build a minimal SearchResult for stage tests."""
    return SearchResult(
        query="test query",
        query_type="general",
        chunks=chunks or [],
        total=len(chunks) if chunks else 0,
        latency_ms=0,
        source="test",
    )


QUERY = "what is Section 55?"


class TestStageDefinition:
    def test_stages_are_ordered_list_of_retrieval_stage(self):
        assert isinstance(POST_RETRIEVAL_STAGES, list)
        assert len(POST_RETRIEVAL_STAGES) == 3
        assert all(isinstance(s, RetrievalStage) for s in POST_RETRIEVAL_STAGES)

    def test_stage_names_are_deterministic(self):
        names = [s.name for s in POST_RETRIEVAL_STAGES]
        assert names == ["legal_identity", "reference_expansion", "evidence_selector"]

    def test_output_keys_match_original_contract(self):
        """The registry output keys must match the original return-dict keys."""
        keys = [s.output_key for s in POST_RETRIEVAL_STAGES]
        assert keys == ["legal_identities", "expanded_candidates", "evidence_set"]


class TestApplyStagesNoChunks:
    def test_empty_chunks_returns_defaults(self):
        """With no chunks, all stages are skipped and defaults are returned."""
        result = _result(chunks=[])
        out = apply_stages(QUERY, result, stages=POST_RETRIEVAL_STAGES)
        assert out == {
            "legal_identities": [],
            "expanded_candidates": [],
            "evidence_set": None,
        }


class TestApplyStagesDisabled:
    def test_all_disabled_returns_defaults(self):
        result = _result(chunks=[SimpleNamespace()])  # truthy chunks to enter loop
        fake_stages = [
            RetrievalStage(
                "a", is_enabled=lambda: False, enrich=lambda q, r: "X", output_key="a_val", default="a_default"
            ),
            RetrievalStage(
                "b", is_enabled=lambda: False, enrich=lambda q, r: "Y", output_key="b_val", default="b_default"
            ),
        ]
        out = apply_stages(QUERY, result, stages=fake_stages)
        assert out == {"a_val": "a_default", "b_val": "b_default"}


class TestApplyStagesEnabled:
    def test_enabled_stage_overrides_default(self):
        result = _result(chunks=[SimpleNamespace()])
        fake_stages = [
            RetrievalStage(
                "a", is_enabled=lambda: True, enrich=lambda q, r: "X", output_key="a_val", default="a_default"
            ),
        ]
        out = apply_stages(QUERY, result, stages=fake_stages)
        assert out["a_val"] == "X"

    def test_partial_enable(self):
        result = _result(chunks=[SimpleNamespace()])
        fake_stages = [
            RetrievalStage(
                "a", is_enabled=lambda: True, enrich=lambda q, r: "X", output_key="a_val", default="a_default"
            ),
            RetrievalStage(
                "b", is_enabled=lambda: False, enrich=lambda q, r: "Y", output_key="b_val", default="b_default"
            ),
            RetrievalStage(
                "c", is_enabled=lambda: True, enrich=lambda q, r: "Z", output_key="c_val", default="c_default"
            ),
        ]
        out = apply_stages(QUERY, result, stages=fake_stages)
        assert out == {"a_val": "X", "b_val": "b_default", "c_val": "Z"}

    def test_stages_run_in_order(self):
        """Enrich functions are called in registry order."""
        result = _result(chunks=[SimpleNamespace()])
        call_log = []

        def make_enrich(label):
            def _enrich(q, r):
                call_log.append(label)
                return label

            return _enrich

        fake_stages = [
            RetrievalStage("a", is_enabled=lambda: True, enrich=make_enrich("first"), output_key="o1"),
            RetrievalStage("b", is_enabled=lambda: True, enrich=make_enrich("second"), output_key="o2"),
            RetrievalStage("c", is_enabled=lambda: True, enrich=make_enrich("third"), output_key="o3"),
        ]
        apply_stages(QUERY, result, stages=fake_stages)
        assert call_log == ["first", "second", "third"]


class TestApplyStagesErrorIsolation:
    def test_isolated_stage_swallows_error_returns_default(self):
        result = _result(chunks=[SimpleNamespace()])
        fake_stages = [
            RetrievalStage(
                "good", is_enabled=lambda: True, enrich=lambda q, r: "ok", output_key="good_val", default="ok_default"
            ),
            RetrievalStage(
                "bad",
                is_enabled=lambda: True,
                enrich=lambda q, r: (_ for _ in ()).throw(RuntimeError("boom")),
                output_key="bad_val",
                default="bad_default",
                isolate=True,
            ),
            RetrievalStage(
                "good2",
                is_enabled=lambda: True,
                enrich=lambda q, r: "ok2",
                output_key="good2_val",
                default="ok2_default",
            ),
        ]
        out = apply_stages(QUERY, result, stages=fake_stages)
        assert out["good_val"] == "ok"
        assert out["bad_val"] == "bad_default"  # error swallowed, default kept
        assert out["good2_val"] == "ok2"  # subsequent stages still run

    def test_non_isolated_stage_reraises(self):
        result = _result(chunks=[SimpleNamespace()])
        fake_stages = [
            RetrievalStage(
                "good", is_enabled=lambda: True, enrich=lambda q, r: "ok", output_key="good_val", default="ok_default"
            ),
            RetrievalStage(
                "bad",
                is_enabled=lambda: True,
                enrich=lambda q, r: (_ for _ in ()).throw(ValueError("hard fail")),
                output_key="bad_val",
                default="bad_default",
                isolate=False,
            ),
        ]
        with pytest.raises(ValueError, match="hard fail"):
            apply_stages(QUERY, result, stages=fake_stages)
