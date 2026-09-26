"""Offline tests for Step 3 — budgeted gated generation (no network, no LLM).

Covers the two registered branches (evidence_missing / model_wrong), the
human-in-the-loop reference-anchored fill workflow, the budget ledger
(150-cap, transport failures outside, reference_narrow spends zero), the
checkpoint/resume behavior, and the per-label-before-aggregate report.
"""

from __future__ import annotations

import json
import sys
import types
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

import evaluation.step3_gated_generation as s
from evaluation.resolution import FamilyMap
from evaluation.step3_gated_generation import (
    BUDGET_CAP,
    MAX_ATTEMPTS,
    REF_FILL_K,
    Deps,
    Ledger,
    build_family_indexes,
    build_fill_review,
    build_report,
    cut_contrastive_options,
    derive_mw_fields,
    derive_status,
    derive_step2_fields,
    derive_targets,
    em_precondition,
    execute,
    find_ref_fill,
    load_fill_approval,
    preflight,
    render_report_md,
    run_one_em,
    run_one_mw,
    section_mentions,
    section_number_only_swap,
    write_approval,
)

EPA = "Environment Protection Act, 1986"
SPAN = (
    "The Central Government may give directions to any State Government or "
    "person regarding the discharge of pollutants in streams or wells."
)
C1 = "Section 7. " + SPAN + " The State Board shall comply with such directions."
C2 = (
    "Section 8. Every person carrying on any industry, operation or process "
    "shall not discharge electromagnetic radiation into the environment "
    "without the prior consent of the State Board."
)
C3 = "Section 12. " + SPAN
C9 = (
    "Any person intending to establish any industrial plant shall furnish "
    "information about the process and hazardous substance to the prescribed "
    "authority. Nothing else is prescribed here."
)
EM_CONCL = SPAN  # EM reference conclusion (== SPAN)
MW_CONCL = SPAN + " under section 12."
D2_EM = (
    "The State Board frames effluent standards for industrial discharge into "
    "water bodies."
)
D2_MW = (
    "The Board shall prescribe effluent standards and collect fees from every "
    "industry for the time being."
)


class _U:
    def __init__(self, family: str, section):
        self.family = family
        self.section = section
        self.provision_id = f"{family}:{section}"


class _Q:
    def __init__(self, question: str, conclusion: str, units: list[_U]):
        self.question = question
        self.acceptable_conclusion = conclusion
        self._units = units

    def recall_units(self):
        return list(self._units)


def _payload() -> dict:
    return {
        "c1": {"chunk_text": C1, "act_name": EPA, "section_number": "7"},
        "c2": {"chunk_text": C2, "act_name": EPA, "section_number": "8"},
        "c3": {"chunk_text": C3, "act_name": EPA, "section_number": "12"},
        "c9": {"chunk_text": C9, "act_name": EPA, "section_number": "9"},
    }


def _mk_deps(
    questions: dict,
    payload: dict,
    frozen: dict[str, list[str]],
    d2: dict[str, str] | None = None,
    widened: dict | None = None,
    labels: dict | None = None,
) -> Deps:
    fam_map = FamilyMap()
    fam_sec, fam_chunks = build_family_indexes(payload, fam_map)
    manifest = {
        qid: {
            "question": questions[qid].question,
            "conditions": {"O3_full_support": {"context_chunk_ids": list(ids)}},
        }
        for qid, ids in frozen.items()
    }
    return Deps(
        questions=questions,
        payload=payload,
        manifest=manifest,
        d2_answers=d2 or {},
        widened=widened or {},
        banned=set(),
        fam_map=fam_map,
        fam_sec_index=fam_sec,
        fam_chunks=fam_chunks,
        labels=labels or {},
    )


def _em_deps(**kw) -> Deps:
    """QEM: gold unit epa/7 NOT in the frozen payload -> mechanical add."""
    q = _Q(
        "Which provision empowers the Central Government to issue directions "
        "about discharge of pollutants?",
        EM_CONCL,
        [_U("epa", "7")],
    )
    return _mk_deps({"QEM": q}, _payload(), {"QEM": ["c2"]}, **kw)


def _mw_deps(**kw) -> Deps:
    """QMW: frozen payload already carries the governing span in c3."""
    q = _Q(
        "Under which provision may the Central Government issue directions "
        "regarding discharge of pollutants?",
        MW_CONCL,
        [_U("epa", "12")],
    )
    return _mk_deps({"QMW": q}, _payload(), {"QMW": ["c2", "c3"]}, **kw)


def _resp(payload, error=None, text=None):
    body = text if text is not None else json.dumps(payload)
    return types.SimpleNamespace(
        text=body,
        error=error,
        usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        latency=0.01,
    )


class _FakeClient:
    def __init__(self, payloads=None, error=None, text=None):
        self.payloads = list(payloads or [])
        self.error = error
        self.text = text
        self.calls = 0

    def call(self, system, user, *, temperature, max_tokens):
        self.calls += 1
        if self.error is not None:
            return _resp(None, error=self.error)
        if self.text is not None:
            return _resp(None, text=self.text)
        payload = self.payloads.pop(0) if self.payloads else {}
        return _resp(payload)


# --------------------------------------------------------------------------- #
# Target derivation / section mentions
# --------------------------------------------------------------------------- #

def test_section_mentions_variants():
    assert section_mentions("see section 12 of the Act") == {"12"}
    assert section_mentions("under s7 of the Act") == {"7"}
    assert section_mentions("sec. 4 applies") == {"4"}
    assert section_mentions("s. 9 of the rules") == {"9"}
    assert section_mentions("Section 16(2)(ii) governs") == {"16"}
    # no digits -> no mention
    assert section_mentions("Which provision empowers the government?") == set()


def test_derive_targets_splits_units_and_mentions():
    q = _Q("What does section 9 require?", EM_CONCL, [_U("epa", "7"), _U("epa", None)])
    t = derive_targets(q)
    assert t["unit_pairs"] == {("epa", "7")}
    assert t["instrument_families"] == ["epa"]
    assert "9" in t["mention_secs"]
    assert t["families"] == ["epa"]


# --------------------------------------------------------------------------- #
# evidence_missing precondition (zero-call gates)
# --------------------------------------------------------------------------- #

