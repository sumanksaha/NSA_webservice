"""Tests for the section/act-scoped structural retrieval arm.

Covers three seams:

1. :mod:`app.rag.retrieval.section_scope` — identifier metadata to Qdrant
   payload filter, including the "nothing detected -> no arm" case that keeps
   the arm from guessing.
2. ``DenseRetriever._build_filter`` — the pass-through for already-shaped
   filters (an OR over scoping keys cannot be expressed by a flat dict).
3. ``HybridRetriever`` — the arm is purely additive in RRF fusion and is off
   by default.

No network and no LLM: retrievers are test doubles.
"""

from __future__ import annotations

import pytest

from app.rag.retrieval.dense_retriever import DenseRetriever
from app.rag.retrieval.hybrid_retriever import HybridRetriever
from app.rag.retrieval.result import RetrievedChunk, SearchResult
from app.rag.retrieval.rrf import reciprocal_rank_fuse
from app.rag.retrieval.section_scope import (
    section_scope_filter,
    section_scope_meta,
)


def _chunk(cid: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        score=score,
        text=f"text {cid}",
        section_number=cid.split("-")[-1],
    )


# --------------------------------------------------------------------------- #
# 1. section_scope_filter
# --------------------------------------------------------------------------- #
class TestSectionScopeFilter:
    def test_nothing_detected_returns_none(self):
        """No Act and no section -> no arm. Guessing is what regresses."""
        assert section_scope_filter(None, None) is None

    def test_act_only_scopes_by_act(self):
        built = section_scope_filter("Indian Contract Act, 1872", None)
        assert built is not None
        # act scoping is an OR over the identity fields
        assert built["should"] == [
            {"key": "act_name", "match": {"value": "Indian Contract Act, 1872"}},
            {"key": "document_title", "match": {"value": "Indian Contract Act, 1872"}},
        ]
        # no section pin when none was detected
        assert "must" not in built

    def test_act_and_section_pins_both(self):
        built = section_scope_filter("Air (Prevention and Control of Pollution) Act, 1981", "8")
        assert built is not None
        # section pin in MUST (an AND); act identity ORs live in SHOULD
        assert built["must"] == [{"key": "section_number", "match": {"value": "8"}}]
        assert built["should"] == [
            {"key": "act_name", "match": {"value": "Air (Prevention and Control of Pollution) Act, 1981"}},
            {"key": "document_title", "match": {"value": "Air (Prevention and Control of Pollution) Act, 1981"}},
        ]

    def test_should_sits_at_the_top_level(self):
        """Nesting the OR inside ``must`` is rejected by qdrant-client's
        model — an item of ``must`` must be a FieldCondition/Filter, and
        ``min_should`` expects a MinShould model in the installed version."""
        built = section_scope_filter("Companies Act, 2013", "133")
        assert "should" in built
        assert "min_should" not in built
        assert all("should" not in item for item in built["must"])

    def test_section_without_act_filters_section_only(self):
        built = section_scope_filter(None, "31(2)")
        assert built == {"must": [{"key": "section_number", "match": {"value": "31(2)"}}]}


class TestSectionScopeMeta:
    def test_act_and_section_query_is_act_section_mode(self):
        meta = section_scope_meta(
            "Under the Indian Contract Act, 1872 section 73 what is the remedy for breach",
        )
        assert meta is not None
        assert meta["mode"] == "act_section"
        assert meta["act"] == "Indian Contract Act, 1872"
        assert meta["section"] == "73"

    def test_act_only_query_is_act_only_mode(self):
        meta = section_scope_meta("What are the penalty provisions under the Companies Act, 2013")
        assert meta is not None
        assert meta["mode"] == "act_only"
        assert meta["section"] is None

    def test_plain_query_fires_no_arm(self):
        meta = section_scope_meta("Which licence do cloud kitchens need?")
        assert meta is None


# --------------------------------------------------------------------------- #
# 2. DenseRetriever._build_filter pass-through
# --------------------------------------------------------------------------- #
class TestBuildFilter:
    def test_flat_dict_still_becomes_a_must_clause(self):
        """The existing flat contract is unchanged."""
        built = DenseRetriever._build_filter({"authority": "FSSAI"})
        assert built == {"must": [{"key": "authority", "match": {"value": "FSSAI"}}]}

    def test_list_value_expands_to_multiple_matches(self):
        built = DenseRetriever._build_filter({"document_id": ["a", "b"]})
        assert built == {
            "must": [
                {"key": "document_id", "match": {"value": "a"}},
                {"key": "document_id", "match": {"value": "b"}},
            ]
        }

    def test_empty_filter_stays_empty(self):
        assert DenseRetriever._build_filter({}) == {}

    def test_shaped_filter_is_passed_through_unchanged(self):
        """A filter carrying Qdrant boolean clauses is NOT flattened — a flat
        dict cannot express the act scoping OR."""
        shaped = {
            "must": [{"key": "section_number", "match": {"value": "8"}}],
            "should": [
                {"key": "act_name", "match": {"value": "A"}},
                {"key": "document_title", "match": {"value": "A"}},
            ],
        }
        assert DenseRetriever._build_filter(shaped) == shaped


