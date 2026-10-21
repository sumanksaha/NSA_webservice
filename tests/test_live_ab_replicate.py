"""Live A/B harness contract tests (0 LLM — no calls in tests)."""

from __future__ import annotations

import json

import pytest

import evaluation.live_ab_replicate as ab
from evaluation.ruler import validate_header


def test_cohort_selection_is_deterministic_and_sized():
    a = ab.pick_qids()
    b = ab.pick_qids()
    assert a == b, "cohort selection must be deterministic (no re-picking)"
    assert len(a["calibration"]) == 12 and len(a["holdout"]) == 8
    assert not (set(a["calibration"]) & set(a["holdout"]))


def test_refusal_without_explicit_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("MAX_LLM_CALLS", "0")
    rc = ab.run_live(ab.pick_qids())
    assert rc == 2, "a real run must refuse without the explicit kill-switch opt-in"


def test_refusal_on_explicit_opt_in_object():
    # The CLI captures (live, budget) BEFORE any import (env clobbering via
    # load_dotenv(override=True) must not matter); passing the tuple directly
    # must produce the same refusal contract.
    assert ab.run_live(ab.pick_qids(), opt_in=(False, 10**9)) == 2
    assert ab.run_live(ab.pick_qids(), opt_in=(True, 10)) == 2, "budget below the cohort size must refuse"


def test_empty_tasks_run_is_call_free():
    # 60/60 checkpointed and an explicit opt-in -> zero tasks -> zero calls,
    # resumability intact (never re-runs a checkpointed replicate).
    assert ab.run_live(ab.pick_qids(), opt_in=(True, 10**9)) == 0


def test_scored_artifact_carries_gate_noise_and_header():
    data = json.loads(ab.RESULTS.read_text(encoding="utf-8"))
    validate_header(data["publication_header"])
    gate = data["paired_gate"]
    assert gate["verdict"] in {"IMPROVED", "NOT_IMPROVED", "TIED"}
    assert gate["verdict"] is not None
    assert len(gate["live_claim_cal_by_replicate"]) == 3  # boot rule 6: 3 replicates
    assert gate["rule"].startswith("boot rule 6")
    noise = data["noise"]
    assert noise["claim_cal_min_max"] is not None
    for rep in ab.REPLICATES:
        assert rep in data["per_replicate"] and "n_errored" in data["per_replicate"][rep]
