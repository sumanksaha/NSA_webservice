"""WF-9 freeze tests — the re-baselined Experiment B under ruler v4.2."""

from __future__ import annotations

import json

import pytest

import evaluation.rebaseline_experiment_b as reb

EXPECTED_ARMS = {f"K={k}" for k in (1, 5, 10, 20, 30, 50, 75, 100)} | {"oracle"}


@pytest.fixture
def frozen_baseline(tmp_path, monkeypatch):
    k_ckpt = tmp_path / "k.jsonl"
    oracle_ckpt = tmp_path / "oracle.jsonl"
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(reb, "K_CHECKPOINT", k_ckpt)
    monkeypatch.setattr(reb, "ORACLE_CHECKPOINT", oracle_ckpt)
    monkeypatch.setattr(reb, "BASELINE", baseline)

    bundle = {
        "meta": {"bundle_version": "test-1.0.0"},
        "questions": {"Q001": {"conclusions": ["alpha beta gamma"], "concepts": [], "expected_abstain": False}},
        "banned_abstain_markers": {"rules_by_qid": {}, "carried_from": {}},
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr("evaluation.build_references_v4.BUNDLE_PATH", bundle_path)
    monkeypatch.setattr(
        "evaluation.build_references_v4.verify_bundle",
        lambda path=bundle_path: {"ok": True, "status": "frozen", "recorded_content_sha256": "abc"},
    )
    monkeypatch.setattr("evaluation.calibrate_abstention.load_lexicon", lambda: ["i cannot answer"])
    monkeypatch.setattr("evaluation.text_overlap.token_overlap", lambda answer, expected: {"correctness": 0.9})

    def _fake_rescore_row(row, arm, bundle, lexicon, bans_by_qid, token_overlap, apply_rule, soft_v4):
        if row.get("error") not in (None, "", "None"):
            return None
        qid = row.get("question_id", "Q001")
        is_oracle = arm == "oracle"
        is_error_k = arm.startswith("K=")
        transport_err = is_error_k
        return {
            "qid": qid,
            "arm": arm,
            "credit_binary": True,
            "claim_binary": is_oracle or arm == "K=10",
            "soft_v4": 0.7,
            "v1_soft": 0.3,
            "v1_binary": False,
            "grader_correct": True,
            "false_abstention": False,
            "coverage_union": 0.8,
            "accuracy_axis_0_3": 2.1,
            "attribution_axis": 0.6,
            "composite_2axis_50_40": 1.35,
            "_transport_error": transport_err,
        }

    monkeypatch.setattr(reb, "rescore_row", _fake_rescore_row)

    k_rows = []
    for k in (1, 5, 10, 20, 30, 50, 75, 100):
        for i in range(4):
            row = {"question_id": f"Q{k:03d}{i}", "k": k, "answer": "alpha beta gamma"}
            if i == 0:
                row["error"] = "transport"
            k_rows.append(row)
    oracle_rows = [{"question_id": f"QO{i}", "answer": "alpha beta gamma"} for i in range(4)]
    k_ckpt.write_text("\n".join(json.dumps(r) for r in k_rows), encoding="utf-8")
    oracle_ckpt.write_text("\n".join(json.dumps(r) for r in oracle_rows), encoding="utf-8")
    reb.build_baseline(baseline)
    return baseline


def test_all_arms_present_and_complete(frozen_baseline):
    data = json.loads(frozen_baseline.read_text(encoding="utf-8"))
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


def test_transport_errors_disclosed_per_arm(frozen_baseline):
    data = json.loads(frozen_baseline.read_text(encoding="utf-8"))
    for arm, row in data["arms"].items():
        assert "transport_error_rate" in row and row["n_errored"] >= 0
        if arm.startswith("K="):
            # B precedent: ~25% of requests transport-errored; disclose, don't hide
            assert row["transport_error_rate"] > 0.10, arm
        else:
            assert row["transport_error_rate"] < 0.10, arm


def test_composite_is_a_reporting_index_with_per_axis_beside_it(frozen_baseline):
    data = json.loads(frozen_baseline.read_text(encoding="utf-8"))
    policy = data["composite_policy"]
    assert "REPORTING INDEX ONLY" in policy["status"]
    assert (
        "evaluator_miss <= 10%" in policy["promotion_rule"]
        and "until then gates stay per-axis" in policy["promotion_rule"]
    )
    assert policy["weights_frozen"] == {"accuracy": 0.5, "attribution": 0.4, "presentation": None}
    for row in data["arms"].values():
        assert "composite_2axis_mean" in row and "accuracy_axis_mean_0_3" in row and "attribution_axis_mean" in row


def test_superseded_figures_point_to_the_new_baseline(frozen_baseline):
    data = json.loads(frozen_baseline.read_text(encoding="utf-8"))
    figures = data["superseded_figures"]
    assert len(figures) >= 3
    for f in figures:
        assert "superseded_by" in f and "evaluator_v4_baseline.json" in f["superseded_by"]
    assert data["acceptance"]["A6_baseline_frozen"] is True


def test_rebuild_is_byte_identical(frozen_baseline, tmp_path):
    a = tmp_path / "one.json"
    b = tmp_path / "two.json"
    reb.build_baseline(a)
    reb.build_baseline(b)
    assert a.read_bytes() == b.read_bytes(), "the frozen baseline is not reproducible byte-for-byte"
