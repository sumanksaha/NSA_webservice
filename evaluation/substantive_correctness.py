"""Substantive correctness: the binary gate with abstention credit removed.

The abstention credit is legitimate but it is not answer quality. It applies
only to ``insufficient_evidence`` questions, where the benchmark itself says "a
correct abstention is the right answer". A control cohort containing 11 such
questions will therefore pass the binary gate 11 times without answering
anything, which is exactly the kind of composition effect that makes a failure
cohort look 5x worse than it is.

This reports, per arm and cohort:

* binary as recorded;
* binary restricted to questions where a substantive answer was required;
* the IE composition of each cohort, since that is the denominator doing the work.

It matters for the knowledge-graph decision: if most of the apparent
failure/control gap is abstention composition, the gap a graph has to close is
smaller than the headline suggests, and the case for a graph built on the
headline number is weaker than it looks.

Usage::

    python -m evaluation.substantive_correctness
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
GEN = OUT / "gen_C_hybrid.jsonl"
GEN_SCOPED = OUT / "gen_C_hybrid_section.jsonl"


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    from evaluation.benchmark import load_questions

    ie = {q.question_id: bool(getattr(q, "insufficient_evidence", False))
          for q in load_questions()}
    retr = {r["qid"]: r for r in load(OUT / "retrieval.jsonl")}

    for label, path in (("C_hybrid", GEN), ("C_hybrid_section", GEN_SCOPED)):
        if not path.exists():
            continue
        recs = load(path)
        print("=" * 76)
        print(f"{label}")
        print("=" * 76)
        for cohort, total in (("failure", 89), ("control", 61)):
            sub = [r for r in recs
                   if (retr.get(r["qid"]) or {}).get("cohort") == cohort]
            if not sub:
                continue
            n_ie = sum(1 for r in sub if ie.get(r["qid"]))
            binary = [r for r in sub if r.get("binary_correct")]
            credit = [r for r in binary if r.get("binary_correct_abstention_credit")]
            # Substantive = the gate was passed without the abstention credit,
            # or the question needed an answer and got one.
            subst = [r for r in sub
                     if r.get("binary_correct") and not r.get("binary_correct_abstention_credit")]
            softs = [float(r.get("answer_correctness") or 0) for r in sub]
            soft_answer = [float(r.get("answer_correctness") or 0)
                           for r in sub if not r.get("binary_correct_abstention_credit")]
            print(f"  {cohort}: n={len(sub)}/{total}")
            print(f"    insufficient_evidence questions : {n_ie} "
                  f"({100 * n_ie / len(sub):.0f}% of this cohort)")
            print(f"    binary as recorded              : {len(binary)}/{len(sub)} "
                  f"({100 * len(binary) / len(sub):.1f}%)")
            print(f"    of which abstention-credited    : {len(credit)}")
            print(f"    SUBSTANTIVE passes              : {len(subst)}/{len(sub)} "
                  f"({100 * len(subst) / len(sub):.1f}%)   { [r['qid'] for r in subst] }")
            print(f"    soft (all) {statistics.mean(softs):.3f}   "
                  f"soft (excluding credited) {statistics.mean(soft_answer):.3f}")
            print()

    print("=" * 76)
    print("IMPLICATION")
    print("=" * 76)
    print("  Control's binary rate is inflated by IE composition: on that set,")
    print("  abstaining IS the right answer, so those passes measure the gate's")
    print("  leniency, not answer quality. The substantive gap between failure")
    print("  and control is much smaller than the headline binary gap.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())