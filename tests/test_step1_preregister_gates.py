"""Offline tests for Step 1 pre-registered gates (no network, no LLM)."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.step1_preregister_gates import (
    BUDGET_CAP,
    EVALUATORS,
    GATE_TABLE,
    SAFETY_PROPERTIES,
    SCORER,
    STEP1_LABELS,
    assign_qids,
    build_gates,
    evaluate_candidate,
    evaluate_evidence_missing,
    evaluate_model_wrong,
    evaluate_reference_narrow,
    main,
    publish,
    quote_in_evidence,
    validate_prereg,
)
from evaluation.step0_label_residual import STEP0_ENUM, load_residual_qids


def _complete_labels() -> dict[str, str]:
    residual = load_residual_qids()
    return {qid: STEP1_LABELS[i % 3] for i, qid in enumerate(sorted(residual))}


def test_labels_match_step0_enum():
    assert STEP1_LABELS == STEP0_ENUM
    assert set(GATE_TABLE) == set(STEP0_ENUM)


def test_gate_table_matches_plan_strings():
    # Spot-check the plan sec 5.2 Step 1 contract (frozen — must not drift).
    em = GATE_TABLE["evidence_missing"]
    assert "Re-retrieve those question ids only" in em["intervention"]
    assert "binary correctness on that id rises" in em["keep_if"]
    assert "Do not loop retrieval" in em["reject_if"]
    assert em["generations"] == 1 and em["re_retrieve"] is True

    mw = GATE_TABLE["model_wrong"]
    assert "contrastive" in mw["intervention"]
    assert "verbatim substring" in mw["keep_if"]
    assert "section number" in mw["reject_if"] or "section-number" in mw["reject_if"]
    assert mw["generations"] == 1 and mw["re_retrieve"] is False

    rn = GATE_TABLE["reference_narrow"]
    assert "Do not change the model" in rn["intervention"]
    assert "old and the new score" in rn["keep_if"]
    assert "generation call" in rn["reject_if"]
    assert rn["generations"] == 0 and rn["re_retrieve"] is False


def test_build_gates_deferred_when_step0_incomplete():
    residual = load_residual_qids()
    payload = build_gates({}, step0_complete=False, source="none")
    v = validate_prereg(payload)
    assert v["ok"] is True
    assert payload["step0_complete"] is False
    assert payload["qid_assignment"] == "deferred_pending_step0_labels"
    assert payload["n_residual_total"] == len(residual)
    for label in STEP1_LABELS:
        assert payload["gates"][label]["n"] == 0
        assert payload["gates"][label]["qids"] == []
        assert payload["gates"][label]["intervention"] == GATE_TABLE[label]["intervention"]
    assert payload["budget_step3"]["cap_successful_generations"] == BUDGET_CAP
    assert payload["scorer"] == SCORER
    assert len(SAFETY_PROPERTIES) >= 3


def test_build_gates_assigns_partition_when_complete():
    residual = load_residual_qids()
    # Round-robin complete labels covering residual exactly.
    labels = {qid: STEP1_LABELS[i % 3] for i, qid in enumerate(sorted(residual))}
    payload = build_gates(labels, step0_complete=True, source="test")
    v = validate_prereg(payload)
    assert v["ok"] is True
    assert payload["step0_complete"] is True
    assert payload["qid_assignment"] == "assigned"
    union = set()
    for label in STEP1_LABELS:
        s = set(payload["gates"][label]["qids"])
        assert not (union & s)
        union |= s
        assert payload["gates"][label]["n"] == len(s)
    assert union == set(residual)
    assert sum(payload["gates"][l]["n"] for l in STEP1_LABELS) == len(residual)


def test_validate_prereg_detects_drift():
    residual = load_residual_qids()
    labels = {qid: "model_wrong" for qid in residual}
    payload = build_gates(labels, step0_complete=True, source="test")
    payload["gates"]["model_wrong"]["keep_if"] = "tampered"
    v = validate_prereg(payload)
    assert v["ok"] is False
    assert any("keep_if drifted" in e for e in v["errors"])

    # Mutate a private copy only — build_gates must not share BUDGET_RULES.
    payload2 = build_gates(labels, step0_complete=True, source="test")
    assert payload2["budget_step3"] is not GATE_TABLE  # structural sanity
    from evaluation.step1_preregister_gates import BUDGET_RULES

    assert payload2["budget_step3"] is not BUDGET_RULES
    payload2["budget_step3"]["cap_successful_generations"] = 999
    v2 = validate_prereg(payload2)
    assert v2["ok"] is False
    assert any("budget cap" in e for e in v2["errors"])
    # Global rules unchanged after the drift mutation.
    assert BUDGET_RULES["cap_successful_generations"] == BUDGET_CAP


def test_validate_prereg_detects_partition_break():
    residual = load_residual_qids()
    labels = {qid: "model_wrong" for qid in residual}
    payload = build_gates(labels, step0_complete=True, source="test")
    # Drop one qid from its bucket without re-deriving from labels
    payload["gates"]["model_wrong"]["qids"] = payload["gates"]["model_wrong"]["qids"][:-1]
    payload["gates"]["model_wrong"]["n"] = len(payload["gates"]["model_wrong"]["qids"])
    v = validate_prereg(payload)
    assert v["ok"] is False
    assert any("residual" in e or "partition" in e or "!=" in e for e in v["errors"])


def test_evidence_missing_gate():
    # Keep: span present + binary rose
    r = evaluate_evidence_missing(
        gold_span_in_payload=True,
        section_in_index=True,
        binary_before=False,
        binary_after=True,
    )
    assert r["keep"] is True and r["verdict"] == "kept"
    # Reject: section not in index (hard stop)
    r = evaluate_evidence_missing(
        gold_span_in_payload=False,
        section_in_index=False,
        binary_before=False,
        binary_after=False,
    )
    assert r["keep"] is False
    assert r["reason"] == "section_not_in_index_stop_do_not_loop_retrieval"
    # Reject: span present but binary did not rise
    r = evaluate_evidence_missing(
        gold_span_in_payload=True,
        section_in_index=True,
        binary_before=False,
        binary_after=False,
    )
    assert r["keep"] is False


def test_model_wrong_gate():
    r = evaluate_model_wrong(
        cited_span_in_context=True,
        section_number_only_swap=False,
        operative_sentence_changed=True,
        abstains_on_element_absent_from_payload=False,
    )
    assert r["keep"] is True
    r = evaluate_model_wrong(
        cited_span_in_context=False,
        section_number_only_swap=False,
        operative_sentence_changed=True,
        abstains_on_element_absent_from_payload=False,
    )
    assert r["keep"] is False and "not_verbatim" in r["reason"]
    r = evaluate_model_wrong(
        cited_span_in_context=True,
        section_number_only_swap=True,
        operative_sentence_changed=False,
        abstains_on_element_absent_from_payload=False,
    )
    assert r["keep"] is False and r["reason"] == "section_number_only_swap"
    r = evaluate_model_wrong(
        cited_span_in_context=True,
        section_number_only_swap=False,
        operative_sentence_changed=True,
        abstains_on_element_absent_from_payload=True,
    )
    assert r["keep"] is False and "abstains" in r["reason"]


def test_reference_narrow_gate():
    r = evaluate_reference_narrow(
        old_score_reported=True, new_score_reported=True, generation_call_spent=False
    )
    assert r["keep"] is True
    r = evaluate_reference_narrow(
        old_score_reported=True, new_score_reported=True, generation_call_spent=True
    )
    assert r["keep"] is False and "generation_call" in r["reason"]
    r = evaluate_reference_narrow(
        old_score_reported=True, new_score_reported=False, generation_call_spent=False
    )
    assert r["keep"] is False


def test_evaluate_candidate_dispatch_and_unknown():
    r = evaluate_candidate(
        "evidence_missing",
        {
            "gold_span_in_payload": True,
            "section_in_index": True,
            "binary_before": False,
            "binary_after": True,
        },
    )
    assert r["keep"] is True and r["label"] == "evidence_missing"
    r = evaluate_candidate("nope", {})
    assert r["keep"] is False and "unknown_label" in r["reason"]


def test_quote_in_evidence_whitespace_normalized():
    ctx = "Section 16  of the Act\nimposes a duty."
    assert quote_in_evidence("section 16 of the act imposes a duty", ctx) is True
    assert quote_in_evidence("Section 99 of the Act", ctx) is False
    assert quote_in_evidence("", ctx) is False


def test_publish_writes_artifacts(tmp_path, monkeypatch):
    import evaluation.step1_preregister_gates as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    residual = load_residual_qids()
    labels = {qid: STEP1_LABELS[i % 3] for i, qid in enumerate(sorted(residual))}
    payload = m.build_gates(labels, step0_complete=True, source="test")
    v = m.validate_prereg(payload)
    written = m.publish(payload, v, out_dir=tmp_path)
    assert written["json"].exists()
    assert written["md"].exists()
    assert written["targets"].exists()
    loaded = json.loads(written["json"].read_text())
    assert loaded["step0_complete"] is True
    assert set(loaded["gates"]) == set(STEP1_LABELS)
    md = written["md"].read_text()
    assert "Pre-registered gates" in md
    assert "evidence_missing" in md and "model_wrong" in md and "reference_narrow" in md
    # Mirrored step0 target files
    for label in STEP1_LABELS:
        path = tmp_path / GATE_TABLE[label]["target_file"]
        assert path.exists()
        t = json.loads(path.read_text())
        assert t["label"] == label
        assert t["source"] == "step1_preregistered_gates"


def test_cli_register_allow_incomplete_step0(tmp_path, monkeypatch):
    import evaluation.step1_preregister_gates as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    # Incomplete Step 0 + --require-complete without allow -> exit 2
    code = m.main(["--register", "--require-complete"])
    assert code == 2
    # With allow: registers deferred gates, exit 0
    code = m.main(["--register", "--allow-incomplete-step0"])
    assert code == 0
    assert (tmp_path / "step1_preregistered_gates.json").exists()
    payload = json.loads((tmp_path / "step1_preregistered_gates.json").read_text())
    assert payload["step0_complete"] is False
    assert payload["qid_assignment"] == "deferred_pending_step0_labels"


def test_evaluators_cover_all_labels():
    assert set(EVALUATORS) == set(STEP1_LABELS)


# --------------------------------------------------------------------------- #
# Step 1b — assign qids
# --------------------------------------------------------------------------- #


def test_assign_qids_refuses_incomplete_step0():
    import evaluation.step1_preregister_gates as m

    incomplete = {
        "step0_complete": False,
        "n_missing": 124,
        "n_invalid": 0,
        "n_labeled_valid": 0,
        "n_residual_total": 124,
        "labels": {},
    }
    try:
        m.assign_qids({}, validation0=incomplete)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "complete Step 0" in str(exc)


def test_assign_qids_partitions_and_writes(tmp_path, monkeypatch):
    import evaluation.step1_preregister_gates as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    residual = load_residual_qids()
    labels = _complete_labels()
    from evaluation.step0_label_residual import validate_labels

    v0 = validate_labels(labels, residual)
    assert v0["step0_complete"] is True

    payload, validation, written = m.assign_qids(labels, validation0=v0, source="test")
    assert validation["ok"] is True
    assert payload["step0_complete"] is True
    assert payload["qid_assignment"] == "assigned"

    # Partition: disjoint + covers residual
    union: set[str] = set()
    for label in STEP1_LABELS:
        s = set(payload["gates"][label]["qids"])
        assert not (union & s)
        union |= s
        assert payload["gates"][label]["n"] == len(s)
        # Nonzero for round-robin on 124 residual
        assert payload["gates"][label]["n"] > 0
    assert union == set(residual)
    assert sum(payload["gates"][l]["n"] for l in STEP1_LABELS) == len(residual)

    # Artifacts
    assert written["json"].exists()
    assert written["md"].exists()
    assert written["targets"].exists()
    assert written["assignment"].exists()

    assign = json.loads(written["assignment"].read_text())
    assert assign["step0_complete"] is True
    assert assign["qid_assignment"] == "assigned"
    assert assign["n_residual_total"] == len(residual)
    assert sum(assign["buckets"][l]["n"] for l in STEP1_LABELS) == len(residual)
    assert assign["budget_cap"] == BUDGET_CAP

    targets = json.loads(written["targets"].read_text())
    assert targets["step0_complete"] is True
    assert sum(targets["buckets"][l]["n"] for l in STEP1_LABELS) == len(residual)

    # Mirrored step0_*_targets.json rewritten with assigned qids
    for label in STEP1_LABELS:
        path = tmp_path / GATE_TABLE[label]["target_file"]
        t = json.loads(path.read_text())
        assert t["label"] == label
        assert t["n"] == payload["gates"][label]["n"]
        assert set(t["qids"]) == set(payload["gates"][label]["qids"])
        assert t["source"] == "step1_preregistered_gates"
        assert t["keep_if"] == GATE_TABLE[label]["keep_if"]


def test_cli_assign_qids_fails_when_step0_incomplete(tmp_path, monkeypatch):
    import evaluation.step1_preregister_gates as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    # Live Step 0 is incomplete (0/124) — assign must exit 2 and write nothing
    code = m.main(["--assign-qids"])
    assert code == 2
    assert not (tmp_path / "step1_qid_assignment.json").exists()


def test_cli_assign_qids_with_labels_file(tmp_path, monkeypatch):
    import evaluation.step1_preregister_gates as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    labels = _complete_labels()
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(json.dumps({"labels": labels}), encoding="utf-8")

    code = m.main(["--assign-qids", "--labels", str(labels_path), "--require-complete"])
    assert code == 0
    assert (tmp_path / "step1_qid_assignment.json").exists()
    assert (tmp_path / "step1_preregistered_gates.json").exists()
    payload = json.loads((tmp_path / "step1_preregistered_gates.json").read_text())
    assert payload["step0_complete"] is True
    assert payload["qid_assignment"] == "assigned"
    residual = load_residual_qids()
    union = set()
    for label in STEP1_LABELS:
        union |= set(payload["gates"][label]["qids"])
    assert union == set(residual)


def test_cli_assign_qids_with_partial_labels_file_fails(tmp_path, monkeypatch):
    import evaluation.step1_preregister_gates as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    labels_path = tmp_path / "partial.json"
    labels_path.write_text(json.dumps({"labels": {"Q005": "model_wrong"}}), encoding="utf-8")
    code = m.main(["--assign-qids", "--labels", str(labels_path)])
    assert code == 2
    assert not (tmp_path / "step1_qid_assignment.json").exists()
