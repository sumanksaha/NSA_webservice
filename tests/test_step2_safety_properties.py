"""Offline tests for Step 2 safety properties (no network, no LLM)."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.step1_preregister_gates import SAFETY_PROPERTIES
from evaluation.step2_safety_properties import (
    CONCLUSION_FIELDS,
    PROPERTY_IDS,
    SAFETY_CHECKS,
    build_payload,
    check_gated_rewrite,
    check_no_open_critic,
    check_quote_in_evidence,
    check_zero_regression,
    conclusion_retained,
    evaluate_candidate_list,
    evaluate_safety,
    fields_for_answers,
    gate_answers,
    gate_candidate_list,
    is_full_answer_replacement,
    main,
    quote_in_evidence,
    register,
    validate_payload,
)


def test_property_ids_match_frozen_strings():
    assert len(PROPERTY_IDS) == 4
    assert len(SAFETY_PROPERTIES) == 4
    payload = build_payload()
    texts = [p["text"] for p in payload["properties"]]
    assert texts == list(SAFETY_PROPERTIES)
    ids = [p["id"] for p in payload["properties"]]
    assert ids == list(PROPERTY_IDS)
    for p in payload["properties"]:
        assert p["action_on_fail"] == "keep_d2"
        assert p["check"] == SAFETY_CHECKS[p["id"]].__name__


def test_validate_payload_ok():
    v = validate_payload(build_payload())
    assert v["ok"] is True
    assert v["errors"] == []
    assert v["n_properties"] == 4


def test_validate_payload_detects_drift():
    payload = build_payload()
    payload["properties"][0]["text"] = "tampered"
    v = validate_payload(payload)
    assert v["ok"] is False
    assert any("drifted" in e for e in v["errors"])

    payload2 = build_payload()
    payload2["properties"][1]["action_on_fail"] = "delete_d2"
    v2 = validate_payload(payload2)
    assert v2["ok"] is False


def test_zero_regression_property():
    # Already-correct must not regress
    r = check_zero_regression(already_correct=True, candidate_correct=False)
    assert r["pass"] is False and r["action"] == "keep_d2"
    # Already-correct stays correct
    r = check_zero_regression(already_correct=True, candidate_correct=True)
    assert r["pass"] is True
    # Incorrect staying incorrect is not a regression
    r = check_zero_regression(already_correct=False, candidate_correct=False)
    assert r["pass"] is True
    # Incorrect -> correct is a gain, not a regression
    r = check_zero_regression(already_correct=False, candidate_correct=True)
    assert r["pass"] is True


def test_quote_in_evidence_property():
    # No rewrite: pass
    r = check_quote_in_evidence(cited_span="", context="", rewrite=False)
    assert r["pass"] is True
    # Span present verbatim (whitespace-normalized)
    r = check_quote_in_evidence(
        cited_span="the licence fee  is  five lakh",
        context="Under section 16 the licence fee is five lakh rupees.",
        rewrite=True,
    )
    assert r["pass"] is True
    # Span absent: discard, D2 stands
    r = check_quote_in_evidence(
        cited_span="imprisonment for seven years",
        context="the fine may extend to five lakh rupees",
        rewrite=True,
    )
    assert r["pass"] is False and r["action"] == "keep_d2"
    # Empty span/context fails the quote check for a claimed rewrite
    r = check_quote_in_evidence(cited_span="", context="", rewrite=True)
    assert r["pass"] is False


def test_no_open_critic_property():
    r = check_no_open_critic(is_open_critic=False, is_full_answer_replacement=False)
    assert r["pass"] is True
    r = check_no_open_critic(is_open_critic=True, is_full_answer_replacement=False)
    assert r["pass"] is False and r["action"] == "keep_d2"
    r = check_no_open_critic(is_open_critic=False, is_full_answer_replacement=True)
    assert r["pass"] is False and r["reason"] == "unconstrained_full_answer_replacement"


def test_gated_rewrite_property():
    # No conclusion change: pass
    r = check_gated_rewrite()
    assert r["pass"] is True
    # Conclusion change with quote + checker: pass
    r = check_gated_rewrite(
        conclusion_fields_changed=["operative_rule", "authority"],
        new_subsection_quoted_from_evidence=True,
        frozen_checker_accepts=True,
    )
    assert r["pass"] is True
    # No-rewrite sentence deleted (not replaced by gated rewrite): fail
    r = check_gated_rewrite(no_rewrite_sentence_deleted=True)
    assert r["pass"] is False and r["action"] == "keep_d2"
    # Conclusion changed without quote: fail
    r = check_gated_rewrite(
        conclusion_fields_changed=["remedy"],
        new_subsection_quoted_from_evidence=False,
        frozen_checker_accepts=True,
    )
    assert r["pass"] is False
    # Conclusion changed without frozen checker: fail
    r = check_gated_rewrite(
        conclusion_fields_changed=["operative_rule"],
        new_subsection_quoted_from_evidence=True,
        frozen_checker_accepts=False,
    )
    assert r["pass"] is False
    # Full answer replacement: fail
    r = check_gated_rewrite(is_full_answer_replacement=True)
    assert r["pass"] is False
    # Unknown conclusion field: fail
    r = check_gated_rewrite(conclusion_fields_changed=["tone"])
    assert r["pass"] is False and "unknown_conclusion_fields" in r["reason"]


def test_evaluate_safety_happy_path():
    r = evaluate_safety(
        {
            "already_correct": False,
            "candidate_correct": True,
            "cited_span": "shall not manufacture",
            "context": "No person shall not manufacture, process, distribute or sell",
            "is_open_critic": False,
            "is_full_answer_replacement": False,
            "no_rewrite_sentence_deleted": False,
            "conclusion_fields_changed": ["remedy"],
            "new_subsection_quoted_from_evidence": True,
            "frozen_checker_accepts": True,
        }
    )
    assert r["pass"] is True
    assert r["verdict"] == "accepted"
    assert r["keep_d2"] is False
    assert len(r["checks"]) == 4
    assert all(c["pass"] for c in r["checks"])


def test_evaluate_safety_first_failure_wins():
    # Regression fails first (property 1), even if other fields are also bad
    r = evaluate_safety(
        {
            "already_correct": True,
            "candidate_correct": False,
            "is_open_critic": True,
        }
    )
    assert r["pass"] is False
    assert r["failed_property"] == "zero_regression"
    assert r["keep_d2"] is True
    # Only the first failing property is recorded as failed_property
    assert r["checks"][0]["pass"] is False
    # Later checks are not run after first failure
    assert len(r["checks"]) == 1


def test_evaluate_safety_quote_failure():
    r = evaluate_safety(
        {
            "already_correct": False,
            "candidate_correct": False,
            "cited_span": "not in context at all",
            "context": "some other evidence text",
        }
    )
    assert r["pass"] is False
    assert r["failed_property"] == "quote_in_evidence"
    assert r["keep_d2"] is True


def test_evaluate_safety_open_critic_after_quote_pass():
    r = evaluate_safety(
        {
            "already_correct": False,
            "candidate_correct": False,
            "cited_span": "verbatim span",
            "context": "this has the verbatim span inside",
            "is_open_critic": True,
        }
    )
    assert r["pass"] is False
    assert r["failed_property"] == "no_open_critic"


def test_evaluate_safety_gated_rewrite_failure():
    r = evaluate_safety(
        {
            "already_correct": False,
            "candidate_correct": True,
            "cited_span": "ok",
            "context": "ok is here",
            "is_open_critic": False,
            "no_rewrite_sentence_deleted": True,
        }
    )
    assert r["pass"] is False
    assert r["failed_property"] == "gated_rewrite"


def test_evaluate_candidate_list_counts():
    rows = [
        # pass
        {
            "qid": "Q001",
            "already_correct": False,
            "candidate_correct": True,
            "cited_span": "span",
            "context": "contains span",
        },
        # fail: regression
        {"qid": "Q002", "already_correct": True, "candidate_correct": False},
        # fail: open critic (quote passes)
        {
            "qid": "Q003",
            "already_correct": False,
            "candidate_correct": False,
            "cited_span": "span",
            "context": "contains span",
            "is_open_critic": True,
        },
    ]
    out = evaluate_candidate_list(rows)
    assert out["n"] == 3
    assert out["accepted"] == 1
    assert out["rejected"] == 2
    assert out["rejected_by_property"]["zero_regression"] == 1
    assert out["rejected_by_property"]["no_open_critic"] == 1
    qids = [r["qid"] for r in out["results"]]
    assert qids == ["Q001", "Q002", "Q003"]


def test_conclusion_retained_and_full_replacement():
    d2 = "The licence requirement applies [1]."
    # Notes appended: conclusion retained, not a full replacement.
    notes = "The licence requirement applies [1].\nNote: consider the exemption."
    assert conclusion_retained(d2, notes) is True
    assert is_full_answer_replacement(d2, notes) is False
    # Identical: not a replacement.
    assert is_full_answer_replacement(d2, d2) is False
    # Full replacement: D2 conclusion gone.
    replacement = "The exemption applies; no licence is required."
    assert conclusion_retained(d2, replacement) is False
    assert is_full_answer_replacement(d2, replacement) is True


def test_fields_for_answers_note_append_passes_gate():
    d2 = "The licence requirement applies [1]."
    cand = d2 + "\nNote: consider the petty-retailer exemption."
    fields = fields_for_answers(d2_answer=d2, candidate_answer=cand, context="ctx")
    assert fields["is_rewrite"] is False  # conclusion retained → not a content rewrite
    assert fields["is_full_answer_replacement"] is False
    assert fields["is_open_critic"] is False
    r = gate_answers(d2_answer=d2, candidate_answer=cand, context="ctx")
    assert r["pass"] is True
    assert r["keep_d2"] is False


def test_fields_for_answers_full_replacement_rejected():
    d2 = "The licence requirement applies [1]."
    replacement = "The exemption applies; no licence is required."
    fields = fields_for_answers(d2_answer=d2, candidate_answer=replacement, context="ctx")
    assert fields["is_rewrite"] is True
    assert fields["is_full_answer_replacement"] is True
    assert fields["is_open_critic"] is True
    r = gate_answers(d2_answer=d2, candidate_answer=replacement, context="ctx")
    # Quote check runs first (empty cited_span) then open-critic — either rejects.
    assert r["pass"] is False
    assert r["keep_d2"] is True
    assert r["failed_property"] in ("quote_in_evidence", "no_open_critic")


def test_gate_candidate_list_counts():
    rows = [
        {
            "qid": "Q1",
            "d2_answer": "The licence requirement applies [1].",
            "candidate_answer": "The licence requirement applies [1].\nNote: ok.",
        },
        {
            "qid": "Q2",
            "d2_answer": "The licence requirement applies [1].",
            "candidate_answer": "Something entirely different with no overlap.",
        },
        {
            "qid": "Q3",
            "d2_answer": "Already correct [1].",
            "candidate_answer": "Already correct [1].",
            "already_correct": True,
            "candidate_correct": False,
        },
    ]
    out = gate_candidate_list(rows)
    assert out["n"] == 3
    assert out["accepted"] == 1
    assert out["rejected"] == 2
    # Full replacement fails at quote (empty span) before open-critic, or at open-critic.
    assert (
        out["rejected_by_property"].get("quote_in_evidence", 0)
        + out["rejected_by_property"].get("no_open_critic", 0)
    ) >= 1
    assert out["rejected_by_property"]["zero_regression"] == 1
    # Q2 specifically is the full replacement.
    q2 = next(r for r in out["results"] if r["qid"] == "Q2")
    assert q2["pass"] is False
    assert q2["keep_d2"] is True
    assert q2["failed_property"] in ("quote_in_evidence", "no_open_critic")


def test_cli_gate_answers(tmp_path):
    import evaluation.step2_safety_properties as m

    rows = [
        {
            "qid": "Q1",
            "d2_answer": "The licence requirement applies [1].",
            "candidate_answer": "Something else entirely.",
        },
    ]
    path = tmp_path / "gate.json"
    path.write_text(json.dumps({"records": rows}), encoding="utf-8")
    code = m.main(["--gate-answers", str(path)])
    assert code == 0


def test_register_writes_artifacts(tmp_path, monkeypatch):
    import evaluation.step2_safety_properties as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    payload, validation, written = m.register(out_dir=tmp_path)
    assert validation["ok"] is True
    assert written["json"].exists()
    assert written["md"].exists()
    loaded = json.loads(written["json"].read_text())
    assert loaded["n_properties"] == 4
    assert [p["id"] for p in loaded["properties"]] == list(PROPERTY_IDS)
    md = written["md"].read_text()
    assert "Safety properties" in md
    for pid in PROPERTY_IDS:
        assert pid in md
    assert list(loaded["conclusion_fields_under_gate"]) == list(CONCLUSION_FIELDS)


def test_cli_register_and_require_registered(tmp_path, monkeypatch):
    import evaluation.step2_safety_properties as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    # require without register -> exit 2
    code = m.main(["--require-registered", "--out-dir", str(tmp_path)])
    assert code == 2
    # register -> exit 0
    code = m.main(["--register", "--out-dir", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "step2_safety_properties.json").exists()
    # require after register -> exit 0
    code = m.main(["--require-registered", "--out-dir", str(tmp_path)])
    assert code == 0


def test_cli_evaluate_candidates(tmp_path, monkeypatch):
    import evaluation.step2_safety_properties as m

    monkeypatch.setattr(m, "OUT", tmp_path)
    rows = [
        {
            "qid": "Q1",
            "already_correct": True,
            "candidate_correct": False,
        },
        {
            "qid": "Q2",
            "already_correct": False,
            "candidate_correct": True,
            "cited_span": "span",
            "context": "has span",
        },
    ]
    path = tmp_path / "cands.json"
    path.write_text(json.dumps({"candidates": rows}), encoding="utf-8")
    code = m.main(["--evaluate", "--candidates", str(path)])
    assert code == 0


def test_cli_no_args_prints_help():
    code = main([])
    assert code == 1


def test_quote_in_evidence_reexported():
    # Step 2 reuses Step 1's frozen quote check (single implementation)
    from evaluation.step1_preregister_gates import quote_in_evidence as q1

    assert quote_in_evidence is q1
    assert quote_in_evidence("abc def", "xx abc   def yy") is True
    assert quote_in_evidence("abc", "xyz") is False


def test_safety_properties_match_step1_registration():
    # The four strings in Step 2 are exactly what Step 1 registered
    s1 = json.loads(
        (ROOT / "evaluation" / "out" / "ceiling_v5" / "step1_preregistered_gates.json").read_text()
    )
    assert s1["safety_properties_step2"] == list(SAFETY_PROPERTIES)
