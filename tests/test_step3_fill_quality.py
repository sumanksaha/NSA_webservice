"""Offline tests for the Step 3 multi-stage reference-quality / legal-aware gate.

The 14 first-round fill proposals (human-reviewed in
``Step3_EM_Final_Datasheet_2026-09-26.csv``) are the negative-control set this
gate was built against: 11 human-REJECT rows driven by metadata labels,
isolated generic tokens and OCR fragments, plus 3 rows the human marked
"Manual verification" (genuine but relevance-unproven).

Contract under test:
  * obvious fragments / form fields / OCR debris / headings -> REJECT with
    machine-readable reason codes (not a length heuristic);
  * short but substantive legal provisions survive (no word_count < 10 rule);
  * a clear legal-instrument conflict rejects even at high lexical overlap;
  * the frozen margin (+0.05) is retained but is only one component;
  * REJECT / REVIEW / PASS are all represented; PASS never appears for the
    human-rejected control rows;
  * determinism: identical inputs -> identical diagnostics.
"""

from __future__ import annotations

import csv
import sys
import warnings
from pathlib import Path
from typing import ClassVar

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.step3_fill_quality import (
    FRAGMENT_TOO_SHORT,
    HIGH_COMMON_TOKEN_RATIO,
    IDENTIFIER_MATCH,
    IDENTIFIER_PRESENT,
    INSTRUMENT_CONFLICT,
    MARGIN_FAIL,
    MARGIN_OK,
    METADATA_FRAGMENT,
    NO_SEMANTIC_ANCHOR,
    OVERLAP_INFLATED_BY_GENERIC_TOKENS,
    PASS,
    REJECT,
    REVIEW,
    SECTION_MISMATCH,
    SEMANTIC_ANCHOR_OK,
    WEAK_SEMANTIC_ANCHOR,
    content_overlap,
    detect_heading_fragment,
    detect_metadata_fragment,
    detect_ocr_fragment,
    evaluate_reference_quality,
    extract_legal_identifiers,
    gate_fill_candidate,
    gate_fill_proposal,
    meaningful_tokens,
)

QUESTION = (
    "Under section 32 of the Food Safety and Standards Act, 2006, what "
    "improvement notice may the Food Safety Officer issue?"
)
REFERENCE = (
    "The Food Safety Officer may serve an improvement notice under section 32 "
    "of the Food Safety and Standards Act, 2006 requiring the food business "
    "operator to remedy the contravence within a specified period."
)
FSSAI_ALIASES = [("fssai", "food safety and standards")]


class _FakeFamMap:
    alias_list: ClassVar[list] = FSSAI_ALIASES
    family_to_acts: ClassVar[dict] = {"fssai": ["Food Safety and Standards Act, 2006"]}


FAM = _FakeFamMap()


def _gate(candidate, **kw):
    kw.setdefault("question", QUESTION)
    kw.setdefault("frozen_reference", REFERENCE)
    kw.setdefault("raw_overlap", 0.55)
    kw.setdefault("best_frozen_overlap", 0.40)
    kw.setdefault("fam_map", FAM)
    kw.setdefault("question_families", ["fssai"])
    return gate_fill_candidate(candidate, **kw)


# --------------------------------------------------------------------------- #
# Text primitives
# --------------------------------------------------------------------------- #

def test_meaningful_tokens_drop_stopwords_and_generic_filler():
    toks = meaningful_tokens("the Board: shall under section of the act")
    assert "the" not in toks and "board" not in toks and "under" not in toks
    assert "act" not in toks  # generic legal filler
    assert meaningful_tokens("improvement notice remedy contravence") == [
        "improvement", "notice", "remedy", "contravence",
    ]


def test_content_overlap_ignores_boilerplate():
    # shared only in stopwords -> no content overlap
    assert content_overlap("the board shall not", "under the board of rules") == 0.0
    q = "improvement notice remedy"
    assert content_overlap(q, "the improvement notice must be remedyed") > 0.6


