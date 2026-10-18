"""WF-7 disposition tests — the 24 ``overlay_insufficient`` qids."""

from __future__ import annotations

import json
from pathlib import Path

import evaluation.audit_insufficient_24 as w7

RESULTS = w7.RESULTS


def test_artifact_matches_the_triage_bucket_exactly():
    triage = json.loads((w7.OUT / "evaluator_v3_triage.json").read_text(encoding="utf-8"))
    expected = {e["qid"] for e in triage["buckets"]["overlay_insufficient"]}
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    got = {row["qid"] for row in data["dispositions"]}
    assert got == expected == set(expected)
    assert len(data["dispositions"]) == 24


def test_gate_every_qid_exactly_one_class_no_prose():
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    gate = data["gate"]
    assert gate["exactly_one_class_each"] is True
    assert gate["no_reference_prose_proposed"] is True
    assert gate["classified_machine"] + gate["escalated_human"] == 24
    causes = {r["cause"] for r in data["dispositions"]}
    assert causes <= set(w7.CAUSE_CLASSES)


def test_escalated_rows_carry_infra_evidence():
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    for row in data["dispositions"]:
        if row["cause"] == "unresolvable_infra_input":
            assert "(WF-1" in (row["evidence"].get("gap") or ""), row["qid"]


def test_recovered_rows_consistent_with_v4_state():
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    for row in data["dispositions"]:
        v4 = row.get("v4_state")
        if row["cause"] == "recovered_by_v4.2":
            assert v4 is not None and v4["credit"] is True, row["qid"]
        else:
            assert v4 is None or v4["credit"] is False, f"{row['qid']} classified failing but CREDIT held"