def test_em_section_not_in_index_zero_call_reject():
    deps = _em_deps()
    deps.questions["QEM"]._units = [_U("epa", "42")]  # section absent from index
    rec = em_precondition("QEM", deps)
    assert rec["verdict"] == "rejected"
    assert rec["reason"] == "section_not_in_index_stop_do_not_loop_retrieval"
    assert rec["section_in_index"] is False


def test_em_eligible_adds_target_chunks_first():
    deps = _em_deps()
    rec = em_precondition("QEM", deps)
    assert rec["verdict"] == "eligible"
    assert rec["reason"] == "text_added_to_payload"
    assert rec["n_added_chunks"] == 1
    # added chunk is PREPENDED so it survives char truncation
    assert rec["new_payload_ids"][0] == "c1"
    assert set(rec["new_payload_ids"]) == {"c1", "c2"}
    assert rec["gold_span_in_payload"] is True
    assert SPAN in rec["context"]
    assert rec["uncovered_targets"] == []
    # plan artifacts must never carry the heavy fields
    assert rec["context"] and rec["new_payload_ids"]


def test_em_gold_already_in_payload_without_ref_candidate_is_not_run():
    q = _Q(
        "What is the appellate mechanism for an aggrieved manufacturer?",
        "The appellate mechanism shall be available to an aggrieved "
        "manufacturer within sixty days.",
        [_U("epa", "7")],
    )
    deps = _mk_deps({"QNT": q}, _payload(), {"QNT": ["c1", "c2"]})
    rec = em_precondition("QNT", deps)
    assert rec["verdict"] == "not_run"
    assert rec["reason"] == "no_new_text_added"
    assert "proposed_fill" not in rec


def test_em_question_missing_is_not_run():
    deps = _em_deps()
    rec = em_precondition("NOPE", deps)
    assert rec["verdict"] == "not_run"
    assert rec["reason"] == "question_missing"


# --------------------------------------------------------------------------- #
# Reference-anchored fill (human-in-the-loop)
# --------------------------------------------------------------------------- #

def _fill_deps() -> Deps:
    """Gold epa/7 already in payload; epa/9 chunk carries the reference text."""
    q = _Q(
        "What must a person do before establishing an industrial plant?",
        C9,  # reference == c9 text (no section mention anywhere)
        [_U("epa", "7")],
    )
    return _mk_deps({"QFILL": q}, _payload(), {"QFILL": ["c1", "c2"]})


def test_fill_pending_until_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    deps = _fill_deps()

    rec = em_precondition("QFILL", deps)
    assert rec["verdict"] == "not_run"
    assert rec["reason"] == "fill_review_pending"
    assert load_fill_approval() == set()
    assert rec["proposed_fill"]["candidates"], "c9 must be proposed"
    assert all(c["chunk_id"] != "c1" for c in rec["proposed_fill"]["candidates"])
    assert rec["proposed_fill"]["candidates"][0]["chunk_id"] == "c9"
    assert rec["proposed_fill"]["candidates"][0]["snippet"]

    write_approval({"QFILL"}, source="test")
    assert load_fill_approval() == {"QFILL"}

    rec2 = em_precondition("QFILL", deps)
    assert rec2["verdict"] == "eligible"
    assert rec2["fill_approved"] is True
    assert rec2["new_payload_ids"][0] == "c9"
    assert C9.split(".")[0] in rec2["context"] or C9[:40] in rec2["context"]


