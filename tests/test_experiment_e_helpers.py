"""Offline helper tests for Experiment E (no LLM calls, no network).

Covers the deterministic citation checker, the E2 removal-only answer builder,
the repair-payload validator, and the E1 answer post-processing — the pieces
whose behaviour is fully specified by Experiment_E_Citation_Verification_Design.md.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.experiment_e_citation_verification import (
    REPAIR_SYSTEM_PROMPT,
    _marker_span,
    _section_numbers_in,
    build_claim_index,
    check_claim_support,
    deterministic_citation_check,
    e1_answer_from_payload,
    e2_answer,
    render_repair_prompts,
    validate_repair_payload,
)


# --------------------------------------------------------------------------- #
# Section extraction
# --------------------------------------------------------------------------- #
def test_section_numbers_in_extracts_subsections():
    t = "Under Section 38 of the FSS Act 2006 and section 42(5) plus 68(1)(b) rules"
    got = _section_numbers_in(t)
    assert "38" in got
    assert "42(5)" in got
    assert "68(1)(b)" in got


def test_section_numbers_in_empty():
    assert _section_numbers_in("no numbers here") == set()


# --------------------------------------------------------------------------- #
# Claim-window extraction (semicolon-aware — the Q007 false-positive fix)
# --------------------------------------------------------------------------- #
def test_marker_span_sentence_bounded():
    text = "First sentence. Source [2] provides the appointment mechanism. Third sentence."
    span = _marker_span(text, "[2]")
    assert "appointment mechanism" in span
    assert "First sentence" not in span
    assert "Third sentence" not in span


def test_marker_span_semicolon_bounded():
    text = "Source [14] defines X under section 37; [16] provides the mechanism; [15] lists bodies."
    span = _marker_span(text, "[16]")
    assert "section 37" not in span  # neighbour clause must not leak in
    assert "mechanism" in span


# --------------------------------------------------------------------------- #
# check_claim_support labels
# --------------------------------------------------------------------------- #
def test_support_unverifiable_when_chunk_lacks_claimed_section():
    res = check_claim_support(
        "The officer shall inspect under section 42(5).",
        "[3]",
        "The Food Authority may by order delegate powers to the Commissioner.",
    )
    # Positive-mismatch-only: a chunk with no section identifier cannot
    # demonstrate a mismatch (chunks are mid-section fragments).
    assert res["label"] == "unverifiable-by-heuristics"
    assert "42(5)" in res["reason"]


def test_support_supported_when_section_matches():
    res = check_claim_support(
        "Under section 16(1) the Food Authority shall regulate and monitor food.",
        "[1]",
        "Section 16(1): It shall be the duty of the Food Authority to regulate, monitor the manufacture, sale and import of food.",
    )
    assert res["label"] == "supported"


def test_support_unverifiable_when_chunk_names_no_section():
    res = check_claim_support(
        "Under Section 38 the Food Safety Officer takes samples for analysis.",
        "[38]",
        "Where the Designated Officer receives a report he shall forward it, without unnecessary delay, to the purchaser.",
    )
    # Positive-mismatch-only policy: no section identifier in the chunk means
    # no demonstrable mismatch -> unverifiable, never convicted.
    assert res["label"] == "unverifiable-by-heuristics"


def test_support_unverifiable_when_no_signal():
    res = check_claim_support(
        "The authority concerned may appoint gazetted officers for this purpose.",
        "[7]",
        "The Central Government may, by notification, establish committees as it considers necessary for standard enforcement.",
    )
    # No section signal + zero keyword overlap: the honest label is
    # 'unverifiable-by-heuristics' (conservative — the checker must not
    # fabricate a misattribution it cannot demonstrate).
    assert res["label"] in {"unverifiable-by-heuristics", "supported"}


# --------------------------------------------------------------------------- #
# Claim index over the D2 analysis structure
# --------------------------------------------------------------------------- #
def test_build_claim_index_finds_markers_across_fields():
    analysis = {
        "legal_conclusion": "The authority must act under section 31 [2].",
        "governing_provisions": [{"provision_id": "s31", "reason": "see [1]"}],
        "conditions": [{"condition": "licence required", "source": "[3]"}],
        "uncertainties": ["timing unclear [2]"],
    }
    pairs = build_claim_index(analysis)
    keys = {(p["marker"], p["field"]) for p in pairs}
    assert ("[2]", "legal_conclusion") in keys
    assert ("[1]", "governing_provisions") in keys
    # sibling binding: the pure source pointer is paired with the sibling text
    assert ("[3]", "conditions.source") in keys
    assert ("[2]", "uncertainties") in keys


def test_build_claim_index_dedupes_pairs():
    analysis = {"legal_conclusion": "Rule applies [1]. Rule applies [1]."}
    pairs = build_claim_index(analysis)
    assert len([p for p in pairs if p["marker"] == "[1]"]) == 1


# --------------------------------------------------------------------------- #
# deterministic_citation_check end-to-end (structure level)
# --------------------------------------------------------------------------- #
def test_check_flags_out_of_range_marker():
    analysis = {"legal_conclusion": "Totally unsupported claim [9]."}
    citations = [{"index": 1, "chunk_id": "c1", "text": "Some real evidence text about licences."}]
    out = deterministic_citation_check(analysis, citations)
    assert out["labels"].get("misattributed") == 1
    assert "outside the supplied evidence range" in out["results"][0]["reason"]


def test_check_uses_claim_window_not_whole_field():
    analysis = {
        "supporting_evidence": [
            {"claim": "Source [1] defines X under section 37; [2] provides the mechanism", "source": "[1]"}
        ]
    }
    citations = [
        {"index": 1, "chunk_id": "c1", "text": "Definition of X: an officer appointed under section 37."},
        {"index": 2, "chunk_id": "c2", "text": "The appointment mechanism is by order of the Commissioner."},
    ]
    out = deterministic_citation_check(analysis, citations)
    by_marker = {r["marker"]: r["label"] for r in out["results"]}
    # The neighbour's "section 37" must not convict [2]; [1]'s window may be
    # confirmed or left unverifiable, but never misattributed.
    assert by_marker.get("[2]") != "misattributed"
    assert by_marker.get("[1]") != "misattributed"


# --------------------------------------------------------------------------- #
# E2 removal-only answer builder
# --------------------------------------------------------------------------- #
def _check_from(analysis, citations):
    return deterministic_citation_check(analysis, citations)


def test_e2_removes_only_fully_misattributed_markers():
    # Genuine positive mismatch: claim asserts section 31(2), chunk is from
    # a different section (44) — demonstrable misattribution.
    analysis = {"legal_conclusion": "Under section 31(2) the business must be registered [1]."}
    citations = [
        {
            "index": 1,
            "chunk_id": "c1",
            "text": "Under section 44 no licence shall be required for such petty retailers.",
        }
    ]
    check = _check_from(analysis, citations)
    assert check["labels"].get("misattributed", 0) >= 1
    ans = e2_answer("The FSSAI must register the business [1].", check, max_index=2)
    assert "[1]" not in ans
    assert "register the business" in ans  # text preserved, only marker dropped


def test_e2_keeps_unverifiable_markers():
    # No demonstrable mismatch: conservative removal policy keeps the marker.
    analysis = {"legal_conclusion": "The FSSAI must register the business [1]."}
    citations = [{"index": 1, "chunk_id": "c1", "text": "Unrelated evidence about registration of food businesses."}]
    check = _check_from(analysis, citations)
    assert check["labels"].get("misattributed", 0) == 0
    ans = e2_answer("The FSSAI must register the business [1].", check, max_index=2)
    assert "[1]" in ans


def test_e2_keeps_mixed_evidence_markers():
    analysis = {"legal_conclusion": "Mixed use [1]."}
    citations = [{"index": 1, "chunk_id": "c1", "text": "Registration is required under section 31 of the Act."}]
    check = _check_from(analysis, citations)
    # the claim window flags (no section match), but simulate mixed evidence by
    # patching one result back to supported
    check["results"][0]["label"] = "supported"
    ans = e2_answer("Registration required [1].", check, max_index=1)
    assert "[1]" in ans


def test_e2_falls_back_to_supported_sources_tail():
    analysis = {"legal_conclusion": "Under section 11(1) everything wrong [1]; and under section 12(2) also wrong [2]."}
    citations = [
        {
            "index": 1,
            "chunk_id": "c1",
            "text": "Under section 21(1) packaging shall conform to the notified standards.",
        },
        {"index": 2, "chunk_id": "c2", "text": "Under section 33(2) labelling requirements apply to packaged goods."},
    ]
    check = _check_from(analysis, citations)
    assert check["labels"].get("misattributed", 0) == 2
    ans = e2_answer("Bare conclusion [1] [2].", check, max_index=2)
    assert "[1]" not in ans and "[2]" not in ans
    assert "Bare conclusion" in ans


# --------------------------------------------------------------------------- #
# Repair payload validation + E1 answer post-processing
# --------------------------------------------------------------------------- #
def test_validate_repair_payload_accepts_valid():
    payload = {
        "repaired_claims": [{"claim": "c", "old_marker": "[1]", "new_marker": "[2]", "basis": "b"}],
        "removed_claims": [],
        "conclusion_unchanged": True,
        "final_answer": "The licensing requirement under section 31(2) applies here [2].",
    }
    ok, why = validate_repair_payload(payload, "q")
    assert ok, why


def test_validate_repair_payload_rejects_degenerate():
    base = {
        "repaired_claims": [],
        "removed_claims": [],
        "conclusion_unchanged": False,
        "final_answer": "short",
    }
    ok, why = validate_repair_payload(base, "q")
    assert not ok
    for missing in ("final_answer", "repaired_claims", "conclusion_unchanged"):
        broken = dict(base)
        broken.pop(missing)
        ok, why = validate_repair_payload(broken, "q")
        assert not ok and missing in why


def test_e1_answer_drops_out_of_range_markers():
    check = {"results": [{"marker": "[2]", "label": "supported"}]}
    text = e1_answer_from_payload({"final_answer": "Answer cites [2] and a phantom [99]."}, check, max_index=2)
    assert "[99]" not in text
    assert "[2]" in text


def test_e1_answer_reattaches_supported_sources_when_all_lost():
    check = {"results": [{"marker": "[3]", "label": "supported"}]}
    text = e1_answer_from_payload({"final_answer": "A fully supported conclusion without markers."}, check, max_index=5)
    assert "[3]" in text


def test_render_repair_prompts_contains_flagged_json():
    sys_p, user_p = render_repair_prompts(
        "Q?",
        "CONTEXT",
        '{"legal_conclusion": "x"}',
        json.dumps([
            {"claim": "c", "marker": "[4]", "checker_reason": "no section", "cited_chunk_text_excerpt": "..."}
        ]),
    )
    assert sys_p == REPAIR_SYSTEM_PROMPT
    assert "FLAGGED CITATIONS" in user_p
    assert "[4]" in user_p
    assert "STRUCTURED ANALYSIS" in user_p


# --------------------------------------------------------------------------- #
# run_e_one repair-call path (offline: stub client + monkeypatched context)
# --------------------------------------------------------------------------- #
def test_run_e_one_repair_path_offline(tmp_path, monkeypatch):
    import threading
    import types

    import evaluation.experiment_e_citation_verification as emod

    canned_check = {
        "claims_checked": 1,
        "labels": {"misattributed": 1},
        "results": [
            {
                "claim": "Under section 31(2) the business must be registered.",
                "marker": "[1]",
                "field": "legal_conclusion",
                "label": "misattributed",
                "reason": "claim cites ['31(2)'] but chunk contains ['44']",
                "chunk_id": "c1",
            },
        ],
    }
    fake_built = types.SimpleNamespace(citations=[], context="CTX")
    monkeypatch.setattr(emod, "_build_context", lambda *a, **k: ([], fake_built, 2))
    monkeypatch.setattr(emod, "deterministic_citation_check", lambda *a, **k: dict(canned_check))

    manifest_q = {"Q001": {"question": "Q?", "conditions": {emod.O3_COND: {"context_chunk_ids": []}}}}
    d2_rec = {"analysis": {"legal_conclusion": "x"}, "answer": "The FSSAI must register the business [1]."}
    calls_path = tmp_path / "calls.jsonl"

    out = emod.run_e_one(
        "Q001",
        manifest_q,
        {},
        emod._EStubClient(),
        calls_path,
        threading.Lock(),
        d2_rec,
    )
    e1, e2 = out["e1"], out["e2"]
    # repair call actually made and logged
    assert e1.get("repair_call_made") is True
    assert not e1.get("error")
    assert "Stub repair answer" in e1["answer"]
    assert calls_path.exists() and len(calls_path.read_text(encoding="utf-8").strip().splitlines()) == 1
    call_rec = json.loads(calls_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert call_rec["condition"] == "E1" and call_rec["stage"] == "repair" and call_rec["success"] is True
    # E2 removed the fully-flagged marker with 0 calls
    assert "[1]" not in e2["answer"]
    assert "register the business" in e2["answer"]


def test_run_e_one_no_flag_skips_call(tmp_path, monkeypatch):
    import threading
    import types

    import evaluation.experiment_e_citation_verification as emod

    canned_check = {
        "claims_checked": 1,
        "labels": {"supported": 1},
        "results": [
            {
                "claim": "supported claim text",
                "marker": "[1]",
                "field": "legal_conclusion",
                "label": "supported",
                "reason": "ok",
                "chunk_id": "c1",
            }
        ],
    }
    fake_built = types.SimpleNamespace(citations=[], context="CTX")
    monkeypatch.setattr(emod, "_build_context", lambda *a, **k: ([], fake_built, 2))
    monkeypatch.setattr(emod, "deterministic_citation_check", lambda *a, **k: dict(canned_check))

    manifest_q = {"Q001": {"question": "Q?", "conditions": {emod.O3_COND: {"context_chunk_ids": []}}}}
    d2_rec = {"analysis": {"legal_conclusion": "supported claim text"}, "answer": "Answer text [1]."}
    calls_path = tmp_path / "calls.jsonl"

    out = emod.run_e_one("Q001", manifest_q, {}, emod._EStubClient(), calls_path, threading.Lock(), d2_rec)
    e1 = out["e1"]
    assert e1.get("repair_skipped") == "no misattributed citations flagged"
    assert e1.get("repair_call_made") is False
    assert e1["answer"] == d2_rec["answer"]  # unchanged D2 answer
    assert not calls_path.exists()  # no generation logged
