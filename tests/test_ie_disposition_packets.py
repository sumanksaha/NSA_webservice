"""IE disposition lane tests — packets, gates, additive merge (0 LLM).

Contract made executable:
- packets exist for exactly the 11 IE qids, all pending human,
- the gates (choice membership A/B/C, human-written rationale) reject each
  violation with a specific reason and accept a well-formed decision,
- the merge is additive-only (stamps a NON-scoring field; no rule consumes
  it), bumps the bundle minor version, re-verifies the checksum, is
  idempotent, and refuses conflicting re-decisions without silent overwrite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import evaluation.build_references_v4 as br4
import evaluation.ie_disposition_packets as ie

IE_QIDS = ie.IE_QIDS
PROJECT = Path(ie.PROJECT_ROOT)


@pytest.fixture(scope="module")
def real_state():
    return json.loads(ie.STATE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- packets ---


def test_packets_exist_for_exactly_the_ie_qids(real_state):
    assert set(real_state["packets"]) == set(IE_QIDS), "disposition lane covers exactly the 11 IE qids"
    for qid, rec in real_state["packets"].items():
        assert Path(rec["packet"]).is_file(), f"packet missing: {qid}"
        assert rec["status"] in {"pending_human", "merged"}
        assert rec["class"] in {"A1_answered", "A2_no_trace"}, f"{qid}: unexpected behaviour class"
    kinds = {rec["class"] for rec in real_state["packets"].values()}
    assert kinds == {"A1_answered", "A2_no_trace"}, "the two measured behaviour classes must both be represented"


def test_all_ie_qids_expect_abstain_and_carry_references():
    bundle = json.loads((PROJECT / "evaluation" / "evaluator_v4_references.json").read_text(encoding="utf-8"))
    for qid in IE_QIDS:
        info = bundle["questions"].get(qid) or {}
        assert info.get("expected_abstain") is True, f"{qid}: the human audit expectation must be encoded"
        assert info.get("conclusions") or info.get("concepts"), f"{qid}: no references frozen"
        assert "ie_disposition" not in info, f"{qid}: pre-existing disposition - merge would need a new wave"


def test_template_packets_are_not_decisions():
    template = "### IED-DISPOSITION\ndisposition: <A|B|C>\nrationale: <human-written, 1-3 sentences, >=20 chars>"
    entry = ie.parse_disposition(template)
    assert entry, "template fields must parse"
    assert ie.is_template(entry)


# ------------------------------------------------------------------ gates ---


def test_choice_membership_and_human_rationale_gates():
    assert ie.validate_disposition("Q042", {"disposition": "D", "rationale": "x" * 30})["status"] == "invalid"
    no_rationale = ie.validate_disposition("Q042", {"disposition": "A", "rationale": "too short"})
    assert no_rationale["status"] == "invalid" and "rationale" in no_rationale["reason"]
    ok = ie.validate_disposition(
        "Q042", {"disposition": "B", "rationale": "The retrieved provision is inapplicable here."}
    )
    assert ok["status"] == "valid" and ok["disposition"] == "question_or_corpus_insufficiency"


# ------------------------------------------------------------------ merge ---


@pytest.fixture()
def tmp_lane(tmp_path, monkeypatch):
    """A private copy of the real bundle + a freshly built set of packets."""
    lane = tmp_path / "ie_lane"
    packets = lane / "packets"
    state = ie.build_packets(out_dir=packets, state_path=lane / "state.json")
    monkeypatch.setattr(br4, "BUNDLE_PATH", lane / "bundle.json")
    monkeypatch.setattr(ie, "MERGE_REPORT", lane / "report.json")
    bundle = json.loads((PROJECT / "evaluation" / "evaluator_v4_references.json").read_text(encoding="utf-8"))
    (lane / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    return {"lane": lane, "state": state, "packets": packets, "state_path": lane / "state.json"}


def _fill(
    packet_path: Path, disposition: str, rationale: str = "Human legal decision recorded in the reviewer packet."
) -> None:
    text = packet_path.read_text(encoding="utf-8")
    block = f"### IED-DISPOSITION\ndisposition: {disposition}\nrationale: {rationale}"
    head = text.split("### IED-DISPOSITION")[0]
    packet_path.write_text(head + block + "\n", encoding="utf-8")


def test_merge_is_additive_versioned_and_checksummed(tmp_lane):
    lane = tmp_lane
    _fill(lane["packets"] / "Q042.md", "A")
    report = ie.merge_from_packets(state_path=lane["state_path"])
    merged = [r for r in report["results"] if r["status"] == "valid"]
    assert report["n_merged"] == 1 and merged
    bundle = json.loads((lane["lane"] / "bundle.json").read_text(encoding="utf-8"))
    rec = bundle["questions"]["Q042"]["ie_disposition"]
    assert rec["disposition"] == "generator_gating_defect" and rec["wave"] == "IE-disposition-1"
    assert rec["rationale"] and not rec["rationale"].startswith("<")
    assert bundle["meta"]["bundle_version"] == "1.3.1", "minor bump, patch digit preserved (1.2.1 lineage)"
    assert report["bundle_verdict"]["ok"], "checksum must re-verify after merge"


def test_merge_is_idempotent_and_refuses_conflicts(tmp_lane):
    lane = tmp_lane
    _fill(lane["packets"] / "Q042.md", "A")
    state_path = lane["state_path"]
    first = ie.merge_from_packets(state_path=state_path)
    bundle = json.loads((lane["lane"] / "bundle.json").read_text(encoding="utf-8"))
    version_1 = bundle["meta"]["bundle_version"]

    again = ie.merge_from_packets(state_path=state_path)
    assert again["n_merged"] == 1 and any(r["status"] == "already_merged" for r in again["results"])
    assert json.loads((lane["lane"] / "bundle.json").read_text(encoding="utf-8"))["meta"]["bundle_version"] == version_1

    _fill(lane["packets"] / "Q042.md", "C", rationale="A different later human wave tries to re-decide.")
    conflict = ie.merge_from_packets(state_path=state_path)
    assert any(r["status"] == "conflict" for r in conflict["results"]), "re-decision must be refused, not overwritten"
    after = json.loads((lane["lane"] / "bundle.json").read_text(encoding="utf-8"))
    assert after["questions"]["Q042"]["ie_disposition"]["disposition"] == "generator_gating_defect"
    assert after["meta"]["bundle_version"] == version_1, "no version churn without an accepted change"
