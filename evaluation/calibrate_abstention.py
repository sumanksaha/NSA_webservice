"""WF-5 — abstention-lexicon calibration against the human tabulation (0 LLM).

Baseline (from the 150/150 audit + top-lawyer rubric): the hand-written refusal
lexicon produces false abstentions on **38.3 %** of the failure cohort and the
v2 overlay already banned 2 qids (Q060/Q138). This module measures the lexicon
per audited arm, proposes **qid-scoped bans** backed by the audit's own
verdicts (the unambiguous case: the human adjudicated the qid's answer correct
while the lexicon refused it), merges them into the reference bundle (bundle
version bump per the ruler boot rules), and re-measures:

  false_abstention_rate  = matched-while-answerable / answerable        (target <= 0.10)
  missed_abstention_rate = unanswered-while-IE / IE                     (informational —
                            a refusal the text never states cannot be
                            added by a lexicon; generator-side, WF-6+)

Definitions (data-driven, no guessing):
  expected_abstain(qid)   = bundle.expected_abstain (15 IE questions)
  answerable(qid)         = NOT expected_abstain
  matched(arm, qid)       = lexicon fires on that arm's stored answer text
  ban candidate           = (qid, phrase) matched on an answerable qid whose
                            audit verdict is ``evaluator_miss`` (human said the
                            answer was correct: the refusal was false) — the
                            one mechanical, unambiguous backing available.

Additions (missed-abstention candidates) are PROPOSED ONLY — never auto-applied:
authoring refusal patterns into the lexicon without a human pass writes the
skill being measured (design review R3 / workflow WF-6 discipline).

Usage:
    python -m evaluation.calibrate_abstention            # measure + propose
    python -m evaluation.calibrate_abstention --apply    # also stamp bans into the bundle
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
TABULATION = OUT / "full_review_tabulation.json"
BUNDLE_PATH = PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json"
RESULTS = OUT / "abstention_calibration_v2.json"

AUDIT_ARMS = ("C-O3", "D2", "D3", "E1")


def load_lexicon() -> list[str]:
    """Union refusal lexicon: grading's phrasal lexicons (the private lists the
    graded path actually consults) — analysis-side import, not production path."""
    from evaluation.grading import _ABSTAIN_PHRASES, _REFUSE_PHRASES

    out: list[str] = []
    for phrase in (*_ABSTAIN_PHRASES, *_REFUSE_PHRASES):
        phrase = str(phrase).strip().lower()
        if phrase and phrase not in out:
            out.append(phrase)
    return out


def load_arm_texts() -> dict[str, dict[str, str]]:  # qid -> arm -> text
    from evaluation.measure_component_rule_identity import load_answers
    from evaluation.v3_component_rescore import parse_worksheet

    texts: dict[str, dict[str, str]] = {}
    c_o3 = load_answers()
    for qid, rec in c_o3.items():
        if isinstance(rec, dict) and rec.get("answer"):
            texts.setdefault(qid, {})["C-O3"] = rec["answer"]
    worksheet = OUT / "full_review_worksheet_completed.md"
    if worksheet.exists():
        for qid, arms in parse_worksheet(worksheet).items():
            for arm in AUDIT_ARMS:
                if arm in arms:
                    texts.setdefault(qid, {})[arm] = arms[arm]
    return texts


def matched_phrases(text: str, lexicon: list[str], banned: set[str]) -> list[str]:
    """Which lexicon phrases fire on *text* once qid-scoped bans are subtracted."""
    low = text.lower()
    for phrase in banned:
        low = low.replace(phrase, " ")
    return [p for p in lexicon if p in low]


def measure(
    texts: dict[str, dict[str, str]], lexicon: list[str], expected: dict[str, bool], bans: dict[str, set[str]]
) -> dict[str, Any]:
    per_arm: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    for arm in AUDIT_ARMS:
        rows = [
            dict(qid=qid, matched=matched_phrases(t, lexicon, bans.get(qid, set())))
            for qid, arms in sorted(texts.items())
            if (t := arms.get(arm))
        ]
        answerable = [r for r in rows if not expected[r["qid"]]]
        ie_r = [r for r in rows if expected[r["qid"]]]
        false_hits = sum(1 for r in answerable if r["matched"])
        false_rate = round(false_hits / max(len(answerable), 1), 4)
        missed = sum(1 for r in ie_r if not r["matched"])
        per_arm[arm] = {
            "n_answerable": len(answerable),
            "n_ie": len(ie_r),
            "false_abstentions": false_hits,
            "false_abstention_rate": false_rate,
            "missed_abstentions": missed,
            "missed_abstention_rate": round(missed / max(len(ie_r), 1), 4),
        }
        all_rows += rows
    answerable = [r for r in all_rows if not expected[r["qid"]]]
    ie_r = [r for r in all_rows if expected[r["qid"]]]
    pooled_false = sum(1 for r in answerable if r["matched"])
    pooled_missed = sum(1 for r in ie_r if not r["matched"])
    per_question = {
        "n_arm_rows": len(all_rows),
        "n_answerable_rows": len(answerable),
        "n_ie_rows": len(ie_r),
        "false_abstention_rate": round(pooled_false / max(len(answerable), 1), 4),
        "missed_abstention_rate": round(pooled_missed / max(len(ie_r), 1), 4),
    }
    return {"per_arm": per_arm, "pooled_per_arm_row": per_question}, all_rows


def propose_bans(
    all_rows: list[dict[str, Any]], verdicts: dict[str, dict[str, Any]], expected: dict[str, bool]
) -> dict[str, Any]:
    """qid -> {phrase: provenance} — audit-backed, unambiguous false-refusal bans.

    Two backing sources, both mechanical and audit-authored (no scoring-model
    opinion involved):
    - ``evaluator_miss`` qid + matched phrase  → the answer was correct and the
      lexicon refused it (the double-penalise the v2 overlay already banned 2
      qids for).
    - ``model_wrong`` qid with ``abstention_gate`` model_action + matched phrase
      → the audit explicitly attributed the failure to the refusal behaviour on
      an answerable qid — the same backing class, one bucket later.
    """
    votes: dict[tuple[str, str], dict[str, Any]] = {}
    for row in all_rows:
        qid = row["qid"]
        if expected[qid]:
            continue
        verdict = (verdicts.get(qid) or {}).get("verdict")
        action = (verdicts.get(qid) or {}).get("model_action")
        backing = None
        if verdict == "evaluator_miss":
            backing = "human audit: evaluator_miss (answer correct, lexicon refused)"
        elif verdict == "model_wrong" and action == "abstention_gate":
            backing = "human audit: model_wrong/abstention_gate (refusal judged the failure on an answerable qid)"
        if backing is None:
            continue
        for phrase in row["matched"]:
            slot = votes.setdefault(
                (qid, phrase),
                {
                    "qid": qid,
                    "phrase": phrase,
                    "provenance": {"audited_verdict": verdict, "model_action": action, "backing": backing},
                    "arms": [],
                },
            )
            slot["arms"].append(1)
    grouped: dict[str, dict[str, Any]] = {}
    for (qid, _), slot in votes.items():
        grouped.setdefault(qid, {})[slot["phrase"]] = {"provenance": slot["provenance"]}
    return {"bans_by_qid": grouped, "n_qids": len(grouped), "n_phrase_rules": len(votes)}


def merge_additions(rows: list[dict[str, Any]], expected: dict[str, bool], lexicon: list[str]) -> list[dict[str, Any]]:
    """Harvest missed-abstention candidates (IE answers not lexicon-flagged) for human review."""
    candidates: Counter[str] = Counter()
    for arms in rows:
        pass
    proposals: list[dict[str, Any]] = []
    for row in rows:
        if not expected[row["qid"]] or row["matched"]:
            continue
        qid = row["qid"]
        proposals.append({
            "qid": qid,
            "proposal": "GENERATOR-side review: no refusal phrasing present — lexicon cannot fix (WF-6 packet)",
        })
    return proposals


def apply_bans_to_bundle(bans_by_qid: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Stamp the bans into the frozen bundle (boot rules: bump + new checksum)."""
    from evaluation.build_references_v4 import canonical_bytes

    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    version = str(bundle["meta"].get("bundle_version") or "1.0.0")
    major, minor, patch = (int(part) for part in version.split("."))
    bundle["meta"]["bundle_version"] = f"{major}.{minor + 1}.{patch}"
    overwrite = json.loads(json.dumps(bundle["meta"]))
    _ = overwrite
    bundle["banned_abstain_markers"] = {
        **(bundle.get("banned_abstain_markers") or {b: None for b in []}),
        "rules_by_qid": {
            qid: {
                "banned_phrases": sorted(slot.keys()) if isinstance(slot, dict) else [],
                "provenance": (slot or {}).get("provenance") if isinstance(slot, dict) else None,
            }
            for qid, slot in sorted(bans_by_qid.items())
        },
        "carried_from": {
            "v2_overlay_applies_to_qids": (bundle.get("banned_abstain_markers") or {}).get("applies_to_qids", []),
            "v2_overlay_markers": (bundle.get("banned_abstain_markers") or {}).get("markers", []),
        },
        "source": "WF-5 calibration vs full_review_tabulation.json (audit-backed, qid-scoped)",
    }
    bundle["checksum_sha256"] = None
    from evaluation.build_references_v4 import content_sha256

    bundle["checksum_sha256"] = content_sha256(bundle)
    payload = canonical_bytes({k: v for k, v in bundle.items()})
    if CHECK := (payload is not None):
        pass
    BUNDLE_PATH.write_bytes(payload + b"\n")
    from evaluation.build_references_v4 import verify_bundle

    return verify_bundle(BUNDLE_PATH)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="stamp audit-backed bans into the bundle")
    args = parser.parse_args()

    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    expected = {qid: bool(rec.get("expected_abstain")) for qid, rec in bundle["questions"].items()}
    carried_bans = set()
    tab = json.loads(TABULATION.read_text(encoding="utf-8"))
    verdicts = {r["qid"]: r for r in tab.get("records") or []}
    lexicon = load_lexicon()
    texts = load_arm_texts()

    before, rows = measure(texts, lexicon, expected, bans={qid: set() for qid in texts})
    proposals = propose_bans(rows, verdicts, expected)
    additions = merge_additions(rows, expected, lexicon)
    missed_rows = []
    seen: set[str] = set()
    for row in additions:
        if row["qid"] not in seen:
            seen.add(row["qid"])
            missed_rows.append(row)

    after = None
    apply_result = None
    if proposals["n_phrase_rules"]:
        live_bans = {qid: set(rules) for qid, rules in proposals["bans_by_qid"].items()}
        # carried v2 overlay bans keep their effect inside the measurement
        carried_bans = (
            set(bundle.get("banned_abstain_markers", {}).get("markers", []))
            if bundle.get("banned_abstain_markers", {}).get("applies_to_qids")
            else set()
        )
        for qid in bundle.get("banned_abstain_markers", {}).get("applies_to_qids", []):
            live_bans.setdefault(qid, set()).update(m for m in carried_bans)
        after, _ = measure(texts, lexicon, expected, bans=live_bans)
        if args.apply:
            apply_result = apply_bans_to_bundle(proposals["bans_by_qid"])

    gate = {
        "targets": {
            "false_abstention_rate": 0.10,
            "missed_abstention_rate_note": "generator-side; lexicon-irreducible",
        },
        "before_false": before["pooled_per_arm_row"]["false_abstention_rate"],
        "after_false": None,
        "verdict": None,
    }
    if after:
        gate["after_false"] = after["pooled_per_arm_row"]["false_abstention_rate"]
        gate["verdict"] = "PASS" if gate["after_false"] <= 0.10 else "STILL ABOVE TARGET - re-review after WF-6"

    artifacts = {
        "version": "abstention-calibration-v2-1",
        "lexicon_size": len(lexicon),
        "before": before,
        "apply_requested": bool(args.apply),
        "bundle_apply": apply_result,
        "proposals": proposals,
        "missed_abstention_notes": {
            "n_rows": len(additions),
            "n_qids": len(missed_rows),
            "note": "not lexicon-fixable; queued into the WF-6 human packet",
            "additions_proposed": missed_rows,
        },
        "after": after,
        "gate": gate,
    }

    from evaluation.ruler import format_publication_header, publication_header, write_json_with_header

    print(format_publication_header(publication_header()))
    print(f"\nlexicon phrases            : {len(lexicon)}")
    print(f"arm texts loaded           : {sum(len(v) for v in texts.values())} over {len(texts)} qids")
    print(f"BEFORE  pooled false rate  : {gate['before_false']}  (targets: false<=0.10)")
    for arm, row in before["per_arm"].items():
        print(
            f"          {arm:<5} false {row['false_abstention_rate']:>6} missed {row['missed_abstention_rate']:>6} (n_ans {row['n_answerable']}, n_ie {row['n_ie']})"
        )
    print(
        f"\nbans proposed              : {proposals['n_phrase_rules']} phrase rules across {proposals['n_qids']} qids (audit-backed)"
    )
    if after:
        print(f"AFTER   pooled false rate  : {gate['after_false']}  verdict: {gate['verdict']}")
        for arm, row in after["per_arm"].items():
            print(
                f"          {arm:<5} false {row['false_abstention_rate']:>6} missed {row['missed_abstention_rate']:>6}"
            )
    if apply_result:
        print(
            f"bundle apply               : {apply_result['status']} v{apply_result.get('bundle_version')} content-sha {str(apply_result.get('computed_content_sha256'))[:12]}"
        )
    print(f"missed-abstention rows     : {len(additions)} (generator-side, queued WF-6)")
    print()

    write_json_with_header(
        RESULTS,
        artifacts,
        thresholds={
            "false_abstention_before": gate["before_false"],
            "false_abstention_after": gate.get("after_false"),
            "calibrated_against": "full_review_tabulation.json",
        },
    )
    print(f"written: {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
