"""Offline tests for Step 0b widened-reference drafting (no stdin, no network, no LLM)."""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

import evaluation.step0_dual_score_widen as w

# --------------------------------------------------------------------------- #
# load_drafts
# --------------------------------------------------------------------------- #


def test_load_drafts_basic_and_skips(tmp_path):
    p = tmp_path / "drafts.csv"
    p.write_text(
        "question_id,add,note,anchor_quote\n"
        "Q006,The Commissioner heads State food safety administration.,positive framing,\"exercising powers of supervision\"\n"
        ",,missing qid,\n"
        "Q009,,empty add,\n",
        encoding="utf-8-sig",
    )
    records, skipped = w.load_drafts(p)
    assert set(records) == {"Q006"}
    assert records["Q006"]["anchor_quote"] == "exercising powers of supervision"
    assert len(skipped) == 2


def test_load_drafts_quoted_commas(tmp_path):
    p = tmp_path / "drafts.csv"
    p.write_text(
        'question_id,add,note,anchor_quote\n'
        'Q013,"Shall manufacture, sell, or distribute","",s. 31 text\n',
        encoding="utf-8",
    )
    records, _ = w.load_drafts(p)
    assert records["Q013"]["add"] == "Shall manufacture, sell, or distribute"


# --------------------------------------------------------------------------- #
# validate_widening
# --------------------------------------------------------------------------- #


def _anchor(text="The Commissioner of Food Safety shall exercise supervision over Food Safety Officers."):
    return {
        "anchor_chunk_ids": ["c1"],
        "anchor_text": text,
        "anchor_is_fallback": False,
    }


def test_validate_passes_good_draft():
    draft = {
        "add": "The Commissioner of Food Safety is the State-level head of food safety administration and directs enforcement.",
        "note": "v1 only accepted the enumeration phrasing",
        "anchor_quote": "exercis*",
    }
    # quote must be verbatim: pick a real substring
    draft["anchor_quote"] = "supervision over Food Safety Officers"
    r = w.validate_widening("Q006", draft, _anchor(), "v1 ref text goes here and is quite different", None, {})
    assert r["status"] == "valid", r
    assert r["entry"]["anchor_chunk_ids"] == ["c1"]


def test_validate_rejects_quote_not_in_anchor():
    draft = {
        "add": "A sufficiently long statutory statement of the widened reading goes here.",
        "note": "n",
        "anchor_quote": "this sentence appears nowhere in the anchor text at all",
    }
    r = w.validate_widening("Q006", draft, _anchor(), "v1 ref", None, {})
    assert r["status"] == "invalid"
    assert any("NOT a verbatim substring" in x for x in r["reasons"])


def test_validate_rejects_missing_quote():
    draft = {"add": "A sufficiently long statutory statement of the widened reading.", "note": "", "anchor_quote": ""}
    r = w.validate_widening("Q006", draft, _anchor(), "v1 ref", None, {})
    assert r["status"] == "invalid"
    assert any("anchor_quote missing" in x for x in r["reasons"])


def test_validate_rejects_candidate_paraphrase():
    d2 = "The Commissioner of Food Safety is the State-level head of the food safety administration and directs enforcement of food safety law."
    draft = {
        "add": "The Commissioner of Food Safety is the State-level head of the food safety administration and directs enforcement.",
        "note": "n",
        "anchor_quote": "supervision over Food Safety Officers",
    }
    r = w.validate_widening("Q006", draft, _anchor(), "an entirely different v1 reference about standards", d2, {})
    assert r["status"] == "invalid"
    assert any("paraphrases the stored D2 answer" in x for x in r["reasons"])


def test_validate_rejects_v1_duplicate():
    v1 = "The Commissioner of Food Safety is the State-level head of food safety administration with supervisory powers."
    draft = {"add": v1, "note": "n", "anchor_quote": "supervision over Food Safety Officers"}
    r = w.validate_widening("Q006", draft, _anchor(), v1, None, {})
    assert r["status"] == "invalid"
    assert any("duplicates the v1 reference" in x for x in r["reasons"])


def test_validate_flags_fallback_anchor():
    a = _anchor()
    a["anchor_is_fallback"] = True
    draft = {
        "add": "A sufficiently long statutory statement of the widened reading is here.",
        "note": "n",
        "anchor_quote": "supervision over Food Safety Officers",
    }
    r = w.validate_widening("Q006", draft, a, "v1 ref", None, {})
    assert r["status"] == "invalid"
    assert any("fallback" in x for x in r["reasons"])


def test_validate_too_short_and_too_long():
    short = {"add": "too short", "note": "", "anchor_quote": "supervision over Food Safety Officers"}
    r = w.validate_widening("Q006", short, _anchor(), "v1", None, {})
    assert r["status"] == "invalid"
    long_add = "word " * 500
    r2 = w.validate_widening(
        "Q006",
        {"add": long_add, "note": "", "anchor_quote": "supervision over Food Safety Officers"},
        _anchor(),
        "v1",
        None,
        {},
    )
    assert r2["status"] == "invalid"


def test_validate_already_widened():
    draft = {"add": "A sufficiently long statutory statement of the widened reading.", "note": "n", "anchor_quote": "q"}
    r = w.validate_widening("Q006", draft, _anchor(), "v1", None, {"widened_conclusions": {"Q006": {"add": "x"}}})
    assert r["status"] == "already_widened"