def test_find_ref_fill_properties(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    deps = _fill_deps()
    # near-duplicate of a frozen chunk (whitespace only) must be excluded
    deps.payload["c5"] = {
        "chunk_text": "   " + C1 + "  ",
        "act_name": EPA,
        "section_number": "16",
    }
    deps.fam_sec_index, deps.fam_chunks = build_family_indexes(deps.payload, deps.fam_map)

    out = find_ref_fill("QFILL", deps)
    ids = [c["chunk_id"] for c in out["candidates"]]
    assert "c9" in ids
    assert "c5" not in ids, "textual duplicate of a frozen chunk must be excluded"
    assert "c1" not in ids and "c2" not in ids, "frozen chunks must be excluded"
    threshold = max(out["min_overlap"], out["best_frozen_overlap"] + out["margin"])
    assert all(c["overlap"] >= threshold for c in out["candidates"])
    assert len(ids) <= REF_FILL_K


def test_find_ref_fill_caps_at_k(tmp_path):
    deps = _fill_deps()
    for i in range(REF_FILL_K + 4):
        deps.payload[f"x{i}"] = {
            "chunk_text": C9 + f" Distinct filler sentence number {i} for cap testing.",
            "act_name": EPA,
            "section_number": str(100 + i),
        }
    deps.fam_sec_index, deps.fam_chunks = build_family_indexes(deps.payload, deps.fam_map)
    out = find_ref_fill("QFILL", deps)
    assert len(out["candidates"]) == REF_FILL_K


def test_build_fill_review_and_approval_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    monkeypatch.setattr(s, "load_queue", lambda deps: {"evidence_missing": ["QFILL"], "model_wrong": []})
    deps = _fill_deps()

    review = build_fill_review(deps)
    assert review["n_proposals"] == 1
    assert review["proposals"][0]["qid"] == "QFILL"
    assert review["proposals"][0]["approved"] is False

    write_approval({"QFILL"}, source="test")
    review2 = build_fill_review(deps)
    assert review2["proposals"][0]["approved"] is True
    assert review2["approved_qids"] == ["QFILL"]


# --------------------------------------------------------------------------- #
# Budget ledger
# --------------------------------------------------------------------------- #

def test_ledger_cap_enforced(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=2)
    assert led.can_spend()
    assert led.spend("QA", "evidence_missing") is True
    assert led.spend("QB", "model_wrong") is True
    assert led.spent == 2
    assert led.can_spend() is False
    assert led.spend("QC", "evidence_missing") is False
    assert led.spent == 2  # rejected spend is not counted


def test_ledger_reference_narrow_raises(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    with pytest.raises(RuntimeError):
        led.spend("QRN", "reference_narrow")
    assert led.spent == 0


def test_ledger_transport_failures_outside_cap(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=1)
    assert led.spend("QA", "evidence_missing") is True
    for _ in range(5):
        led.record_transport_failure("QB")
    assert led.spent == 1
    assert led.data["transport_failures"] == 5
    assert led.spend("QB", "model_wrong") is False  # cap still blocks


def test_ledger_persists_but_cap_never_taken_from_disk(tmp_path):
    path = tmp_path / "ledger.json"
    led = Ledger(path, cap=5)
    led.spend("QA", "evidence_missing")
    reloaded = Ledger(path, cap=BUDGET_CAP)
    assert reloaded.spent == 1
    assert reloaded.cap == BUDGET_CAP  # registered cap wins over any file content


# --------------------------------------------------------------------------- #
# _call_model accounting
# --------------------------------------------------------------------------- #

def test_call_model_transport_failures_never_spend(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    client = _FakeClient(error="connection reset")
    model, not_run = s._call_model(
        client=client, system="s", user="u", qid="QA", label="evidence_missing",
        ledger=led, calls_path=tmp_path / "calls.jsonl",
    )
    assert model is None and not_run == "transport_failure"
    assert client.calls == MAX_ATTEMPTS
    assert led.spent == 0
    assert led.data["transport_failures"] == MAX_ATTEMPTS


def test_call_model_parse_failure_is_a_received_generation(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    client = _FakeClient(text="this is not json at all")
    model, not_run = s._call_model(
        client=client, system="s", user="u", qid="QA", label="evidence_missing",
        ledger=led, calls_path=tmp_path / "calls.jsonl",
    )
    assert model is None and not_run == "parse_failure"
    assert client.calls == MAX_ATTEMPTS
    assert led.spent == MAX_ATTEMPTS  # received responses count against the cap


def test_call_model_stops_at_exhausted_budget(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=0)
    client = _FakeClient(payloads=[{"answer": "x", "cited_span": ""}])
    model, not_run = s._call_model(
        client=client, system="s", user="u", qid="QA", label="evidence_missing",
        ledger=led, calls_path=tmp_path / "calls.jsonl",
    )
    assert model is None and not_run == "budget_exhausted"
    assert client.calls == 0  # no call placed once the cap is reached


def test_call_model_success_logs_and_spends(tmp_path):
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    client = _FakeClient(payloads=[{"answer": "a full answer", "cited_span": SPAN}])
    model, not_run = s._call_model(
        client=client, system="s", user="u", qid="QA", label="evidence_missing",
        ledger=led, calls_path=tmp_path / "calls.jsonl",
    )
    assert not_run is None and model["answer"] == "a full answer"
    assert led.spent == 1
    log = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(log) == 1 and log[0]["success"] is True


# --------------------------------------------------------------------------- #
# evidence_missing full run
# --------------------------------------------------------------------------- #

def test_run_one_em_recovers_behind_gates(tmp_path):
    deps = _em_deps(
        d2={"QEM": D2_EM},
        widened={"QEM": {"add": EM_CONCL}},
        labels={"QEM": "evidence_missing"},
    )
    client = _FakeClient(
        payloads=[{"answer": EM_CONCL + " The State Board shall comply.", "cited_span": SPAN}]
    )
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_em("QEM", deps, client, led, tmp_path / "calls.jsonl")

    assert rec["status"] == "recovered"
    assert rec["binary_before"] is False and rec["binary_after"] is True
    assert rec["step1"]["keep"] is True
    assert rec["step2"]["pass"] is True
    assert rec["generation_calls"] == 1
    assert led.spent == 1 and client.calls == 1


def test_run_one_em_zero_call_gate_reject(tmp_path):
    deps = _em_deps(d2={"QEM": D2_EM})
    deps.questions["QEM"]._units = [_U("epa", "42")]
    client = _FakeClient(payloads=[{"answer": "should never be called"}])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_em("QEM", deps, client, led, tmp_path / "calls.jsonl")

    assert rec["status"] == "rejected"
    assert rec["reason"] == "gate:section_not_in_index_stop_do_not_loop_retrieval"
    assert rec["generation_calls"] == 0
    assert led.spent == 0 and client.calls == 0


def test_run_one_em_fill_pending_spends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    deps = _fill_deps()
    deps.d2_answers["QFILL"] = D2_EM
    client = _FakeClient(payloads=[{"answer": "never called", "cited_span": SPAN}])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_em("QFILL", deps, client, led, tmp_path / "calls.jsonl")

    assert rec["status"] == "not_run"
    assert rec["reason"] == "fill_review_pending"
    assert rec["generation_calls"] == 0
    assert led.spent == 0 and client.calls == 0


def test_run_one_em_binary_flat_is_unchanged(tmp_path):
    deps = _em_deps(d2={"QEM": D2_EM})
    # candidate scores below threshold -> gate rejects (binary did not rise)
    client = _FakeClient(payloads=[{"answer": "xyz unrelated answer.", "cited_span": SPAN}])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_em("QEM", deps, client, led, tmp_path / "calls.jsonl")
    assert rec["binary_after"] is False
    assert rec["step1"]["keep"] is False
    assert rec["status"] == "rejected"
    assert rec["reason"].startswith("gate:")


# --------------------------------------------------------------------------- #
# model_wrong full run
# --------------------------------------------------------------------------- #

def _chosen_option(deps, needle="Central Government may give directions"):
    opts = cut_contrastive_options("QMW", deps)
    assert opts, "contrastive options must be cut from the frozen context"
    return next(o for o in opts if needle in o["sentence"])


def test_cut_contrastive_options_from_frozen_only():
    deps = _mw_deps()
    opts = cut_contrastive_options("QMW", deps)
    assert 1 <= len(opts) <= 6
    assert all(o["chunk_id"] in {"c2", "c3"} for o in opts)
    assert any(SPAN[:-1] in o["sentence"] for o in opts)
    ids = [o["option_id"] for o in opts]
    assert ids == list(range(1, len(opts) + 1))


def test_run_one_mw_recovers_behind_quote_gate(tmp_path):
    deps = _mw_deps(d2={"QMW": D2_MW}, labels={"QMW": "model_wrong"})
    hit = _chosen_option(deps)
    cand = SPAN + " The remedy for contravention is a criminal penalty under section 15."
    client = _FakeClient(
        payloads=[
            {
                "chosen_option_id": hit["option_id"],
                "answer": cand,
                "cited_span": SPAN,
                "operative_rule": "directions",
                "authority": "central government",
                "remedy": "criminal penalty",
                "missing_element_if_any": None,
            }
        ]
    )
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_mw("QMW", deps, client, led, tmp_path / "calls.jsonl")

    assert rec["status"] == "recovered"
    assert rec["binary_before"] is False and rec["binary_after"] is True
    assert rec["step1"]["keep"] is True
    assert rec["step1_fields"]["cited_span_in_context"] is True
    assert rec["step1_fields"]["operative_sentence_changed"] is True
    assert rec["step1_fields"]["section_number_only_swap"] is False
    assert rec["step2"]["pass"] is True
    assert led.spent == 1


def test_run_one_mw_section_number_swap_rejected(tmp_path):
    deps = _mw_deps(
        d2={"QSWAP": "Under section 7 of the Act, " + SPAN},
        labels={"QSWAP": "model_wrong"},
    )
    deps.questions["QSWAP"] = deps.questions.pop("QMW")
    deps.manifest["QSWAP"] = deps.manifest.pop("QMW")
    opts = cut_contrastive_options("QSWAP", deps)
    hit = next(o for o in opts if "Central Government may give directions" in o["sentence"])
    cand = "Under section 15 of the Act, " + SPAN
    client = _FakeClient(
        payloads=[{"chosen_option_id": hit["option_id"], "answer": cand, "cited_span": SPAN}]
    )
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_mw("QSWAP", deps, client, led, tmp_path / "calls.jsonl")

    assert rec["status"] == "rejected"
    assert rec["reason"] == "gate:section_number_only_swap"
    assert rec["step1_fields"]["section_number_only_swap"] is True


def test_run_one_mw_span_not_in_context_rejected(tmp_path):
    deps = _mw_deps(d2={"QMW": D2_MW})
    _chosen_option(deps)  # options exist; the invented span still fails the gate
    invented = (
        "The Central Government shall always obtain prior judicial approval "
        "before issuing any direction to a State Government."
    )
    client = _FakeClient(
        payloads=[{"chosen_option_id": 1, "answer": SPAN + " More.", "cited_span": invented}]
    )
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_mw("QMW", deps, client, led, tmp_path / "calls.jsonl")
    assert rec["status"] == "rejected"
    assert rec["reason"] == "gate:cited_span_not_verbatim_substring_of_context"
    assert rec["step1_fields"]["cited_span_in_context"] is False


def test_run_one_mw_abstain_on_absent_element_rejected(tmp_path):
    deps = _mw_deps(d2={"QMW": D2_MW})
    hit = _chosen_option(deps)
    client = _FakeClient(
        payloads=[
            {
                "chosen_option_id": hit["option_id"],
                "answer": "I cannot answer this question from the given corpus.",
                "cited_span": SPAN,
                "missing_element_if_any": "penalty for lateral entry",
            }
        ]
    )
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_mw("QMW", deps, client, led, tmp_path / "calls.jsonl")
    assert rec["status"] == "rejected"
    assert rec["reason"] == "gate:abstains_on_element_absent_from_new_payload"


def test_run_one_mw_no_options_is_not_run(tmp_path):
    deps = _mw_deps()
    deps.manifest["QMW"]["conditions"]["O3_full_support"]["context_chunk_ids"] = []
    client = _FakeClient(payloads=[{"answer": "never", "cited_span": SPAN}])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_mw("QMW", deps, client, led, tmp_path / "calls.jsonl")
    assert rec["status"] == "not_run" and rec["reason"] == "no_contrastive_span"
    assert led.spent == 0 and client.calls == 0


# --------------------------------------------------------------------------- #
# Field derivations
# --------------------------------------------------------------------------- #

def test_section_number_only_swap_detection():
    a = "Under section 7 of the Act the Board shall decide."
    b = "Under section 15 of the Act the Board shall decide."
    assert section_number_only_swap(a, b) is True
    assert section_number_only_swap(a, a.replace("Board", "Authority")) is False


def test_score_frozen_abstention_credit_only_on_ie():
    """Before/after scoring shares the evaluator-v2 abstention rule.

    No queued qid is insufficient_evidence today, so this is a consistency
    guarantee: the same answer must score identically on both sides, and the
    credit must never fire for a non-IE question.
    """
    abstainy = (
        "The exact annual licence fee for a State food licence cannot be "
        "determined from the provided evidence under the FSS Act fee rules."
    )
    refs = ["The available corpus does not establish the exact annual licence fee."]

    credited = s.score_frozen(abstainy, refs, set(), True)
    plain = s.score_frozen(abstainy, refs, set(), False)
    default = s.score_frozen(abstainy, refs, set())
    assert credited["correct"] is True          # IE + negation -> credit
    assert credited["soft"] <= 0.5              # credit is binary-only
    assert plain["correct"] is False            # non-IE: no credit
    assert default["correct"] is False          # default flag is off
    assert plain["soft"] == credited["soft"]    # soft never moves
    # the reported abstain flag keeps FROZEN v1 lexicon semantics (v1 regex
    # misses "cannot be determined" — that is why credit needed its own rule)
    assert credited["abstained"] == plain["abstained"] is False


def test_derive_mw_fields_flags():
    opts = [
        {"option_id": 1, "sentence": SPAN},
        {"option_id": 2, "sentence": C2.split(". ")[1]},
    ]
    fields = derive_mw_fields(
        model={"chosen_option_id": 1, "cited_span": SPAN, "missing_element_if_any": None},
        options=opts,
        context="EVIDENCE " + SPAN,
        d2_answer=D2_MW,
        candidate=SPAN + " Remedy follows.",
    )
    assert fields["cited_span_in_context"] is True
    assert fields["operative_sentence_changed"] is True
    assert fields["section_number_only_swap"] is False
    assert fields["abstains_on_element_absent_from_payload"] is False
    assert fields["_chosen_valid"] is True

    fields2 = derive_mw_fields(
        model={"chosen_option_id": 99, "cited_span": SPAN, "missing_element_if_any": None},
        options=opts,
        context="EVIDENCE " + SPAN,
        d2_answer=D2_MW,
        candidate=SPAN,
    )
    assert fields2["_chosen_valid"] is False
    assert fields2["operative_sentence_changed"] is False


def test_derive_step2_fields_full_replacement_only_when_quote_fails():
    # conclusion changed + valid quote -> gated rewrite, NOT a full replacement
    f1 = derive_step2_fields(
        d2_answer=D2_EM,
        candidate=SPAN + " New remedy.",
        binary_before=False,
        binary_after=True,
        cited_span=SPAN,
        context="ctx " + SPAN,
        step1_kept=True,
    )
    assert f1["is_full_answer_replacement"] is False
    assert f1["new_subsection_quoted_from_evidence"] is True
    assert f1["conclusion_fields_changed"] == ["operative_rule", "authority", "remedy"]
    assert f1["is_open_critic"] is False

    # conclusion changed but quote not in context -> unconstrained replacement
    f2 = derive_step2_fields(
        d2_answer=D2_EM,
        candidate=SPAN + " New remedy.",
        binary_before=False,
        binary_after=True,
        cited_span="some invented sentence",
        context="ctx " + SPAN,
        step1_kept=True,
    )
    assert f2["is_full_answer_replacement"] is True

    # conclusion retained -> no fields changed
    f3 = derive_step2_fields(
        d2_answer=D2_EM,
        candidate=D2_EM + " Notes follow here.",
        binary_before=False,
        binary_after=True,
        cited_span=SPAN,
        context="ctx " + SPAN,
        step1_kept=True,
    )
    assert f3["conclusion_fields_changed"] == []
    assert f3["is_full_answer_replacement"] is False


def test_derive_status_precedence():
    assert derive_status(
        step1_keep=False, step1_reason="x", step2_pass=None, step2_reason=None,
        binary_before=False, binary_after=True,
    ) == ("rejected", "gate:x")
    assert derive_status(
        step1_keep=True, step1_reason="x", step2_pass=False, step2_reason="s",
        binary_before=False, binary_after=True,
    ) == ("rejected", "safety:s")
    assert derive_status(
        step1_keep=True, step1_reason="x", step2_pass=True, step2_reason=None,
        binary_before=False, binary_after=True,
    )[0] == "recovered"
    assert derive_status(
        step1_keep=True, step1_reason="x", step2_pass=True, step2_reason=None,
        binary_before=True, binary_after=True,
    ) == ("unchanged", "already_correct_before")
    assert derive_status(
        step1_keep=True, step1_reason="x", step2_pass=True, step2_reason=None,
        binary_before=False, binary_after=False,
    ) == ("unchanged", "kept_no_binary_flip")
    assert derive_status(
        step1_keep=True, step1_reason="x", step2_pass=None, step2_reason=None,
        binary_before=False, binary_after=False, not_run="budget_exhausted",
    ) == ("not_run", "budget_exhausted")


# --------------------------------------------------------------------------- #
# Preflight / queue
# --------------------------------------------------------------------------- #

def test_preflight_plan_hygiene(monkeypatch):
    deps = _em_deps(d2={"QEM": D2_EM})
    monkeypatch.setattr(
        s, "load_queue",
        lambda d: {"evidence_missing": ["QEM"], "model_wrong": ["QMW"]},
    )
    plan = preflight(deps)  # QMW is not in deps.questions -> question_missing path
    assert plan["reference_narrow_queued"] == 0
    assert plan["projected_spend"] == plan["n_eligible_for_generation"]
    assert plan["budget_cap"] == BUDGET_CAP
    assert plan["within_budget"] is True
    assert plan["queue"] == {"evidence_missing": 1, "model_wrong": 1}
    assert len(plan["records"]) == 2
    for r in plan["records"]:
        assert "context" not in r
        assert "new_payload_ids" not in r
        assert r["verdict"] in {"eligible", "rejected", "not_run"}


def test_rn_never_queued_real_assignment():
    """Registered assignment: reference_narrow must never reach generation."""
    path = ROOT / "evaluation" / "out" / "ceiling_v5" / "step1_qid_assignment.json"
    if not path.exists():
        pytest.skip("step1_qid_assignment.json not present")
    buckets = json.loads(path.read_text(encoding="utf-8")).get("buckets") or {}
    em = set((buckets.get("evidence_missing") or {}).get("qids") or [])
    mw = set((buckets.get("model_wrong") or {}).get("qids") or [])
    rn = set((buckets.get("reference_narrow") or {}).get("qids") or [])
    assert em and mw and rn
    assert not (em | mw) & rn
    assert len(em | mw | rn) == len(em) + len(mw) + len(rn)


# --------------------------------------------------------------------------- #
# Execute: fold + resume
# --------------------------------------------------------------------------- #

def _em_payload_for(qid: str):
    return {"answer": EM_CONCL + " The State Board shall comply.", "cited_span": SPAN}


def _qc_payload_for(qid: str):
    """QC's reference is C9 itself, so the candidate must track C9."""
    return {
        "answer": C9 + " The prescribed authority must be informed in writing.",
        "cited_span": C9.split(" Nothing else")[0],
    }


def test_execute_folds_and_resumes_past_fill_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    deps = _mk_deps(
        {
            "QA": _Q("Which provision empowers directions?", EM_CONCL, [_U("epa", "7")]),
            "QB": _Q("Which provision empowers directions?", EM_CONCL, [_U("epa", "42")]),
            "QC": _Q("What must a person do?", C9, [_U("epa", "7")]),
        },
        _payload(),
        {"QA": ["c2"], "QB": ["c2"], "QC": ["c1", "c2"]},
        d2={"QA": D2_EM, "QB": D2_EM, "QC": D2_EM},
        labels={"QA": "evidence_missing", "QB": "evidence_missing", "QC": "evidence_missing"},
    )
    plan1 = {
        "records": [
            {"qid": "QA", "label": "evidence_missing", "verdict": "eligible",
             "reason": "text_added_to_payload"},
            {"qid": "QB", "label": "evidence_missing", "verdict": "rejected",
             "reason": "section_not_in_index_stop_do_not_loop_retrieval"},
            {"qid": "QC", "label": "evidence_missing", "verdict": "not_run",
             "reason": "fill_review_pending"},
        ]
    }
    out = tmp_path / "candidates.jsonl"
    calls = tmp_path / "calls.jsonl"
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)

    r1 = execute(
        deps=deps, client=_FakeClient(payloads=[_em_payload_for("QA")]), ledger=led,
        plan=plan1, out_jsonl=out, calls_path=calls,
    )
    assert r1["n_pending"] == 1 and r1["spent"] == 1
    ck = s.load_checkpoint(out)
    assert set(ck) == {"QA", "QB", "QC"}
    assert ck["QA"]["status"] == "recovered"
    assert ck["QB"]["status"] == "rejected"
    assert ck["QB"]["reason"].startswith("gate:")
    assert ck["QC"]["status"] == "not_run"
    assert ck["QC"]["reason"] == "fill_review_pending"

    # Resume WITHOUT approval: QC must not be retried (still pending review).
    r2 = execute(
        deps=deps, client=_FakeClient(), ledger=led, plan=plan1,
        out_jsonl=out, calls_path=calls,
    )
    assert r2["n_pending"] == 0

    # Human approves -> new plan marks QC eligible -> resume processes it.
    write_approval({"QC"}, source="test")
    plan2 = {
        "records": [
            {"qid": "QA", "label": "evidence_missing", "verdict": "eligible",
             "reason": "text_added_to_payload"},
            {"qid": "QB", "label": "evidence_missing", "verdict": "rejected",
             "reason": "section_not_in_index_stop_do_not_loop_retrieval"},
            {"qid": "QC", "label": "evidence_missing", "verdict": "eligible",
             "reason": "fill_approved"},
        ]
    }
    client2 = _FakeClient(payloads=[_qc_payload_for("QC")])
    r3 = execute(
        deps=deps, client=client2, ledger=led, plan=plan2,
        out_jsonl=out, calls_path=calls,
    )
    assert r3["n_pending"] == 1, "approved fill qid must be processed on resume"
    assert client2.calls == 1
    ck2 = s.load_checkpoint(out)
    assert ck2["QC"]["status"] == "recovered"
    assert ck2["QA"]["status"] == "recovered", "already-done qid must not rerun"
    assert led.spent == 2


def test_execute_fresh_discards_checkpoint(tmp_path):
    deps = _mk_deps(
        {"QA": _Q("Which provision empowers directions?", EM_CONCL, [_U("epa", "7")])},
        _payload(),
        {"QA": ["c2"]},
        d2={"QA": D2_EM},
    )
    plan = {"records": [{"qid": "QA", "label": "evidence_missing",
                         "verdict": "eligible", "reason": "text_added_to_payload"}]}
    out = tmp_path / "candidates.jsonl"
    out.write_text('{"qid": "QA", "status": "recovered", "reason": "old"}\n')
    client = _FakeClient(payloads=[_em_payload_for("QA")])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    res = execute(
        deps=deps, client=client, ledger=led, plan=plan,
        out_jsonl=out, calls_path=tmp_path / "calls.jsonl", fresh=True,
    )
    assert res["n_pending"] == 1
    ck = s.load_checkpoint(out)
    assert ck["QA"]["reason"] != "old"


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def _report_deps(tmp_path, monkeypatch, rn_qids=("R1", "R2")):
    outdir = tmp_path / "out"
    outdir.mkdir(exist_ok=True)
    (outdir / "step1_qid_assignment.json").write_text(
        json.dumps({"buckets": {"reference_narrow": {"qids": list(rn_qids)}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(s, "OUT", outdir)
    return Deps(
        questions={}, payload={}, manifest={}, d2_answers={}, widened={},
        banned=set(), fam_map=FamilyMap(), labels={},
    )


def test_build_report_counts_before_aggregates(tmp_path, monkeypatch):
    deps = _report_deps(tmp_path, monkeypatch)
    ck = {
        "QA": {"qid": "QA", "label": "evidence_missing", "status": "recovered",
               "reason": "binary_rose_0_to_1", "binary_before": False,
               "binary_after": True, "soft_before": 0.3, "soft_after": 0.8,
               "step1": {"keep": True},
               "step2": {"pass": True, "fields": {"is_full_answer_replacement": False}}},
        "QB": {"qid": "QB", "label": "model_wrong", "status": "rejected",
               "reason": "gate:section_number_only_swap",
               "binary_before": False, "binary_after": False,
               "soft_before": 0.2, "soft_after": 0.2,
               "step1": {"keep": False, "reason": "section_number_only_swap"}},
        "QC": {"qid": "QC", "label": "evidence_missing", "status": "not_run",
               "reason": "fill_review_pending"},
    }
    report = build_report(ck, deps)

    # 1. per-label counts are published before the aggregate soft scores
    keys = list(report)
    assert keys.index("counts") < keys.index("soft_scores")
    assert report["counts"]["evidence_missing"] == {
        "recovered": 1, "rejected": 0, "unchanged": 0, "not_run": 1,
    }
    assert report["counts"]["model_wrong"]["rejected"] == 1
    assert report["n_queued_residual"] == 3

    # registered-reason rollups
    assert report["gate_rejections"]["section_number_only_swap"] == 1
    assert report["reasons"]["evidence_missing"]["fill_review_pending"] == 1

    # reference_narrow: never queued, zero spend, share reported
    assert report["reference_narrow"]["n"] == 2
    assert report["reference_narrow"]["generation_calls_spent"] == 0
    assert report["reference_narrow"]["share_of_residual_sample"] == 2 / 5
    assert "budget_violation" not in report

    # 3. aggregates exist after counts
    assert report["soft_scores"]["evidence_missing"]["soft_after_mean"] == 0.8

    # decision rules: em recovered, mw did not -> r1 fires (corpus bottleneck)
    flags = {r["id"]: r["fires"] for r in report["decision_rules"]}
    assert flags["5.3-r1"] is True
    assert flags["5.3-r2"] is False
    assert flags["5.3-r3"] is False
    assert flags["5.3-r4"] is False

    md = render_report_md(report)
    assert "recovered" in md and "Decision rules" in md


def test_build_report_rule3_when_both_flat_and_rn_large(tmp_path, monkeypatch):
    deps = _report_deps(tmp_path, monkeypatch, rn_qids=("R1", "R2", "R3"))
    ck = {
        f"Q{i}": {"qid": f"Q{i}", "label": "model_wrong", "status": "unchanged",
                  "reason": "kept_no_binary_flip", "binary_before": False,
                  "binary_after": False}
        for i in range(3)
    }
    report = build_report(ck, deps)
    flags = {r["id"]: r["fires"] for r in report["decision_rules"]}
    assert flags["5.3-r1"] is False
    assert flags["5.3-r3"] is True  # both flat + RN is 3/6 = 50% of the sample
    assert report["reference_narrow"]["share_of_residual_sample"] == 0.5


def test_build_report_withholds_rules_on_partial_run(tmp_path, monkeypatch):
    """§5.3 recovery rules must not fire before every queued qid is tabulated.

    Regression: rn_share used to divide by records-tabulated-so-far, so a
    1-record run reported reference_narrow as ~97% of "the sample" and rule 3
    fired before the run had barely started.
    """
    outdir = tmp_path / "out"
    outdir.mkdir(exist_ok=True)
    (outdir / "step1_qid_assignment.json").write_text(
        json.dumps({"buckets": {
            "evidence_missing": {"qids": ["QA", "QB"]},
            "model_wrong": {"qids": ["QC"]},
            "reference_narrow": {"qids": ["R1", "R2", "R3"]},
        }}),
        encoding="utf-8",
    )
    monkeypatch.setattr(s, "OUT", outdir)
    deps = Deps(
        questions={}, payload={}, manifest={}, d2_answers={}, widened={},
        banned=set(), fam_map=FamilyMap(), labels={},
    )
    # only 1 of 3 queued qids tabulated, both flat -> partial state
    ck = {
        "QA": {"qid": "QA", "label": "model_wrong", "status": "unchanged",
               "reason": "kept_no_binary_flip", "binary_before": False,
               "binary_after": False},
    }
    report = build_report(ck, deps)
    assert report["run_complete"] is False
    assert report["n_missing_queued"] == 2
    assert set(report["missing_queued_qids"]) == {"QB", "QC"}
    # share is stable (3/6 of the REGISTERED sample — labels absent, so the
    # assignment buckets are the registered sample), never inflated by the
    # partial tabulation
    assert report["reference_narrow"]["share_of_residual_sample"] == 0.5
    for r in report["decision_rules"]:
        if r["id"] == "5.3-r4":
            continue  # observed defect, ungated
        assert r["blocked_by_incomplete_run"] is True
        assert r["fires"] is False, f"{r['id']} fired on a partial run"
    md = render_report_md(report)
    assert "incomplete" in md and "withheld" in md

    # once the remaining qids are tabulated, the rules become eligible again
    ck["QB"] = {"qid": "QB", "label": "model_wrong", "status": "unchanged",
                "reason": "kept_no_binary_flip", "binary_before": False,
                "binary_after": False}
    ck["QC"] = {"qid": "QC", "label": "model_wrong", "status": "unchanged",
                "reason": "kept_no_binary_flip", "binary_before": False,
                "binary_after": False}
    done = build_report(ck, deps)
    assert done["run_complete"] is True
    r3 = next(r for r in done["decision_rules"] if r["id"] == "5.3-r3")
    assert r3["blocked_by_incomplete_run"] is False
    assert r3["fires"] is True


def test_build_report_counts_registered_sample_for_share(tmp_path, monkeypatch):
    """rn_share uses the registered residual set (step0 labels), not the checkpoint."""
    outdir = tmp_path / "out"
    outdir.mkdir(exist_ok=True)
    (outdir / "step1_qid_assignment.json").write_text(
        json.dumps({"buckets": {
            "evidence_missing": {"qids": ["QA"]},
            "reference_narrow": {"qids": ["R1", "R2"]},
        }}),
        encoding="utf-8",
    )
    monkeypatch.setattr(s, "OUT", outdir)
    deps = Deps(
        questions={}, payload={}, manifest={}, d2_answers={}, widened={},
        banned=set(), fam_map=FamilyMap(),
        # 6 registered residual qids: 4 EM + 2 RN
        labels={"QA": "evidence_missing", "QB": "evidence_missing",
                "QC": "evidence_missing", "QD": "evidence_missing",
                "R1": "reference_narrow", "R2": "reference_narrow"},
    )
    report = build_report(
        {"QA": {"qid": "QA", "label": "evidence_missing", "status": "recovered",
                "reason": "binary_rose_0_to_1", "binary_before": False,
                "binary_after": True}},
        deps,
    )
    assert report["registered_residual_sample"] == 6
    assert report["reference_narrow"]["share_of_residual_sample"] == round(2 / 6, 4)
    assert report["run_complete"] is False  # QB..QD not tabulated


def test_build_report_rule4_and_rn_budget_violation(tmp_path, monkeypatch):
    deps = _report_deps(tmp_path, monkeypatch)
    ck = {
        "QA": {"qid": "QA", "label": "model_wrong", "status": "recovered",
               "reason": "binary_rose_0_to_1", "binary_before": False,
               "binary_after": True,
               "step2": {"pass": True,
                         "fields": {"is_full_answer_replacement": True}}},
        "R1": {"qid": "R1", "label": "reference_narrow", "status": "unchanged",
               "reason": "kept_no_binary_flip"},
    }
    report = build_report(ck, deps)
    flags = {r["id"]: r["fires"] for r in report["decision_rules"]}
    assert flags["5.3-r4"] is True
    assert report["safety_invariants"]["unconstrained_full_answer_replacements_kept"] == 1
    # an RN qid inside the checkpoint is a hard budget violation
    assert report["reference_narrow"]["generation_calls_spent"] == -1
    assert report["budget_violation"]


def test_build_report_counts_zero_regression(tmp_path, monkeypatch):
    deps = _report_deps(tmp_path, monkeypatch)
    ck = {
        "QA": {"qid": "QA", "label": "evidence_missing", "status": "unchanged",
               "reason": "kept_no_binary_flip", "binary_before": True,
               "binary_after": False},
    }
    report = build_report(ck, deps)
    assert report["safety_invariants"]["already_correct_regressed"] == 1


# --------------------------------------------------------------------------- #
# Integration against the registered artifacts (read-only)
# --------------------------------------------------------------------------- #

def test_real_preflight_invariants():
    if not (ROOT / "evaluation" / "out" / "ceiling_v5" / "step1_qid_assignment.json").exists():
        pytest.skip("step1_qid_assignment.json not present")
    deps = s.load_deps(with_labels=True)
    plan = preflight(deps)

    assert plan["registrations"]["ok"] is True
    assert plan["reference_narrow_queued"] == 0
    assert sum(plan["queue"].values()) == 89
    assert plan["projected_spend"] == plan["n_eligible_for_generation"] <= BUDGET_CAP

    known_not_run = {
        "no_new_text_added", "fill_review_pending", "fill_rejected_by_human",
        "fill_rejected_by_gate", "fill_gate_not_pass",
        "no_contrastive_span", "question_missing",
    }
    known_rejected = {
        "section_not_in_index_stop_do_not_loop_retrieval",
        "gold_span_still_absent_from_payload",
    }
    for r in plan["records"]:
        assert "context" not in r and "new_payload_ids" not in r
        assert r["label"] in {"evidence_missing", "model_wrong"}
        if r["verdict"] == "rejected":
            assert r["reason"] in known_rejected
        elif r["verdict"] == "not_run":
            assert r["reason"] in known_not_run
        if r["reason"] == "fill_review_pending":
            assert r["proposed_fill"]["candidates"], "pending implies a proposal"


def test_fill_rejected_by_human_spends_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    monkeypatch.setattr(
        s, "load_queue", lambda d: {"evidence_missing": ["QFILL"], "model_wrong": []}
    )
    deps = _fill_deps()
    s.write_approval(set(), source="test", rejected_qids={"QFILL"})

    rec = em_precondition("QFILL", deps)
    assert rec["verdict"] == "not_run"
    assert rec["reason"] == "fill_rejected_by_human"
    assert rec["proposed_fill"]["candidates"], "proposal still recorded for audit"

    # zero-call end-to-end: no model call, no ledger spend
    client = _FakeClient(payloads=[{"answer": "never called"}])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec2 = run_one_em("QFILL", deps, client, led, tmp_path / "calls.jsonl")
    assert rec2["status"] == "not_run" and rec2["reason"] == "fill_rejected_by_human"
    assert led.spent == 0 and client.calls == 0

    # review payload reports the rejection
    review = s.build_fill_review(deps)
    assert review["rejected_qids"] == ["QFILL"]
    assert review["proposals"][0]["decision"] == "rejected"

    # latest decision wins: a later explicit approval un-rejects
    s.write_approval({"QFILL"}, source="test")
    assert s.load_fill_approval() == {"QFILL"}
    assert em_precondition("QFILL", deps)["verdict"] == "eligible"


def test_load_fill_decisions_csv_statuses(tmp_path):
    p = tmp_path / "decisions.csv"
    p.write_text(
        "\ufeffQID,Final Status,Validation / Truthfulness Finding\n"
        "Q052,Manual verification,needs a human look\n"
        "Q058,REJECT,metadata artifact\n"
        "Q072,rejected,Corporation: fragment\n"
        "Q098,APPROVED,strong match\n"
        "Q114, approved ,generic tokens\n",
        encoding="utf-8",
    )
    approved, rejected, findings, n_rows = s.load_fill_decisions_csv(p)
    assert approved == {"Q098", "Q114"}
    assert rejected == {"Q058", "Q072"}
    assert n_rows == 5
    # undecided rows still record their finding for the audit trail
    assert findings["Q052"] == "needs a human look"
    assert findings["Q058"] == "metadata artifact"
    assert "Q114" not in rejected


def test_load_fill_decisions_csv_requires_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        s.load_fill_decisions_csv(p)


def test_gate_reject_blocks_approval_and_eligibility(tmp_path, monkeypatch):
    """Machine gate REJECT is absolute: CLI refuses, em_precondition refuses."""
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    deps = _fill_deps()
    # force the gate to REJECT for this qid
    real_gate = s.gate_fill_proposal
    monkeypatch.setattr(
        s, "gate_fill_proposal",
        lambda gates: {**real_gate(gates), "decision": "REJECT",
                       "reason_codes": ["METADATA_FRAGMENT"]},
    )

    # gate REJECT outranks the pending state: nothing to review, it is dead
    rec = em_precondition("QFILL", deps)
    assert rec["reason"] == "fill_rejected_by_gate"

    # and a forced approval (bypassing the CLI refusal) cannot revive it
    s.write_approval({"QFILL"}, source="test")
    rec2 = em_precondition("QFILL", deps)
    assert rec2["verdict"] == "not_run"
    assert rec2["reason"] == "fill_rejected_by_gate"
    assert rec2["fill_gate"]["decision"] == "REJECT"

    # CLI refuses the approval outright (exit 2, nothing written)
    monkeypatch.setattr(s, "load_deps", lambda **kw: deps)
    monkeypatch.setattr(
        s, "load_queue", lambda d: {"evidence_missing": ["QFILL"], "model_wrong": []}
    )
    rc = s.main(["--approve-fills", "QFILL"])
    assert rc == 2


def test_approved_review_gate_still_never_generates(tmp_path, monkeypatch):
    """Plan sec 11: generation only for PASS proposals, even when approved."""
    monkeypatch.setattr(s, "FILL_APPROVAL_JSON", tmp_path / "approval.json")
    deps = _fill_deps()
    # force gate REVIEW (the calibration outcome for the 3 manual rows)
    real_gate = s.gate_fill_proposal
    monkeypatch.setattr(
        s, "gate_fill_proposal",
        lambda gates: {**real_gate(gates), "decision": "REVIEW",
                       "reason_codes": ["WEAK_SEMANTIC_ANCHOR"]},
    )
    s.write_approval({"QFILL"}, source="test")

    client = _FakeClient(payloads=[{"answer": "never called"}])
    led = Ledger(tmp_path / "ledger.json", cap=BUDGET_CAP)
    rec = run_one_em("QFILL", deps, client, led, tmp_path / "calls.jsonl")
    assert rec["status"] == "not_run"
    assert rec["reason"] == "fill_gate_not_pass"
    assert led.spent == 0 and client.calls == 0


def test_plan_artifacts_written_by_preflight_are_hygienic(tmp_path, monkeypatch):
    """--preflight output JSON must not embed contexts (artifact hygiene)."""
    deps = _em_deps(d2={"QEM": D2_EM})
    monkeypatch.setattr(s, "load_queue", lambda d: {"evidence_missing": ["QEM"], "model_wrong": []})
    plan = preflight(deps)
    blob = json.dumps(plan, default=str)
    assert C1 not in blob, "frozen chunk text must not leak into the plan artifact"
    rec = next(r for r in plan["records"] if r["qid"] == "QEM")
    assert rec["verdict"] == "eligible"
    assert "context" not in rec and "new_payload_ids" not in rec