# --------------------------------------------------------------------------- #
# 3. HybridRetriever — additive arm, off by default
# --------------------------------------------------------------------------- #
class _RecordingDense:
    """Records the filters it was called with, returns canned results."""

    def __init__(self, results: list[RetrievedChunk] | None = None):
        self.calls: list[dict] = []
        self._results = results or []

    def search(self, query, top_k=10, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return SearchResult(query=query, query_type="", chunks=self._results, total=len(self._results), source="dense")

    def embed_query(self, q):
        return [0.1]


class _RecordingSparse:
    def __init__(self, results: list[RetrievedChunk] | None = None):
        self.calls: list[dict] = []
        self._results = results or []

    def retrieve(self, query, top_k=10, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return SearchResult(query=query, query_type="", chunks=self._results, total=len(self._results), source="sparse")

    def embed_query(self, q):
        return {"indices": [1], "values": [1.0]}


def _hybrid(dense_results=None, sparse_results=None, rrf_k=60.0) -> HybridRetriever:
    return HybridRetriever(
        dense=_RecordingDense(dense_results),
        sparse=_RecordingSparse(sparse_results),
        rrf_k=rrf_k,
    )


class TestSectionArmIsAdditive:
    def test_arm_does_not_run_by_default(self, monkeypatch):
        """RAG_SECTION_ARM defaults False: the default path is unchanged."""
        from app.shared.config import cfg

        if getattr(cfg, "section_arm", False):
            monkeypatch.delenv("RAG_SECTION_ARM", raising=False)
            cfg.section_arm = False
        hybrid = _hybrid(dense_results=[_chunk("a-1")], sparse_results=[_chunk("b-2")])
        hybrid.retrieve("Under the Indian Contract Act, 1872 section 73 what remedy applies", top_k=5)
        # only the two base arms fired — no filtered dense call
        assert len(hybrid.dense.calls) == 1

    def test_enabled_arm_runs_a_second_filtered_dense_call(self, monkeypatch):
        from app.shared.config import cfg

        monkeypatch.setattr(cfg, "section_arm", True, raising=False)
        hybrid = _hybrid(dense_results=[_chunk("a-1")], sparse_results=[_chunk("b-2")])
        hybrid.retrieve("Under the Indian Contract Act, 1872 section 73 what remedy applies", top_k=5)
        assert len(hybrid.dense.calls) == 2
        scope_call = hybrid.dense.calls[1]
        assert scope_call["query"] == hybrid.dense.calls[0]["query"]
        # the structural arm pins the section in MUST
        assert scope_call["filters"]["must"][0]["key"] == "section_number"

    def test_no_arm_when_the_query_names_no_instrument(self, monkeypatch):
        from app.shared.config import cfg

        monkeypatch.setattr(cfg, "section_arm", True, raising=False)
        hybrid = _hybrid(dense_results=[_chunk("a-1")])
        hybrid.retrieve("Which licence do cloud kitchens need", top_k=5)
        assert len(hybrid.dense.calls) == 1

    def test_arm_only_adds_chunks(self, monkeypatch):
        """The arm must never displace gold the base arms already retrieved."""
        from app.shared.config import cfg

        monkeypatch.setattr(cfg, "section_arm", True, raising=False)
        base_gold = _chunk("gold-1", 0.9)
        # the base arms find gold; the scope arm contributes a different chunk
        hybrid = HybridRetriever(
            dense=_RankAwareDense(base_gold),
            sparse=_RankAwareSparse(),
            rrf_k=60.0,
        )
        result = hybrid.retrieve(
            "Under the Indian Contract Act, 1872 section 73 what remedy applies",
            top_k=5,
        )
        ids = [c.chunk_id for c in result.chunks]
        assert "gold-1" in ids
        assert "scope-1" in ids

    def test_fusion_is_non_lossy_for_chunks_at_the_cut_boundary(self, monkeypatch):
        """The measured regression this guards against.

        An additive arm is additive in *membership* but not in *rank*: giving a
        third set of RRF points re-orders the pool, and a marginal gold chunk
        sitting at ``top_k`` can be pushed below the cut. Measured in Phase 2b:
        enabling the arm lost gold for one qid for exactly this reason. The
        fused pool must therefore be a strict superset of what the base arms
        already surfaced.
        """
        from app.shared.config import cfg

        monkeypatch.setattr(cfg, "section_arm", True, raising=False)
        # The dense arm surfaces gold at the very last position of the cut;
        # the scope arm contributes many chunks that out-rank it.
        boundary_gold = _chunk("gold-9", 0.9)
        filler = [_chunk(f"scope-{i}", 0.9) for i in range(1, 13)]

        hybrid = HybridRetriever(
            dense=_FixedDense([*[_chunk(f"d-{i}") for i in range(9)], boundary_gold]),
            sparse=_FixedSparse([]),
            rrf_k=60.0,
        )

        class _ScopeDense(_FixedDense):
            def search(self, query, top_k=10, filters=None, **kw):
                if filters is not None:
                    return SearchResult(
                        query=query, query_type="", chunks=filler, total=len(filler), source="dense"
                    )
                return super().search(query, top_k=top_k, filters=filters, **kw)

        hybrid.dense = _ScopeDense([*[_chunk(f"d-{i}") for i in range(9)], boundary_gold])
        result = hybrid.retrieve(
            "Under the Indian Contract Act, 1872 section 73 what remedy applies",
            top_k=10,
        )
        ids = [c.chunk_id for c in result.chunks]
        assert "gold-9" in ids, "boundary gold was displaced by the structural arm"
        assert "scope-1" in ids

    def test_default_path_is_unchanged(self, monkeypatch):
        """Without the arm the pool is exactly the fusion top_k — the
        non-lossy protection must not fire, so the default path is unchanged."""
        from app.shared.config import cfg

        monkeypatch.setattr(cfg, "section_arm", False, raising=False)
        hybrid = HybridRetriever(
            dense=_FixedDense([_chunk(f"d-{i}") for i in range(20)]),
            sparse=_FixedSparse([_chunk("s-1")]),
            rrf_k=60.0,
        )
        result = hybrid.retrieve("generic question", top_k=5)
        assert len(result.chunks) == 5
        # the scope arm never ran, so no extra protected chunks were appended
        assert all(c.chunk_id.startswith(("d-", "s-")) for c in result.chunks)

    def test_act_only_arm_gets_wider_fetch_than_section_arm(self, monkeypatch):
        """Act-only mode has no section pin, so it needs the extra width to
        let the Act's sections compete for the governing provision."""
        from app.shared.config import cfg

        monkeypatch.setattr(cfg, "section_arm", True, raising=False)
        hybrid = _hybrid(dense_results=[_chunk("a-1")])
        hybrid.retrieve("What are the penalty provisions under the Companies Act, 2013", top_k=10)
        scope_call = hybrid.dense.calls[1]
        assert scope_call["top_k"] == 20  # max(top_k * 2, 20)


class _FixedDense(_RecordingDense):
    def search(self, query, top_k=10, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return SearchResult(
            query=query, query_type="", chunks=list(self._results), total=len(self._results), source="dense"
        )


class _FixedSparse(_RecordingSparse):
    def retrieve(self, query, top_k=10, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return SearchResult(
            query=query, query_type="", chunks=list(self._results), total=len(self._results), source="sparse"
        )


class _RankAwareDense(_RecordingDense):
    """Base arms return gold; the filtered scope call returns a scope chunk."""

    def __init__(self, gold):
        super().__init__()
        self._gold = gold

    def search(self, query, top_k=10, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        if filters is None:
            return SearchResult(query=query, query_type="", chunks=[self._gold], total=1, source="dense")
        return SearchResult(query=query, query_type="", chunks=[_chunk("scope-1")], total=1, source="dense")


class _RankAwareSparse(_RecordingSparse):
    def retrieve(self, query, top_k=10, filters=None, **kwargs):
        self.calls.append({"query": query, "top_k": top_k, "filters": filters})
        return SearchResult(query=query, query_type="", chunks=[], total=0, source="sparse")


def test_rrf_fusion_is_rank_based():
    """Sanity: RRF is what makes a rank-1 hit in one arm survive rank-1 in another."""
    scores = reciprocal_rank_fuse([[_chunk("x-1"), _chunk("y-2")], [_chunk("z-1")]])
    assert scores["x-1"] == scores["z-1"] == 1.0 / (1 + 60)
    assert scores["y-2"] == 1.0 / (2 + 60)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
