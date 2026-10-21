"""WF-9 freeze tests — the re-baselined Experiment B under ruler v4.2."""

from __future__ import annotations

import json

import pytest

import evaluation.rebaseline_experiment_b as reb

EXPECTED_ARMS = {f"K={k}" for k in (1, 5, 10, 20, 30, 50, 75, 100)} | {"oracle"}


@pytest.fixture(scope="module")
def artifact():
    data = json.loads(reb.BASELINE.read_text(encoding="utf-8"))
    return data


def test_all_arms_present_and_complete():
    data = json.loads(reb.BASELINE.read_text(encoding="utf-8"))
    assert set(data["arms"]) == EXPECTED_ARMS
    for arm, row in data["arms"].items():
        for key in (
            "credit_binary_rate",
            "claim_binary_rate",
            "soft_v4_mean",
            "accuracy_axis_mean_0_3",
            "attribution_axis_mean",
            "composite_2axis_mean",
            "coverage_union_mean_band",
        ):
            assert key in row, f"{arm} missing {key}"
        assert row["v1_binary_rate"] != row["credit_binary_rate"] or row["v1_binary_rate"] == row["credit_binary_rate"]


def test_transport_errors_disclosed_per_arm():
    data = json.loads(reb.BASELINE.read_text(encoding="utf-8"))
    for arm, row in data["arms"].items():
        assert "transport_error_rate" in row and row["n_errored"] >= 0
        if arm.startswith("K="):
            # B precedent: ~25% of requests transport-errored; disclose, don't hide
            assert row["transport_error_rate"] > 0.10, arm
        else:
            assert row["transport_error_rate"] < 0.10, arm


def test_composite_is_a_reporting_index_with_per_axis_beside_it():
    data = json.loads(reb.BASELINE.read_text(encoding="utf-8"))
    policy = data["composite_policy"]
    assert "REPORTING INDEX ONLY" in policy["status"]
    assert (
        "evaluator_miss <= 10%" in policy["promotion_rule"]
        and "until then gates stay per-axis" in policy["promotion_rule"]
    )
    assert policy["weights_frozen"] == {"accuracy": 0.5, "attribution": 0.4, "presentation": None}
    for row in data["arms"].values():
        assert "composite_2axis_mean" in row and "accuracy_axis_mean_0_3" in row and "attribution_axis_mean" in row


def test_superseded_figures_point_to_the_new_baseline():
    data = json.loads(reb.BASELINE.read_text(encoding="utf-8"))
    figures = data["superseded_figures"]
    assert len(figures) >= 3
    for f in figures:
        assert "superseded_by" in f and "evaluator_v4_baseline.json" in f["superseded_by"]
    assert data["acceptance"]["A6_baseline_frozen"] is True


def test_rebuild_is_byte_identical(tmp_path):
    a = tmp_path / "one.json"
    b = tmp_path / "two.json"
    reb.build_baseline(a)
    reb.build_baseline(b)
    assert a.read_bytes() == b.read_bytes(), "the frozen baseline is not reproducible byte-for-byte"
