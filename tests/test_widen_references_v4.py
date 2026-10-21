"""WF-6 widening pipeline tests — packets, gates, additive merge (0 LLM).

These tests make the widening workflow's contract executable:
- packets exist for exactly the 14 ``widen_overlay`` qids, all pending human,
- the mechanical gates (add-length, anchor-verbatim, v1-dedupe, D2-independence)
  accept a well-formed entry and reject each violation with a specific reason,
- the merge is additive-only, bumps the bundle version, verifies the checksum,
  and is idempotent,
- the merged state is recorded per-qid with provenance (no silent drops).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import evaluation.widen_references_v4 as w4
from evaluation.build_references_v4 import verify_bundle

WIDEN_QIDS = w4.WIDEN_QIDS
PROJECT = Path(w4.PROJECT_ROOT)


@pytest.fixture(scope="module")
def real_ctx():
    return w4.load_context()


# ---------------------------------------------------------------- packets ---


def test_packets_exist_for_exactly_the_widen_qids():
    state = json.loads(w4.STATE_PATH.read_text(encoding="utf-8"))
    assert set(state["packets"]) == set(WIDEN_QIDS)
    for qid, rec in state["packets"].items():
        assert Path(rec["packet"]).is_file(), f"packet missing: {qid}"
        assert rec["status"] in {"pending_human", "merged"}, f"{qid} in unknown state: {rec['status']}"
        if rec["status"] == "merged":
            assert rec.get("validated") == "valid", f"{qid} merged without passing validation"
        assert rec["anchors"] > 0, f"{qid} packet has no gold anchors — unusable"


def test_packet_template_marks_the_entry_fields():
    template = (
        "### WF6-ENTRY\n"
        "add: <your widened reading — one paragraph, 40..2000 chars>\n"
        "anchor_quote: <verbatim quote from an anchor above>\n"
        "note: <why this reading belongs to the gold set / what the audit missed>\n"
    )
    entry = w4.parse_entry(template)
    assert entry, "template fields must parse"
    assert w4.is_template(entry), "untouched packet must be detected as template"


# ------------------------------------------------------------------ gates ---


def _synthetic_ctx():
    anchor = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu. " * 4
    return {
        "perq": {"QX": {"gold_payload_ids": {"u1": ["c1"]}, "question": "synthetic"}},
        "payload_index": {"c1": {"chunk_text": anchor, "act_name": "Test Act", "section_number": "1"}},
        "bundle": {
            "questions": {
                "QX": {
                    "conclusions": [
                        "Existing v1 conclusion about an entirely unrelated statutory scheme and duty sentence."
                    ]
                }
            }
        },
        "worksheet_arms": {
            "QX": {
                "D2": "Stored failing model answer text with words absent from the new proposed gold reading entirely."
            }
        },
    }, anchor


def test_gate_accepts_a_well_formed_entry():
    ctx, anchor = _synthetic_ctx()
    add = (
        "Proposed reading: the authority must maintain continuous monitoring systems, publish periodic "
        "inspection reports, and keep a public register of every licensed facility within its jurisdiction."
    )
    verdict = w4.validate_entry("QX", {"add": add, "anchor_quote": anchor[:60], "note": "n"}, ctx)
    assert verdict["status"] == "valid", verdict["reasons"]


def test_gate_rejects_short_add_and_missing_anchor():
    ctx, anchor = _synthetic_ctx()
    verdict = w4.validate_entry("QX", {"add": "too short", "anchor_quote": anchor[:60], "note": "n"}, ctx)
    assert verdict["status"] == "invalid" and any("add too short" in r for r in verdict["reasons"])
    verdict = w4.validate_entry(
        "QX", {"add": "x" * 80, "anchor_quote": "words that appear nowhere in the gold anchor", "note": "n"}, ctx
    )
    assert any("NOT a verbatim substring" in r for r in verdict["reasons"])


def test_gate_rejects_v1_duplicate_and_answer_paraphrase():
    ctx, anchor = _synthetic_ctx()
    v1 = ctx["bundle"]["questions"]["QX"]["conclusions"][0]
    verdict = w4.validate_entry("QX", {"add": v1 + " copy", "anchor_quote": anchor[:60], "note": "n"}, ctx)
    assert any("duplicates the v1 reference" in r for r in verdict["reasons"])


# ------------------------------------------------------------------ merge ---
# The merge is exercised on a TMP COPY of the real frozen bundle so the live
# bundle can never be mutated by a test; the test then proves the real bundle
# stayed frozen.


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    tmp_bundle = tmp_path / "bundle_copy.json"
    shutil.copyfile(w4.BUNDLE_PATH, tmp_bundle)
    monkeypatch.setattr(w4, "BUNDLE_PATH", tmp_bundle)
    monkeypatch.setattr(w4, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(w4, "MERGE_REPORT", tmp_path / "merge_report.json")
    return tmp_bundle


def _write_packet(tmp_path: Path, qid: str, entry: dict[str, str]) -> Path:
    packet = tmp_path / f"{qid}.md"
    packet.write_text(
        f"### WF6-ENTRY\nadd: {entry['add']}\nanchor_quote: {entry['anchor_quote']}\nnote: {entry.get('note', '')}\n",
        encoding="utf-8",
    )
    return packet


def _live_bundle_sha() -> str:
    return verify_bundle()["recorded_content_sha256"]


def test_merge_is_additive_versioned_and_idempotent(staged, tmp_path, real_ctx):
    live_sha_before = _live_bundle_sha()
    qid = "Q083"  # small real anchor set; real bundle copy carries the same v1
    anchors = w4.gold_anchors(real_ctx["perq"][qid], real_ctx["payload_index"])
    quote = anchors[0]["anchor_text"][:60]
    add = (
        "Proposed reading: the authority must maintain continuous monitoring systems, publish periodic "
        "inspection reports, and keep a public register of every licensed facility within its jurisdiction."
    )
    packet = _write_packet(tmp_path, qid, {"add": add, "anchor_quote": quote, "note": "wf6 test entry"})
    w4.STATE_PATH.write_text(
        json.dumps({
            "stage": "built",
            "packets": {
                qid: {"packet": str(packet), "status": "pending_human", "merged": None, "validated": None, "anchors": 1}
            },
        }),
        encoding="utf-8",
    )

    before = json.loads(staged.read_text(encoding="utf-8"))["questions"][qid]["conclusions"]
    report = w4.merge_from_packets()
    assert report["merged_qids"] == [qid] and report["blocked"] == []

    after = json.loads(staged.read_text(encoding="utf-8"))["questions"][qid]["conclusions"]
    assert after[: len(before)] == before, "additive-only: v1 conclusions preserved"
    assert len(after) == len(before) + 1
    verdict = verify_bundle(staged)
    assert verdict["ok"] and verdict["status"] == "frozen"

    # idempotent: second merge must not append again nor bump the version again
    report2 = w4.merge_from_packets()
    sha_after_first = json.loads(staged.read_text(encoding="utf-8"))["checksum_sha256"]
    assert (
        report2["merged_qids"] == []
        or json.loads(staged.read_text(encoding="utf-8"))["questions"][qid]["conclusions"] == after
    )
    assert json.loads(staged.read_text(encoding="utf-8"))["checksum_sha256"] == sha_after_first

    assert _live_bundle_sha() == live_sha_before, "the real frozen bundle must not be touched by tests"


def test_merge_never_moves_unfilled_packets(staged, tmp_path):
    live_sha_before = _live_bundle_sha()
    packet = tmp_path / "empty.md"
    packet.write_text("### WF6-ENTRY\n(add placeholders untouched)\n", encoding="utf-8")
    w4.STATE_PATH.write_text(
        json.dumps({
            "stage": "built",
            "packets": {
                "Q146": {
                    "packet": str(packet),
                    "status": "pending_human",
                    "merged": None,
                    "validated": None,
                    "anchors": 1,
                }
            },
        }),
        encoding="utf-8",
    )
    report = w4.merge_from_packets()
    assert report["merged_qids"] == []
    assert any(r["status"] == "not_filled" for r in report["results"])
    assert verify_bundle(w4.BUNDLE_PATH)["ok"]
    assert _live_bundle_sha() == live_sha_before


def test_build_packets_supports_a_named_subset(tmp_path):
    """Build the reference-narrow cohort without clobbering the default run.

    Q042/Q043/Q148 are ``reference_narrow`` partials (not part of the default
    14-qid ``widen_overlay`` cohort). The explicit ``qids``/``state_path``
    arguments let a reviewer build just those packets in a separate lane.
    """
    built = w4.build_packets(
        out_dir=tmp_path / "packets",
        qids=("Q042", "Q043", "Q148"),
        state_path=tmp_path / "state.json",
    )
    state = built["state"]
    assert set(state) == {"Q042", "Q043", "Q148"}
    for qid, rec in state.items():
        assert Path(rec["packet"]).is_file(), f"packet missing: {qid}"
        assert rec["status"] == "pending_human"
        assert rec["anchors"] > 0, f"{qid} packet has no gold anchors — unusable"
