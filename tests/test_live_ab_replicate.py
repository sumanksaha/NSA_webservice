"""Live A/B harness contract tests (0 LLM — no calls in tests)."""

from __future__ import annotations

import json

import pytest

import evaluation.live_ab_replicate as ab
from evaluation.ruler import validate_header


def _synthetic_rows():
    rows = []
    for i in range(12):
        rows.append({"qid": f"QCAL{i:02d}", "split": "calibration"})
    for i in range(8):
        rows.append({"qid": f"QHOLD{i:02d}", "split": "holdout"})
    return rows


@pytest.fixture
def fixed_cohorts(monkeypatch):
    rows = _synthetic_rows()

    def _build_rows():
        return rows

    monkeypatch.setattr("evaluation.evaluator_v4.build_rows", _build_rows)
    return ab.pick_qids()


def test_cohort_selection_is_deterministic_and_sized(fixed_cohorts, monkeypatch):
    rows = _synthetic_rows()
    monkeypatch.setattr("evaluation.evaluator_v4.build_rows", lambda: rows)
    a = ab.pick_qids()
    b = ab.pick_qids()
    assert a == b, "cohort selection must be deterministic (no re-picking)"
    assert len(a["calibration"]) == 12 and len(a["holdout"]) == 8
    assert not (set(a["calibration"]) & set(a["holdout"]))


def test_refusal_without_explicit_budget(monkeypatch, tmp_path, fixed_cohorts):
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("MAX_LLM_CALLS", "0")
    rc = ab.run_live(fixed_cohorts)
    assert rc == 2, "a real run must refuse without the explicit kill-switch opt-in"


def test_refusal_on_explicit_opt_in_object(fixed_cohorts):
    # The CLI captures (live, budget) BEFORE any import (env clobbering via
    # load_dotenv(override=True) must not matter); passing the tuple directly
    # must produce the same refusal contract.
    assert ab.run_live(fixed_cohorts, opt_in=(False, 10**9)) == 2
    assert ab.run_live(fixed_cohorts, opt_in=(True, 10)) == 2, "budget below the cohort size must refuse"


def test_empty_tasks_run_is_call_free(tmp_path, monkeypatch, fixed_cohorts):
    # 60/60 checkpointed and an explicit opt-in -> zero tasks -> zero calls,
    # resumability intact (never re-runs a checkpointed replicate).
    ckpt = tmp_path / "live_ab_replicates.jsonl"
    all_qids = fixed_cohorts["calibration"] + fixed_cohorts["holdout"]
    with ckpt.open("w", encoding="utf-8") as f:
        for rep in ab.REPLICATES:
            for qid in all_qids:
                f.write(json.dumps({"replicate": rep, "question_id": qid, "answer": "x"}) + "\n")
    monkeypatch.setattr(ab, "AB_CKPT", ckpt)
    assert ab.run_live(fixed_cohorts, opt_in=(True, 10**9)) == 0


def test_scored_artifact_carries_gate_noise_and_header(tmp_path, monkeypatch, fixed_cohorts):
    monkeypatch.setattr(ab, "AB_CKPT", tmp_path / "live_ab_replicates.jsonl")
    monkeypatch.setattr(ab, "RESULTS", tmp_path / "live_ab_v4_2.json")
    monkeypatch.setattr(
        ab,
        "baseline_reference",
        lambda qids: {"n_scored": len(qids), "claim_rate": 0.25, "credit_rate": 0.5, "soft_mean": 0.5},
    )

    bundle = {
        "meta": {"bundle_version": "test-1.0.0"},
        "questions": {
            qid: {"conclusions": ["answer mentions alpha beta gamma delta"], "concepts": [], "expected_abstain": False}
            for qid in (fixed_cohorts["calibration"] + fixed_cohorts["holdout"])
        },
        "banned_abstain_markers": {"rules_by_qid": {}},
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr("evaluation.build_references_v4.BUNDLE_PATH", bundle_path)
    monkeypatch.setattr("evaluation.evaluator_v4.BUNDLE_PATH", bundle_path)
    monkeypatch.setattr(
        "evaluation.build_references_v4.verify_bundle", lambda path=bundle_path: {"ok": True, "status": "frozen"}
    )
    monkeypatch.setattr("evaluation.calibrate_abstention.load_lexicon", lambda: ["i cannot answer"])
    monkeypatch.setattr("evaluation.measure_component_rule_identity.load_payload_index", lambda: {})
    monkeypatch.setattr("evaluation.measure_component_rule_identity.evidence_texts_for", lambda ids, idx: {})
    monkeypatch.setattr("evaluation.benchmark.load_questions", lambda: [])

    class _Q:
        def __init__(self, qid):
            self.question_id = qid
            self.question = "q"
            self.insufficient_evidence = False

    questions = [_Q(qid) for qid in (fixed_cohorts["calibration"] + fixed_cohorts["holdout"])]
    monkeypatch.setattr("evaluation.benchmark.load_questions", lambda: questions)
    monkeypatch.setattr(
        "evaluation.grading.grade_answer",
        lambda *args, **kwargs: {"provision_correct": True, "citation_correct": True, "hallucination_detected": False},
    )
    monkeypatch.setattr(
        "evaluation.text_overlap.token_overlap",
        lambda answer, expected: {"correctness": 0.9 if "alpha beta" in (expected or "") else 0.0},
    )

    rows = []
    for rep in ab.REPLICATES:
        for qid in fixed_cohorts["calibration"] + fixed_cohorts["holdout"]:
            rows.append({
                "replicate": rep,
                "question_id": qid,
                "answer": "alpha beta gamma delta answer",
                "cited_chunk_ids": [],
                "context_ids": [],
            })
    ab.AB_CKPT.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    ab.score(fixed_cohorts)

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
