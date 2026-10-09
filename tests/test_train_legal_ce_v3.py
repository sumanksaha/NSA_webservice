"""Tests for evaluation/train_legal_ce_v3.py (offline, no torch)."""

from evaluation.train_legal_ce_v3 import (
    V3_BATCH_SIZE,
    V3_EPOCHS,
    V3_LR,
    V3_MAX_LEN,
    apply_pool,
    downsample_t3,
)


def _pairs(n1, n2, n3):
    return [{"tier": 1}] * n1 + [{"tier": 2}] * n2 + [{"tier": 3}] * n3


def test_t3_cap_limits_mass():
    out, info = downsample_t3(_pairs(100, 200, 900), ratio=2.0)
    assert info["t3_after"] == 600
    assert len(out) == 900


def test_t3_no_cap_when_small():
    out, info = downsample_t3(_pairs(100, 200, 100), ratio=2.0)
    assert info["t3_after"] == 100
    assert len(out) == 400


def test_t3_deterministic():
    a, _ = downsample_t3(_pairs(50, 50, 500), ratio=1.0)
    b, _ = downsample_t3(_pairs(50, 50, 500), ratio=1.0)
    assert a == b


def test_v3_lighter_than_v2():
    assert V3_EPOCHS == 2
    assert V3_BATCH_SIZE <= 8
    assert V3_MAX_LEN <= 192
    assert V3_LR <= 1e-5


# ---------------------------------------------------------------- pool (--use-pool)

_POOL = {
    "weights": {
        "priority_provision": 3.0,
        "priority_promotable": 2.0,
        "guard": 1.0,
        "untrainable": 0.0,
    },
    "qids": {
        "priority_provision": ["Q005", "Q021"],
        "priority_promotable": ["Q038"],
        "guard": ["Q001"],
        "untrainable": ["Q007", "Q010"],
    },
}


def _q(qid, tier=2):
    return {"question_id": qid, "tier": tier}


def test_pool_drops_untrainable():
    pairs = [_q("Q007"), _q("Q010"), _q("Q001")]
    out, info = apply_pool(pairs, _POOL, scale=1.0)
    assert [p["question_id"] for p in out] == ["Q001"]
    assert info["dropped"] == 2
    assert info["out"] == 1


def test_pool_oversamples_priority_buckets():
    pairs = [_q("Q005"), _q("Q038"), _q("Q001")]
    out, info = apply_pool(pairs, _POOL, scale=1.0)
    ids = [p["question_id"] for p in out]
    assert ids.count("Q005") == 3  # priority_provision 3.0x
    assert ids.count("Q038") == 2  # priority_promotable 2.0x
    assert ids.count("Q001") == 1  # guard 1.0x
    assert info["bucket_pairs"] == {"guard": 1, "priority_provision": 3, "priority_promotable": 2}


def test_pool_scale_multiplies_weights():
    pairs = [_q("Q005"), _q("Q038"), _q("Q001")]
    out, _ = apply_pool(pairs, _POOL, scale=2.0)
    ids = [p["question_id"] for p in out]
    assert ids.count("Q005") == 6
    assert ids.count("Q038") == 4
    assert ids.count("Q001") == 2


def test_pool_scale_zero_drops_everything():
    out, info = apply_pool([_q("Q005")], _POOL, scale=0.0)
    assert out == []
    assert info["out"] == 0


def test_pool_unlisted_qid_kept_once():
    """A pool covering a subset of qids must not silently delete the rest."""
    out, info = apply_pool([_q("Q999")], _POOL, scale=1.0)
    assert len(out) == 1
    assert info["bucket_pairs"] == {"unlisted": 1}


def test_pool_is_deterministic_and_preserves_order():
    pairs = [_q("Q005"), _q("Q038"), _q("Q001"), _q("Q007")]
    a, ia = apply_pool(pairs, _POOL, scale=1.0)
    b, ib = apply_pool(pairs, _POOL, scale=1.0)
    assert a == b
    assert ia == ib
    # input order preserved: repeated copies stay adjacent
    assert [p["question_id"] for p in a] == ["Q005"] * 3 + ["Q038"] * 2 + ["Q001"]


def test_pool_empty_pool_is_noop():
    pairs = [_q("Q007"), _q("Q001")]
    out, info = apply_pool(pairs, {}, scale=1.0)
    assert out == pairs
    assert info["applied"] is False
    assert info["dropped"] == 0
