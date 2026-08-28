"""Tests for the shared RRF scoring core (``app.rag.retrieval.rrf``).

Covers the ``reciprocal_rank_fuse`` function that eliminates the 5× RRF
formula duplication across HybridRetriever.retrieve (3 inline loops),
kg.hybrid.rrf_fuse_chunks, and evaluation.fusion.rrf_fuse_items.
"""

from __future__ import annotations

from app.rag.retrieval.rrf import DEFAULT_RRF_K, reciprocal_rank_fuse

# --------------------------------------------------------------------------- #
# Core scoring
# --------------------------------------------------------------------------- #


class TestReciprocalRankFuse:
    def test_single_list_ranks_first_highest(self):
        """Item at rank 0 scores 1/(0+1+k); at rank 1, 1/(1+1+k)."""
        items = ["a", "b", "c"]
        scores = reciprocal_rank_fuse([items], rrf_k=60.0)
        assert scores["a"] > scores["b"] > scores["c"]
        assert abs(scores["a"] - 1 / 61) < 1e-12
        assert abs(scores["b"] - 1 / 62) < 1e-12
        assert abs(scores["c"] - 1 / 63) < 1e-12

    def test_agreement_boost_when_item_in_multiple_lists(self):
        """Item appearing in both lists accumulates (agreement boost)."""
        dense = ["a", "b", "c"]
        sparse = ["b", "d"]
        scores = reciprocal_rank_fuse([dense, sparse], rrf_k=60.0)
        # b is at rank 1 in dense (1/62) and rank 0 in sparse (1/61)
        assert abs(scores["b"] - (1 / 62 + 1 / 61)) < 1e-12
        assert scores["b"] > scores["a"]  # b > a because of agreement boost

    def test_default_key_fn_uses_chunk_id(self):
        """When key_fn is None, the default extracts .chunk_id."""

        class FakeChunk:
            def __init__(self, cid):
                self.chunk_id = cid

        chunks = [FakeChunk("x"), FakeChunk("y")]
        scores = reciprocal_rank_fuse([chunks])
        assert "x" in scores and "y" in scores
        assert scores["x"] > scores["y"]

    def test_default_key_fn_falls_back_to_str(self):
        """Non-object items use str(item) as the key (fallback)."""
        items = ["alpha", "beta"]
        scores = reciprocal_rank_fuse([items])
        assert set(scores) == {"alpha", "beta"}

    def test_custom_key_fn(self):
        """Callers can supply their own key extraction."""

        class FakeItem:
            def __init__(self, id_val, score_val):
                self.id_val = id_val
                self.score = score_val

        items = [FakeItem("k1", 0.5), FakeItem("k2", 0.3)]
        scores = reciprocal_rank_fuse([items], key_fn=lambda x: x.id_val)
        assert set(scores) == {"k1", "k2"}

    def test_empty_lists_returns_empty_dict(self):
        assert reciprocal_rank_fuse([], rrf_k=60.0) == {}
        assert reciprocal_rank_fuse([[], []], rrf_k=60.0) == {}

    def test_skips_items_with_empty_key(self):
        """Items whose key_fn resolves to falsy are skipped."""

        class FakeChunk:
            def __init__(self, cid):
                self.chunk_id = cid

        chunks = [FakeChunk(""), FakeChunk("a"), FakeChunk("b")]
        scores = reciprocal_rank_fuse([chunks])
        assert "a" in scores and "b" in scores
        assert "" not in scores

    def test_rrf_k_affects_scores(self):
        """Different rrf_k values produce different scores."""
        scores_60 = reciprocal_rank_fuse(["a"], rrf_k=60.0)
        scores_20 = reciprocal_rank_fuse(["a"], rrf_k=20.0)
        assert abs(scores_60["a"] - 1 / 61) < 1e-12
        assert abs(scores_20["a"] - 1 / 21) < 1e-12

    def test_default_rrf_k_constant(self):
        """DEFAULT_RRF_K is 60 (standard Cormack et al., 2009)."""
        assert DEFAULT_RRF_K == 60.0

    def test_tuple_keys_for_evaluation_domain(self):
        """The function works with tuple keys (evaluation.fusion use-case)."""
        dense = [("chunk", "c1", "fssai", "16"), ("chunk", "c2", "fssai", "31")]
        kg = [("kg", "p1", "fssai", "16")]
        scores = reciprocal_rank_fuse([dense, kg], key_fn=lambda x: x)
        assert ("chunk", "c1", "fssai", "16") in scores
        assert ("kg", "p1", "fssai", "16") in scores
        # c1 (rank 0, dense only) and p1 (rank 0, kg only) tie at 1/61
        assert abs(scores[("chunk", "c1", "fssai", "16")] - 1 / 61) < 1e-12
        assert abs(scores[("kg", "p1", "fssai", "16")] - 1 / 61) < 1e-12
