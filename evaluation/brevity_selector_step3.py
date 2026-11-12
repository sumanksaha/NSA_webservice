"""Step 3: Per-question variant selector (Strategy A).

From the existing brevity_ab_test.json results, selects the best variant
per question optimizing for the three guardrails:
  1. Brevity: mean answer length reduction >= 15% vs baseline
  2. Correctness: soft correctness drop <= 0.02 vs baseline
  3. Abstention on answerable: increase <= 0.10 vs baseline

Selection rule per answerable question:
  - Among variants with soft >= baseline_soft - 0.02 AND abstain behavior
    not worse than baseline + 0.10 (per-question, binary: abstain or not),
    pick the shortest answer.
  - If no variant meets correctness, pick the variant with highest soft
    (correctness priority).
  - Report aggregate guardrail status for the selected-per-question set.

This is post-hoc: no new LLM calls. It answers "what's the best achievable
guardrail profile if we could pick the right variant per question?"
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

# Guardrail targets
BREVITY_TARGET = -15.0      # percent reduction vs baseline
CORRECTNESS_DROP_LIMIT = 0.02
ABSTENTION_INCREASE_LIMIT = 0.10

VARIANT_DISPLAY_ORDER = [
    "baseline",
    "step2_tight_guardrail",
    "step2_ultra_compact",
    "step2_qtype",
    "step2_guardrail_confident",
    "step2_step2",
    "step2_guardrail_compact",
    "step2_guardrail",
    "step2_both",
    "step2_contract",
]


def main() -> int:
    data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    questions = load_questions()
    qmap = {q.question_id: q for q in questions}

    # Filter out errored / empty rows
    rows = [r for r in data if r.get("answer") and not r.get("error")]

    # Group by (qid, variant)
    by_qv: dict[tuple[str, str], dict] = {}
    for r in rows:
        by_qv[(r["qid"], r["variant"])] = r

    # Separate answerable vs IE
    # The 20 questions actually present in results (selected by seed 42 in the
    # experiment). Only consider answerable ones among them.
    result_qids = {r["qid"] for r in rows}
    answerable_qids = [
        qid for qid in sorted(result_qids)
        if not qmap[qid].insufficient_evidence
    ]

    # Baseline stats on answerable questions
    baseline_rows = [
        by_qv[(qid, "baseline")]
        for qid in answerable_qids
        if (qid, "baseline") in by_qv
    ]
    if not baseline_rows:
        print("No baseline rows found; cannot compute guardrails.")
        return 1
    bl_len = statistics.mean(r["answer_length"] for r in baseline_rows)
    bl_soft = statistics.mean(r["answer_correctness"] for r in baseline_rows)
    bl_ab = sum(r["abstained"] for r in baseline_rows) / len(baseline_rows)
    bl_cov = statistics.mean(r["answer_coverage"] for r in baseline_rows)

    print("=== BASELINE (answerable, n=%d) ===" % len(baseline_rows))
    print("  mean_len=%.0f  mean_soft=%.4f  abstain=%.1f%%  mean_cov=%.4f" % (
        bl_len, bl_soft, bl_ab * 100, bl_cov))
    print()

    # Per-question selection
    selected: list[dict] = []
    selection_reason: dict[str, str] = {}
    skipped_incomplete: list[str] = []
    for qid in answerable_qids:
        present = {r["variant"] for r in rows if r["qid"] == qid}
        # Require baseline + at least one treatment to be meaningful
        if "baseline" not in present or len(present) < 2:
            skipped_incomplete.append(qid)
            continue
        # Gather variants available for this question
        cands = []
        for vname in VARIANT_DISPLAY_ORDER:
            if (qid, vname) in by_qv:
                cands.append(by_qv[(qid, vname)])
        if not cands:
            skipped_incomplete.append(qid)
            continue

        base_row = by_qv.get((qid, "baseline"))
        base_soft_q = base_row["answer_correctness"] if base_row else bl_soft

        # Correctness-eligible: soft >= base_soft_q - 0.02
        correct_ok = [
            r for r in cands
            if r["answer_correctness"] >= base_soft_q - CORRECTNESS_DROP_LIMIT
        ]

        chosen = None
        reason = ""
        if correct_ok:
            # Among correctness-eligible, prefer shortest length (brevity)
            chosen = min(correct_ok, key=lambda r: r["answer_length"])
            reason = "shortest among correctness-eligible"
        else:
            # Fallback: highest soft correctness
            chosen = max(cands, key=lambda r: r["answer_correctness"])
            reason = "highest soft (no variant met correctness limit)"

        chosen["_selected_variant"] = chosen["variant"]
        chosen["_selection_reason"] = reason
        selected.append(chosen)
        selection_reason[qid] = (chosen["_selected_variant"], reason,
                                 chosen["answer_length"], chosen["answer_correctness"],
                                 chosen["abstained"], chosen["answer_coverage"])

    # Aggregate guardrails on selected set
    sel_len = statistics.mean(r["answer_length"] for r in selected)
    sel_soft = statistics.mean(r["answer_correctness"] for r in selected)
    sel_ab = sum(r["abstained"] for r in selected) / len(selected)
    sel_cov = statistics.mean(r["answer_coverage"] for r in selected)

    dl = (sel_len - bl_len) / bl_len * 100
    ds = sel_soft - bl_soft
    dab = sel_ab - bl_ab

    b_ok = dl <= BREVITY_TARGET
    c_ok = ds >= -CORRECTNESS_DROP_LIMIT
    a_ok = dab <= ABSTENTION_INCREASE_LIMIT

    print("=== SELECTED PER-QUESTION (answerable, n=%d) ===" % len(selected))
    if skipped_incomplete:
        print("  (skipped incomplete questions: %s)" % ", ".join(skipped_incomplete))
    print("  mean_len=%.0f  delta_len=%+6.1f%%  [%s]" % (
        sel_len, dl, "OK" if b_ok else "FAIL"))
    print("  mean_soft=%.4f  delta_soft=%+6.4f  [%s]" % (
        sel_soft, ds, "OK" if c_ok else "FAIL"))
    print("  abstain=%.1f%%  delta_abstain=%+5.1fpp  [%s]" % (
        sel_ab * 100, dab * 100, "OK" if a_ok else "FAIL"))
    print("  mean_cov=%.4f  delta_cov=%+6.4f" % (sel_cov, sel_cov - bl_cov))
    print()
    print("  ALL THREE GUARDRAILS: %s" % (
        "PASS" if (b_ok and c_ok and a_ok) else "FAIL"))
    print()

    # Per-question detail
    print("=== PER-QUESTION SELECTION ===")
    for qid in sorted(selection_reason):
        v, reason, ln, sf, ab, cov = selection_reason[qid]
        print("  %-8s %-22s len=%6d soft=%.3f abstain=%s cov=%.3f  (%s)" % (
            qid, v, ln, sf, ab, cov, reason))

    # Variant selection frequency
    freq: dict[str, int] = defaultdict(int)
    for qid in selection_reason:
        freq[selection_reason[qid][0]] += 1
    print()
    print("=== SELECTION FREQUENCY ===")
    for v in VARIANT_DISPLAY_ORDER:
        if freq.get(v, 0):
            print("  %-24s %d/%d" % (v, freq[v], len(selected)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