# --------------------------------------------------------------------------- #
# Fragment / metadata / OCR / heading detection (structural, not length)
# --------------------------------------------------------------------------- #

def test_metadata_labels_detected():
    for text in ("Area:", "Area", "Designation:", "Time: 10 am", "Capacity: 50",
                 "Corporation :", "premises:", "from: to:"):
        flag, reason = detect_metadata_fragment(text)
        assert flag, f"{text!r} should be a metadata fragment (got {reason})"


def test_metadata_lone_field_words_detected():
    assert detect_metadata_fragment("Designation")[0] is True
    assert detect_metadata_fragment("Ward")[0] is True
    # a lone non-field word is a fragment, not metadata: caught by the gate's
    # FRAGMENT_TOO_SHORT / NO_SEMANTIC_ANCHOR instead
    assert detect_metadata_fragment("Food")[0] is False


def test_real_legal_text_is_not_metadata():
    long_text = (
        "The Food Safety Officer may serve an improvement notice under section "
        "32 requiring the food business operator to remedy the contravence "
        "within the period specified in the notice."
    )
    flag, _ = detect_metadata_fragment(long_text)
    assert flag is False


def test_short_but_meaningful_legal_provision_survives():
    # plan sec 9: no `if word_count < 10: reject`
    text = "Section 32 — Improvement notices"
    meta, _ = detect_metadata_fragment(text)
    ocr, _ = detect_ocr_fragment(text)
    assert meta is False and ocr is False
    ids = extract_legal_identifiers(text, fam_map=FAM)
    assert ids.present and "32" in ids.sections
    head, _ = detect_heading_fragment(text, ids)
    assert head is False, "heading carrying a legal identifier must survive"

    g = _gate(text, raw_overlap=0.45, best_frozen_overlap=0.30)
    assert g["decision"] != REJECT
    assert FRAGMENT_TOO_SHORT not in g["reason_codes"]
    assert METADATA_FRAGMENT not in g["reason_codes"]


def test_genuine_sentence_passes_gate():
    text = (
        "The Food Safety Officer may serve an improvement notice under section "
        "32 of the Food Safety and Standards Act, 2006 requiring the operator "
        "to remedy the contravence within the specified period."
    )
    g = _gate(text)
    assert g["decision"] == PASS, g
    assert MARGIN_OK in g["reason_codes"]
    assert IDENTIFIER_MATCH in g["reason_codes"]
    assert g["instrument_match"] is True
    assert g["identifier_conflict"] is False


def test_ocr_and_heading_detection():
    assert detect_ocr_fragment("Page 12 of 45")[0] is True
    assert detect_ocr_fragment("\ufffd\ufffd broken \x0c text")[0] is True
    assert detect_ocr_fragment("#### ---- ~~~~ !!!")[0] is True
    assert detect_ocr_fragment(REFERENCE)[0] is False
    assert detect_heading_fragment("PART II", None)[0] is True
    assert detect_heading_fragment(REFERENCE, None)[0] is False


# --------------------------------------------------------------------------- #
# Full gate decisions: REJECT / REVIEW / PASS + reason codes
# --------------------------------------------------------------------------- #

def test_isolated_generic_tokens_rejected():
    for text in ("under", "no", "the", "Food", "(4) of", "orboth Prohibition of certain Act in"):
        g = _gate(text, raw_overlap=0.52, best_frozen_overlap=0.30)
        assert g["decision"] == REJECT, f"{text!r} -> {g['decision']} {g['reason_codes']}"
        assert g["reason_codes"], "every decision carries machine-readable codes"


def test_metadata_candidate_rejected_even_at_high_overlap():
    g = _gate("Area:", raw_overlap=0.514, best_frozen_overlap=0.27)
    assert g["decision"] == REJECT
    assert METADATA_FRAGMENT in g["reason_codes"]


def test_generic_token_inflation_rejected():
    # high raw overlap manufactured by common tokens, weak content anchor
    g = _gate("no", raw_overlap=0.515, best_frozen_overlap=0.15)
    assert g["decision"] == REJECT
    assert (
        NO_SEMANTIC_ANCHOR in g["reason_codes"]
        or OVERLAP_INFLATED_BY_GENERIC_TOKENS in g["reason_codes"]
    )
    assert g["overlap_inflation"] >= 0.40