def test_validate_no_anchor():
    draft = {"add": "A sufficiently long statutory statement of the widened reading.", "note": "", "anchor_quote": "q"}
    r = w.validate_widening("Q006", draft, None, "v1", None, {})
    assert r["status"] == "invalid"


# --------------------------------------------------------------------------- #
# merge_overlay — additive, idempotent
# --------------------------------------------------------------------------- #


def test_merge_adds_and_preserves(tmp_path, monkeypatch):
    overlay = {
        "version": "evaluator_v2",
        "widened_conclusions": {"Q004": {"add": "existing", "note": "keep me"}},
    }
    opath = tmp_path / "overlay.json"
    opath.write_text(json.dumps(overlay), encoding="utf-8")
    monkeypatch.setattr(w, "OVERLAY_PATH", opath)
    validation = {
        "results": [
            {"qid": "Q006", "status": "valid", "entry": {"add": "new reading", "note": "n", "anchor_quote": "q", "anchor_chunk_ids": ["c1"]}},
            {"qid": "Q004", "status": "valid", "entry": {"add": "would clobber", "note": "", "anchor_quote": "", "anchor_chunk_ids": []}},
            {"qid": "Q009", "status": "invalid", "reasons": ["x"]},
        ]
    }
    out = w.merge_overlay(validation)
    assert out["added"] == ["Q006"]
    merged = json.loads(opath.read_text(encoding="utf-8"))
    assert merged["widened_conclusions"]["Q004"]["add"] == "existing"  # never modified
    assert merged["widened_conclusions"]["Q006"]["add"] == "new reading"
    assert merged["revisions"][-1]["qids"] == ["Q006"]


def test_merge_dry_run_writes_nothing(tmp_path, monkeypatch):
    overlay = {"widened_conclusions": {}}
    opath = tmp_path / "overlay.json"
    opath.write_text(json.dumps(overlay), encoding="utf-8")
    monkeypatch.setattr(w, "OVERLAY_PATH", opath)
    validation = {"results": [{"qid": "Q006", "status": "valid", "entry": {"add": "x"}}]}
    out = w.merge_overlay(validation, dry_run=True)
    assert out["added"] == ["Q006"] and out["dry_run"]
    assert json.loads(opath.read_text(encoding="utf-8")) == overlay  # untouched


def test_merge_no_valid_drafts_no_revision(tmp_path, monkeypatch):
    opath = tmp_path / "overlay.json"
    opath.write_text(json.dumps({"widened_conclusions": {}}), encoding="utf-8")
    monkeypatch.setattr(w, "OVERLAY_PATH", opath)
    out = w.merge_overlay({"results": [{"qid": "Q006", "status": "invalid", "reasons": ["x"]}]})
    assert out["added"] == []
    assert "revisions" not in json.loads(opath.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# quote_in_evidence reuse from step1
# --------------------------------------------------------------------------- #


def test_quote_in_evidence_whitespace_normalized():
    assert w.quote_in_evidence("supervision   over\nOfficers", "text with supervision over\nOfficers inside")
    assert not w.quote_in_evidence("not present here", "the anchor text")
    assert not w.quote_in_evidence("", "anchor")


# --------------------------------------------------------------------------- #
# anchor ordering + fill (_fill_anchor, _rank_o3_chunks)
# --------------------------------------------------------------------------- #


def _payload_index(texts: dict[str, str]) -> dict[str, dict]:
    return {cid: {"text": t} for cid, t in texts.items()}


def test_fill_anchor_keeps_gold_order_and_appends_ranked():
    index = _payload_index({"gold1": "alpha beta", "gold2": "gamma", "rank1": "delta"})
    ranked = [(0.9, "rank1"), (0.4, "gold1")]
    out = w._fill_anchor(["gold1", "gold2"], ranked, index)
    # gold chunks keep their position; the ranked remainder is appended once
    assert out[:2] == ["gold1", "gold2"]
    assert out[2:] == ["rank1"]


def test_fill_anchor_stops_at_cap(monkeypatch):
    monkeypatch.setattr(w, "ANCHOR_TEXT_CAP", 150)
    index = _payload_index({"g": "x" * 100, "a": "y" * 100, "b": "z" * 100})
    ranked = [(0.5, "a"), (0.4, "b")]
    out = w._fill_anchor(["g"], ranked, index)
    # 100 (gold) + 100 (fill) reaches a cap of 150 -> only one chunk appended
    assert out == ["g", "a"]


def test_fill_anchor_skips_empty_and_duplicates():
    index = _payload_index({"g": "text", "empty": "   ", "dup": "more"})
    out = w._fill_anchor(["g"], [(0.9, "g"), (0.8, "empty"), (0.7, "dup")], index)
    assert out == ["g", "dup"]


def test_fill_anchor_noop_when_already_over_cap(monkeypatch):
    monkeypatch.setattr(w, "ANCHOR_TEXT_CAP", 5)
    index = _payload_index({"g": "0123456789", "extra": "more text"})
    out = w._fill_anchor(["g"], [(0.9, "extra")], index)
    assert out == ["g"]


def test_rank_o3_chunks_orders_by_overlap_then_id():
    index = _payload_index({"c1": "irrelevant filler text here", "c2": "noxious trade control"})
    query = {"noxious", "trade"}
    ranked = w._rank_o3_chunks(["c2", "c1"], index, query)
    assert [cid for _s, cid in ranked] == ["c2", "c1"]
    assert ranked[0][0] > ranked[1][0]
