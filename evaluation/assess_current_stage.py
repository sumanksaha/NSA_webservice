"""Soft and binary correctness over all 150 questions, at the current stage.

No LLM calls. The generation outputs are the recorded ones; what is recomputed
here is the roll-up, split in the ways that matter for deciding whether the
knowledge graph is worth building:

* **by cohort** -- the 89 model_wrong failures and the 61 control questions do
  not move together, and a treatment that only shifts one is a ranking artefact
  rather than a repair;
* **by soft-vs-binary** -- soft tells you how close the answers are, binary tells
  you whether the gate is passed. The gap between them is the actionable part:
  effort that is already nearly correct;
* **by concept shortfall** -- the misses that are one or two concepts away are
  the ones a knowledge graph could plausibly close; the ones further away are not.

Two data-integrity problems this had to work around, both worth knowing about
because they make the naive roll-up wrong in silent ways:

1. Some generation records carry ``cohort: None`` rather than a cohort label, so
   grouping on that field quietly demotes failures to controls. The cohort is
   therefore taken from the retrieval records, which carry it for every qid.
2. ``concepts_missing`` is a list, not a count, so any numeric comparison
   against it raises. Len is taken.

The failure cohort's retrieval has since been re-measured against a remediated
corpus, but its *generation* outputs predate that. So this is the score of the
pipeline as last executed, not a projection; the retrieval delta is reported
separately rather than folded in.

Usage::

    python -m evaluation.assess_current_stage
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
Path(PROJECT_ROOT).mkdir(parents=True, exist_ok=True)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
GEN = OUT / "gen_C_hybrid.jsonl"
GEN_SCOPED = OUT / "gen_C_hybrid_section.jsonl"
RETR = OUT / "retrieval.jsonl"

N_FAIL, N_CTRL = 89, 61


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    base, scoped = load(GEN), load(GEN_SCOPED)
    if not base:
        raise SystemExit(f"no generation results at {GEN}")
    retr = {r["qid"]: r for r in load(RETR)}

    print("=" * 78)
    print("SOFT vs BINARY CORRECTNESS, ALL 150 QUESTIONS")
    print("=" * 78)
    print(f"source: {GEN.name} ({len(base)}) / {GEN_SCOPED.name} ({len(scoped)})\n")

    print("--- arm: C_hybrid  (baseline)")
    _report(base, retr, N_FAIL, N_CTRL, indent="    ")
    print("\n--- arm: C_hybrid_section  (scoped)")
    _report(scoped, retr, N_FAIL, N_CTRL, indent="    ")

    print("\n" + "=" * 78)
    print("FAILURE COHORT - HOW CLOSE ARE THE MISSES?")
    print("=" * 78)
    fails = [r for r in base if cohort_of(r, retr) == "failure"]
    passing = [r for r in fails if r.get("binary_correct")]
    print(f"binary-correct {len(passing)}/{len(fails)}   "
          f"incorrect {len(fails) - len(passing)}/{len(fails)}\n")

    buckets = Counter()
    for r in fails:
        if r.get("binary_correct"):
            continue
        missing = r.get("concepts_missing")
        n = len(missing) if isinstance(missing, list) else (missing or 0)
        if n == 0:
            buckets["0 short but scored wrong"] += 1
        elif n == 1:
            buckets["1 concept short"] += 1
        elif n <= 2:
            buckets["2 concepts short"] += 1
        elif n <= 4:
            buckets["3-4 concepts short"] += 1
        else:
            buckets["5+ concepts short"] += 1
    order = ["0 short but scored wrong", "1 concept short", "2 concepts short",
             "3-4 concepts short", "5+ concepts short"]
    for k in order:
        if buckets.get(k):
            print(f"    {k:<30} {buckets[k]:>3}")
    reachable = sum(buckets[k] for k in order[1:4])
    print(f"\n    graph-plausible reach (1-4 short): {reachable}")
    print(f"    not reachable by enrichment      : {buckets['5+ concepts short']}")

    print("\n" + "=" * 78)
    print("RETRIEVAL STATE, SAME QUESTIONS (re-measured post-remediation)")
    print("=" * 78)
    for name in ("retrieval.preremediation.jsonl", "retrieval.jsonl"):
        p = OUT / name
        if not p.exists():
            continue
        recs = {json.loads(l)["qid"]: json.loads(l)
                for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}
        parts = []
        for cohort in ("failure", "control"):
            sub = [q for q, r in recs.items() if (r.get("cohort") or "") == cohort]
            win = sum(1 for q in sub if (recs[q].get("C_hybrid") or {}).get("in_window"))
            parts.append(f"{cohort}: gold-in-top10 {win}/{len(sub)}")
        print(f"  {name:<36} {'  |  '.join(parts)}")

    print("\n" + "=" * 78)
    print("READ-OUT FOR THE GRAPH DECISION")
    print("=" * 78)
    print("  * soft-vs-binary gap is the head-room: nearly-right answers that")
    print("    fail the gate. That is what more context can move.")
    print("  * 1-4 concepts short is the graph's plausible reach; 5+ is not.")
    print("  * generation is the pre-remediation cache and cannot be refreshed")
    print("    without an LLM budget; retrieval above IS post-remediation.")
    return 0


def cohort_of(rec: dict, retr: dict) -> str:
    """Cohort from the retrieval record, which labels every qid.

    Generation records sometimes carry ``cohort: None``; grouping on that field
    silently demotes failures into the control totals, which would make the
    report look better than it is.
    """
    return (retr.get(rec.get("qid")) or {}).get("cohort") or rec.get("cohort") or "control"


def _report(recs, retr, n_fail, n_ctrl, indent):
    for cohort, total in (("failure", n_fail), ("control", n_ctrl)):
        sub = [r for r in recs if cohort_of(r, retr) == cohort]
        softs = [float(r.get("answer_correctness") or 0.0) for r in sub]
        binary = [r for r in sub if r.get("binary_correct")]
        abst = [r for r in sub if r.get("abstained")]
        abcred = [r for r in sub if r.get("binary_correct_abstention_credit")]
        print(f"{indent}{cohort:<9} n={len(sub):>3}/{total}  soft mean "
              f"{statistics.mean(softs):.3f}  median {statistics.median(softs):.3f}  "
              f"range {min(softs):.2f}-{max(softs):.2f}")
        print(f"{indent}{'':9}  binary {len(binary):>3}/{len(sub)} "
              f"({100 * len(binary) / len(sub):5.1f}%)   abstained {len(abst):>3}  "
              f"abstention-credited {len(abcred):>3}")


if __name__ == "__main__":
    raise SystemExit(main())