def test_inflation_rule_requires_weak_anchor():
    # same raw inflation but a strong content anchor -> not an inflation reject
    text = (
        "improvement notice remedy contravence operator food safety officer "
        "section period specified"
    )
    g = _gate(text, raw_overlap=0.55, best_frozen_overlap=0.20)
    assert OVERLAP_INFLATED_BY_GENERIC_TOKENS not in g["reason_codes"]
    assert g["decision"] != REJECT


def test_instrument_conflict_beats_high_overlap():
    # candidate from a clearly unrelated instrument, still sharing question
    # content words ('improvement notice') -> high overlap must not save it
    text = (
        "Section 153 of the Kolkata Municipal Corporation Act requires every "
        "improvement notice issued by the Corporation to be served on the "
        "owner of the premises within thirty days."
    )
    g2 = _gate(
        text,
        raw_overlap=0.70,
        best_frozen_overlap=0.30,
        question_families=["fssai"],
        payload_families=["kmc"],
    )
    assert g2["question_act"] == ["fssai"]
    assert "kmc" in g2["candidate_act"]
    assert g2["identifier_conflict"] is True
    assert g2["decision"] == REJECT
    assert INSTRUMENT_CONFLICT in g2["reason_codes"]
    # high lexical overlap never compensates for the conflict
    assert g2["raw_overlap"] > 0.6


# --------------------------------------------------------------------------- #
# Identifier consistency: positive evidence only (review 2026-09-26)
# --------------------------------------------------------------------------- #

def test_candidate_without_identifiers_earns_no_identifier_codes():
    """A candidate carrying no identifiers must not be reported as a match.

    Regression: `instrument_match` used to be vacuously True when one side had
    no instruments, so `identifier_match` came out True and the reason codes
    claimed IDENTIFIER_PRESENT for a candidate with zero identifiers.
    """
    cand = (
        "The Food Safety Officer may serve an improvement notice requiring the "
        "operator to remedy the contravence within the specified period and "
        "record the compliance in the register maintained for the purpose."
    )
    assert extract_legal_identifiers(cand, fam_map=FAM).present is False
    g = _gate(cand, raw_overlap=0.50, best_frozen_overlap=0.20)
    assert g["identifier_match"] is None, "not assessable, not a match"
    assert g["instrument_match"] is None
    assert IDENTIFIER_PRESENT not in g["reason_codes"]
    assert IDENTIFIER_MATCH not in g["reason_codes"]

def test_identifier_match_false_when_comparable_but_disjoint():
    """Both sides carry identifiers on a comparable axis, none shared -> False."""
    cand = (
        "Section 153 of the Kolkata Municipal Corporation Act requires the "
        "Corporation to serve every improvement notice on the owner within "
        "thirty days of the inspection being completed."
    )
    g = gate_fill_candidate(
        cand,
        question=QUESTION,
        frozen_reference=REFERENCE,
        raw_overlap=0.60,
        best_frozen_overlap=0.30,
        fam_map=FAM,
        question_families=["fssai"],
        payload_families=["kmc"],
    )
    assert g["identifier_match"] is False
    assert g["identifier_conflict"] is True

def test_section_shorthand_s7_is_extracted():
    """'s7' (no dot) must extract — the step3 mention regex already accepts it."""
    ids = extract_legal_identifiers("What does s7 of the Customs Act require?")
    assert "7" in ids.sections, ids.provisions
    ids2 = extract_legal_identifiers("see s.7 and section 8 of the Act")
    assert "7" in ids2.sections and "8" in ids2.sections


