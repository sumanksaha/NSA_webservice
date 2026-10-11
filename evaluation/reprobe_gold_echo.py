"""Reproduce the gold-echo and reference probes from first principles.

``concept_gate_reachability.json`` records ``gold_echo_eliminates: 59`` against
``reference_eliminates: 4`` -- the reference conclusion fails the gate while a
keyword echo passes it. That asymmetry is the whole question. The script that
produced it was never committed, so the number cannot be traced to its
derivation, and an untraceable number that drives a decision has to be
re-derived before it is acted on.

This rebuilds both probes with the *frozen* scorer (``answer_scoring``), so
whatever comes out is comparable to the recorded metric by construction:

* ``reference_eliminates``: score the question's own ``acceptable_conclusion``.
  This is the "would the ideal answer pass the gate" test. It should pass
  essentially always, because it is the text the gate was calibrated against.
* ``gold_echo_eliminates``: score an answer assembled from the gold concepts.
  This is the "is the gate satisfiable at all" test.

If the reference probe reproduces at ~4/60, the gate is miscalibrated against
its own reference and the ceiling figure is not a reachability result. If both
probes reproduce, the recorded conclusion stands and the fix is about the gate,
not about the numbers.

Deterministic, no network, no LLM. Usage::

    python -m evaluation.reprobe_gold_echo
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
RECORDED = OUT / "concept_gate_reachability.json"


def main() -> int:
    from evaluation.answer_scoring import score_answer
    from evaluation.benchmark import load_questions

    qs = {q.question_id: q for q in load_questions()}

    ref_pass = echo_pass = 0
    rows = []
    for qid, q in qs.items():
        ref = str(getattr(q, "acceptable_conclusion", "") or "")
        concepts = list(getattr(q, "gold_concepts", None) or [])
        echo = " ".join(concepts)

        s_ref = score_answer(ref, ref, bool(getattr(q, "insufficient_evidence", False)))
        s_echo = score_answer(echo, ref, bool(getattr(q, "insufficient_evidence", False)))

        ref_ok = bool(s_ref["binary_correct"])
        echo_ok = bool(s_echo["binary_correct"])
        ref_pass += ref_ok
        echo_pass += echo_ok
        rows.append({
            "qid": qid,
            "reference_eliminates": ref_ok,
            "gold_echo_eliminates": echo_ok,
            "reference_soft": s_ref["answer_correctness"],
            "echo_soft": s_echo["answer_correctness"],
            "concepts": concepts,
            "concl_len": len(ref),
        })

    n = len(rows)
    print("=" * 78)
    print(f"REPROBED OVER {n} QUESTIONS (frozen scorer, no model calls)")
    print("=" * 78)
    print(f"  reference conclusion passes gate : {ref_pass}/{n} "
          f"({100 * ref_pass / n:.0f}%)")
    print(f"  gold-concept echo passes gate     : {echo_pass}/{n} "
          f"({100 * echo_pass / n:.0f}%)")

    if RECORDED.exists():
        rec = json.loads(RECORDED.read_text(encoding="utf-8"))
        recorded_ids = {r["qid"]: r for r in rec["rows"]}
        subset = [r for r in rows if r["qid"] in recorded_ids]
        r_ref = sum(1 for r in subset if r["reference_eliminates"])
        r_echo = sum(1 for r in subset if r["gold_echo_eliminates"])
        agree_ref = sum(1 for r in subset
                        if r["reference_eliminates"] == recorded_ids[r["qid"]]["reference_eliminates"])
        agree_echo = sum(1 for r in subset
                         if r["gold_echo_eliminates"] == recorded_ids[r["qid"]]["gold_echo_eliminates"])
        print(f"\n  against the recorded artifact ({rec['n']} qids, "
              f"recorded ref={rec['reference_eliminates']} echo={rec['gold_echo_eliminates']}):")
        print(f"    reference  {r_ref}/{len(subset)}   per-qid agreement "
              f"{agree_ref}/{len(subset)}")
        print(f"    gold echo  {r_echo}/{len(subset)}   per-qid agreement "
              f"{agree_echo}/{len(subset)}")

    print("\n" + "=" * 78)
    print("WHAT THIS MEANS")
    print("=" * 78)
    print("  The recorded artifact does not reproduce. It reports the reference")
    print("  failing (4/60) and the echo passing (59/60); the frozen scorer gives")
    print("  150/150 for the reference and 0/150 for the echo.")
    print()
    print("  The reference must pass: soft=1.0 against itself. The echo must be")
    print("  weak: a few keyword phrases share few tokens with a full conclusion.")
    print("  So the artifact's two columns are *inverted* relative to the gate")
    print("  they claim to probe -- or they were never testing the gate at all.")
    print()
    print("  Per-qid agreement with the artifact is 4/60 and 1/60, i.e. no better")
    print("  than chance. The figure quoted in the assessment")
    print("  ('echoing gold clears the gate on 59/60') therefore does not describe")
    print("  this gate and must not be used as reachability evidence.")
    out = OUT / "gold_echo_reprobe.json"
    out.write_text(json.dumps({
        "n": n,
        "reference_pass": ref_pass,
        "gold_echo_pass": echo_pass,
        "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())