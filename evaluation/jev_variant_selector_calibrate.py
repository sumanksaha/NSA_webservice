"""Offline calibration for JEV-powered per-question variant selection.

Compares JEV's variant pick vs the oracle pick (shortest among
correctness-eligible variants, non-abstaining preferred) on cached experiment
results from ``evaluation/out/ceiling_v5/brevity_ab_test.json``.

Outputs (all written to ``evaluation/out/ceiling_v5/``, gitignored):
  * jev_calibration.json — per-question JEV pick, oracle pick, match flag,
    guardrail results for JEV's pick.
  * jev_calibration_summary.txt — accuracy, guardrail pass-rate vs baseline.

This is exploratory: the current cache has complete variant data for only
~8 answerable questions (rate limits truncated the live runs).  Treat results
as directional, not production-grade, until a larger labeled set exists.

JEV is *not* actually called here (rate limits / cost).  Instead, this script
defines the candidate framing and scoring rule that the live JEV mode would
use, and simulates what JEV *would* pick if it perfectly learned the oracle.
That gives an upper bound on achievable accuracy and identifies which
questions are inherently ambiguous (no variant passes all guardrails).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.benchmark import load_questions

RESULTS_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "brevity_ab_test.json"
OUT_JSON = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "jev_calibration.json"
OUT_TXT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "jev_calibration_summary.txt"

VARIANT_ORDER = [
    "baseline",
    "step2_tight_guardrail",
    "step2_ultra_compact",
    "step2_qtype",
    "step2_guardrail_confident",
    "step2_guardrail_compact",
    "step2_guardrail",
    "step2_both",
    "step2_contract",
    "step2_step2",
]

CORRECTNESS_DROP_LIMIT = 0.02
ABSTENTION_INCREASE_LIMIT = 0.10
BREVITY_REDUCTION_TARGET = -15.0  # percent


def _oracle_pick(rows: list[dict], baseline_soft: float) -> dict | None:
    """Oracle: shortest among correctness-eligible, non-abstaining preferred."""
    correct_ok = [
        r for r in rows
        if r["answer_correctness"] >= baseline_soft - CORRECTNESS_DROP_LIMIT
    ]
    if not correct_ok:
        return None
    non_abstain = [r for r in correct_ok if not r["abstained"]]
    pool = non_abstain if non_abstain else correct_ok
    return min(pool, key=lambda r: r["answer_length"])


def main() -> int:
    data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    questions = load_questions()
    qmap = {q.question_id: q for q in questions}

    rows = [r for r in data if r.get("answer") and not r.get("error")]
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_q[r["qid"]].append(r)

    result_qids = {r["qid"] for r in rows}
    answerable_qids = [
        qid for qid in sorted(result_qids) if not qmap[qid].insufficient_evidence
    ]

    # Baseline stats (answerable)
    baseline_rows = [
        r for qid in answerable_qids
        for r in by_q[qid] if r["variant"] == "baseline"
    ]
    if not baseline_rows:
        print("No baseline rows found.")
        return 1
    bl_soft = statistics.mean(r["answer_correctness"] for r in baseline_rows)
    bl_ab = sum(r["abstained"] for r in baseline_rows) / len(baseline_rows)
    bl_len = statistics.mean(r["answer_length"] for r in baseline_rows)

    print(f"Baseline (answerable, n={len(baseline_rows)}): "
          f"soft={bl_soft:.4f} abstain={bl_ab:.1%} len={bl_len:.0f}")
    print()

    matches = 0
    total = 0
    jev_pass = 0
    jev_rows = []

    for qid in answerable_qids:
        qrows = by_q[qid]
        baseline_row = next((r for r in qrows if r["variant"] == "baseline"), None)
        if not baseline_row:
            continue
        b_soft = baseline_row["answer_correctness"]

        oracle = _oracle_pick(qrows, b_soft)
        if oracle is None:
            continue  # no variant met correctness — skip from accuracy

        # Simulated JEV pick: the variant with the best guardrail profile,
        # approximated by the same oracle rule (upper bound on JEV accuracy).
        # In the real JEV path, this is replaced by the Decisions API call.
        jev_pick = oracle  # placeholder: perfect foresight

        total += 1
        if jev_pick["variant"] == oracle["variant"]:
            matches += 1

        # Guardrail check on JEV's pick vs baseline
        dl = (jev_pick["answer_length"] - bl_len) / bl_len * 100
        ds = jev_pick["answer_correctness"] - bl_soft
        dab = (1.0 if jev_pick["abstained"] else 0.0) - bl_ab
        pass_all = (dl <= BREVITY_REDUCTION_TARGET and
                    ds >= -CORRECTNESS_DROP_LIMIT and
                    dab <= ABSTENTION_INCREASE_LIMIT)
        if pass_all:
            jev_pass += 1

        jev_rows.append({
            "qid": qid,
            "oracle_variant": oracle["variant"],
            "jev_variant": jev_pick["variant"],
            "match": jev_pick["variant"] == oracle["variant"],
            "jev_len": jev_pick["answer_length"],
            "jev_soft": jev_pick["answer_correctness"],
            "jev_abstained": jev_pick["abstained"],
            "delta_len_pct": dl,
            "delta_soft": ds,
            "delta_abstain_pp": dab * 100,
            "pass_all_guardrails": pass_all,
        })

    accuracy = matches / total if total else 0.0
    jev_pass_rate = jev_pass / total if total else 0.0

    print(f"Questions with complete variant data: {total}")
    print(f"JEV-vs-oracle accuracy: {accuracy:.1%} ({matches}/{total})")
    print(f"JEV pick passes all guardrails: {jev_pass_rate:.1%} ({jev_pass}/{total})")
    print()
    print("Per-question:")
    for r in jev_rows:
        print(f"  {r['qid']}: oracle={r['oracle_variant']} jev={r['jev_variant']} "
              f"match={r['match']} pass_all={r['pass_all_guardrails']} "
              f"len={r['jev_len']} soft={r['jev_soft']:.3f} abstain={r['jev_abstained']}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({
            "baseline": {
                "n": len(baseline_rows),
                "soft": bl_soft,
                "abstain": bl_ab,
                "length": bl_len,
            },
            "n_questions": total,
            "accuracy": accuracy,
            "jev_guardrail_pass_rate": jev_pass_rate,
            "matches": matches,
            "jev_pass": jev_pass,
            "rows": jev_rows,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    OUT_TXT.write_text(
        f"JEV variant-selection calibration (offline, simulated)\n"
        f"========================================================\n"
        f"Questions with complete variant data: {total}\n"
        f"JEV-vs-oracle accuracy: {accuracy:.1%} ({matches}/{total})\n"
        f"JEV pick passes all guardrails: {jev_pass_rate:.1%} ({jev_pass}/{total})\n"
        f"\nBaseline (answerable): soft={bl_soft:.4f} abstain={bl_ab:.1%} len={bl_len:.0f}\n"
        f"\nNote: JEV not actually called. 'jev_pick' uses the oracle rule "
        f"(perfect foresight) = upper bound on achievable accuracy.\n"
        f"A real JEV call will score <= this upper bound.\n",
        encoding="utf-8",
    )
    print(f"\nWrote {OUT_JSON} and {OUT_TXT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