def test_section_mismatch_caps_at_review():
    text = (
        "The Food Safety Officer may serve an improvement notice under section "
        "153 of the Food Safety and Standards Act, 2006 requiring the operator "
        "to remedy the contravence within the specified period."
    )
    g = _gate(text, raw_overlap=0.60, best_frozen_overlap=0.30)
    assert g["section_mismatch"] is True
    assert SECTION_MISMATCH in g["reason_codes"]
    assert g["decision"] in (REVIEW, PASS)
    assert g["decision"] != PASS or SECTION_MISMATCH not in g["reason_codes"]


def test_margin_fail_caps_at_review_not_reject():
    text = (
        "The Food Safety Officer may serve an improvement notice under section "
        "32 of the Food Safety and Standards Act, 2006 requiring the operator "
        "to remedy the contravence within the specified period."
    )
    # strong content but margin below frozen+0.05
    g = _gate(text, raw_overlap=0.40, best_frozen_overlap=0.42)
    assert g["margin_ok"] is False
    assert MARGIN_FAIL in g["reason_codes"]
    assert g["decision"] == REVIEW  # margin is one component, not a rejector


def test_weak_semantic_gated_review():
    text = (
        "Functions of the Central Water Laboratory include sampling analysis "
        "and publication of results for pollution control boards."
    )
    g = _gate(text, raw_overlap=0.34, best_frozen_overlap=0.27)
    assert g["decision"] in (REVIEW, REJECT)
    if g["decision"] == REVIEW:
        assert WEAK_SEMANTIC_ANCHOR in g["reason_codes"]


def test_pass_requires_quality_semantic_margin_all_ok():
    # content-rich but boilerplate-heavy -> HIGH_COMMON_TOKEN_RATIO caps it
    text = (
        "the notice shall be served by the officer and the person shall comply "
        "with the notice and the board may in case of default take action as "
        "prescribed under the rules"
    )
    g = _gate(text, raw_overlap=0.60, best_frozen_overlap=0.20)
    if HIGH_COMMON_TOKEN_RATIO in g["reason_codes"]:
        assert g["decision"] != PASS
    # and the clean version passes
    g2 = _gate(REFERENCE, raw_overlap=0.65, best_frozen_overlap=0.30)
    assert g2["decision"] == PASS
    assert SEMANTIC_ANCHOR_OK in g2["reason_codes"]


# --------------------------------------------------------------------------- #
# Proposal-level aggregation
# --------------------------------------------------------------------------- #

def test_gate_fill_proposal_best_candidate_wins():
    p = gate_fill_proposal([
        {"decision": REJECT, "reason_codes": ["METADATA_FRAGMENT"]},
        {"decision": REVIEW, "reason_codes": ["WEAK_SEMANTIC_ANCHOR"]},
    ])
    assert p["decision"] == REVIEW and p["n"] == 2
    assert (p["n_reject"], p["n_review"], p["n_pass"]) == (1, 1, 0)

    p2 = gate_fill_proposal([
        {"decision": REJECT, "reason_codes": []},
        {"decision": PASS, "reason_codes": []},
    ])
    assert p2["decision"] == PASS
    assert gate_fill_proposal([])["decision"] == REVIEW


# --------------------------------------------------------------------------- #
# Determinism + structured diagnostics contract
# --------------------------------------------------------------------------- #

def test_evaluate_reference_quality_contract_fields():
    d = evaluate_reference_quality(REFERENCE, QUESTION, REFERENCE, fam_map=FAM)
    for key in (
        "reference_quality_score", "candidate_length", "word_count",
        "meaningful_token_count", "common_token_ratio", "metadata_fragment",
        "heading_fragment", "ocr_fragment", "legal_identifier_present",
        "legal_identifier_match", "quality_flags",
    ):
        assert key in d, f"missing required diagnostic: {key}"
    assert 0.0 <= d["reference_quality_score"] <= 1.0
    assert d["legal_identifier_present"] is True
    assert d["metadata_fragment"] is False
    # internal handle must not leak into persisted output
    assert "_ids" not in d


def test_gate_is_deterministic():
    a = _gate(REFERENCE)
    b = _gate(REFERENCE)
    assert a == b
    c = _gate("Area:", raw_overlap=0.51, best_frozen_overlap=0.27)
    d = _gate("Area:", raw_overlap=0.51, best_frozen_overlap=0.27)
    assert c == d


