"""S1: per-query-type abstention threshold calibration from existing S1 signals.

Zero LLM calls — the S1 pipeline signals already carry groundedness_score,
query_types, and expected_abstain for all 150 qids.

The plan's blocker was that answers_baseline.json lacked continuous
groundedness; the S1 signals (produced later) have it.
"""
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIGNALS = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "s1_pipeline_signals.jsonl"
BUNDLE = PROJECT_ROOT / "evaluation" / "evaluator_v4_references.json"
OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "abstention_calibration_data.json"


def load_signals() -> list[dict]:
    with open(SIGNALS, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_expected_abstain() -> dict[str, bool]:
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    return {qid: bool(rec.get("expected_abstain")) for qid, rec in (bundle.get("questions") or {}).items()}


def main() -> int:
    rows = load_signals()
    expected = load_expected_abstain()

    # Latest record per qid
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["question_id"]] = r

    qids = sorted(latest.keys())
    print(f"qids: {len(qids)}")

    # Per-query-type aggregation
    per_type: dict[str, dict] = defaultdict(
        lambda: {"qids": [], "n_ie": 0, "n_answerable": 0,
                 "abstention_gate_qids": [], "missed_abstention_qids": [],
                 "false_abstention_qids": []}
    )

    for qid in qids:
        rec = latest[qid]
        ie = bool(expected.get(qid, False))
        abstained = bool(rec.get("abstained"))
        groundedness = float(rec.get("groundedness_score") or 0.0)
        qtypes = rec.get("query_types") or ["Unclassified"]

        for qt in qtypes:
            slot = per_type[qt]
            if qid not in slot["qids"]:
                slot["qids"].append(qid)
            if ie:
                slot["n_ie"] += 1
                if not abstained:
                    slot["missed_abstention_qids"].append(qid)
            else:
                slot["n_answerable"] += 1
                if abstained:
                    slot["false_abstention_qids"].append(qid)
            if abstained:
                slot["abstention_gate_qids"].append(qid)

    # Compute rates and recommended action
    out_types: dict[str, dict] = {}
    for qt, slot in sorted(per_type.items()):
        n_ans = max(slot["n_answerable"], 1)
        n_ie = max(slot["n_ie"], 1)
        false_rate = round(len(slot["false_abstention_qids"]) / n_ans, 4)
        missed_rate = round(len(slot["missed_abstention_qids"]) / n_ie, 4)
        if false_rate > 0.10:
            action = "tighten_threshold"
        elif missed_rate > 0.25:
            action = "loosen_threshold"
        else:
            action = "hold"
        out_types[qt] = {
            "n_qids": len(slot["qids"]),
            "n_ie": slot["n_ie"],
            "n_answerable": slot["n_answerable"],
            "abstention_gate_qids": sorted(slot["abstention_gate_qids"]),
            "missed_abstention_qids": sorted(slot["missed_abstention_qids"]),
            "false_abstention_qids": sorted(slot["false_abstention_qids"]),
            "false_abstention_rate": false_rate,
            "missed_abstention_rate": missed_rate,
            "recommended_action": action,
            "qids": sorted(slot["qids"]),
        }

    # Roll-up
    n_ie_total = sum(1 for qid in qids if expected.get(qid, False))
    n_ans_total = len(qids) - n_ie_total
    ag_total = [qid for qid in qids if latest[qid].get("abstained")]
    ag_set = set(ag_total)
    false_total = [q for q in ag_total if not expected.get(q, False)]
    missed_total = [qid for qid in qids if expected.get(qid, False) and qid not in ag_set]

    rollup = {
        "n_qids": len(qids),
        "n_ie": n_ie_total,
        "n_answerable": n_ans_total,
        "n_abstention_gate": len(ag_total),
        "false_abstention_rate": round(len(false_total) / max(n_ans_total, 1), 4),
        "missed_abstention_rate": round(len(missed_total) / max(n_ie_total, 1), 4),
    }

    artifact = {
        "version": "abstention-calibration-data-v2",
        "source": {
            "signals": str(SIGNALS),
            "references_bundle": str(BUNDLE),
        },
        "rollup": rollup,
        "per_query_type": out_types,
    }
    OUT.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"n_qids={rollup['n_qids']} n_ie={rollup['n_ie']} n_answerable={rollup['n_answerable']}")
    print(f"abstention_gate={rollup['n_abstention_gate']}")
    print(f"false_abstention_rate={rollup['false_abstention_rate']} missed_abstention_rate={rollup['missed_abstention_rate']}")
    print()
    print(f"{'query_type':<22} {'n':>3} {'n_ie':>4} {'n_ans':>5} {'false%':>7} {'missed%':>8}  action")
    for qt, s in sorted(out_types.items()):
        print(
            f"{qt:<22} {s['n_qids']:>3} {s['n_ie']:>4} {s['n_answerable']:>5} "
            f"{s['false_abstention_rate'] * 100:>6.1f} {s['missed_abstention_rate'] * 100:>7.1f}  {s['recommended_action']}"
        )
    print(f"\nwritten: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())