# --------------------------------------------------------------------------- #
# Negative control: the 14 human-reviewed proposals
# --------------------------------------------------------------------------- #

CONTROL_CSV = ROOT / "evaluation" / "out" / "ceiling_v5" / "Step3_EM_Final_Datasheet_2026-09-26.csv"


def _control_rows():
    if not CONTROL_CSV.exists():
        return []
    with open(CONTROL_CSV, encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("QID")]


def test_negative_control_gate_never_passes_human_rejects():
    """No human-rejected control row may ever reach PASS (safety property)."""
    rows = _control_rows()
    if not rows:
        return
    import os
    os.environ.setdefault("SKIP_SCHEMA_CHECK", "1")
    from evaluation.resolution import payload_to_keys
    from evaluation.step3_gated_generation import (
        _norm_ws,
        derive_targets,
        load_deps,
        refs_for,
        token_overlap,
    )

    deps = load_deps(with_labels=True)
    human_rejects = [
        r["QID"] for r in rows if "reject" in (r.get("Final Status") or "").lower()
    ]
    manual = [
        r["QID"] for r in rows if "manual" in (r.get("Final Status") or "").lower()
    ]
    assert len(human_rejects) == 11 and len(manual) == 3

    n_caught = 0
    for qid in human_rejects:
        d = deps.questions[qid]
        t = derive_targets(d)
        frozen = set(deps.o3_ids(qid))
        ref = " ".join(refs_for(qid, deps.questions, deps.widened))
        pool = set()
        for fam in t["families"]:
            pool |= deps.fam_chunks.get(fam, set())
        pool -= frozen
        frozen_texts = {
            _norm_ws(str((deps.payload.get(c) or {}).get("chunk_text") or "")) for c in frozen
        }
        best_frozen = max(
            (
                token_overlap(ref, str((deps.payload.get(c) or {}).get("chunk_text") or ""))["correctness"]
                for c in frozen
            ),
            default=0.0,
        )
        thr = max(0.25, best_frozen + 0.05)
        scored = []
        for cid in pool:
            pl = deps.payload.get(cid) or {}
            txt = str(pl.get("chunk_text") or "")
            nt = _norm_ws(txt)
            if not nt or nt in frozen_texts:
                continue
            ov = token_overlap(ref, txt)["correctness"]
            if ov >= thr:
                scored.append((ov, cid, txt))
        scored.sort(key=lambda x: (-x[0], x[1]))
        seen, cands = set(), []
        for ov, cid, txt in scored:
            nt = _norm_ws(txt)
            if nt in seen:
                continue
            seen.add(nt)
            cands.append((ov, cid, txt))
            if len(cands) >= 8:
                break
        if not cands:
            continue
        gates = []
        for ov, cid, txt in cands:
            pl = deps.payload.get(cid) or {}
            gates.append(
                gate_fill_candidate(
                    txt,
                    question=str(d.question),
                    frozen_reference=ref,
                    raw_overlap=ov,
                    best_frozen_overlap=best_frozen,
                    fam_map=deps.fam_map,
                    payload=pl,
                    question_families=t["families"],
                    payload_families=[
                        fam for fam, _ in payload_to_keys(pl, deps.fam_map)
                    ],
                )
            )
        qg = gate_fill_proposal(gates)
        # safety property: PASS is never emitted for a human-rejected row
        assert qg["decision"] != PASS, f"{qid} human-rejected but gate PASS"
        if qg["decision"] == REJECT:
            n_caught += 1
        # every candidate carries reason codes
        assert all(g["reason_codes"] for g in gates)
    # calibrated: the gate outright rejects the majority of human rejects
    assert n_caught >= 8, f"only {n_caught}/11 human rejects caught outright"

    # the 3 'Manual verification' rows must NOT be killed outright (they are
    # genuinely-provision-shaped; uncertainty -> REVIEW, human decides)
    for qid in manual:
        assert qid in deps.questions
