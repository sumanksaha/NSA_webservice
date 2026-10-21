"""Segment the 85 incorrect failures by bottleneck (offline, 0 LLM calls).

Each incorrect failure QID is joined across the recorded artifacts and assigned
the bottleneck that best explains its missing concepts:

* RETRIEVAL   -- gold not in the top-10 window. The answer cannot contain what
  the context never held. Treatment lives in retrieval/ranking/retry.
* GENERATION  -- gold in window AND context covers the reference's distinctive
  mass well above the answer (retrieval_headroom > 0.10). The content arrived;
  the model dropped it. Treatment lives in prompting/concision/structure.
* CORPUS      -- gold in window but even the gold provisions cover little
  (gold_distinctive_cov < 0.30) and the context is no better. The reference
  draws on material outside the labelled gold. Treatment is benchmark/corpus
  work (gold-labelling check, s52/s63-style procurement), not pipeline.
* GATE_EDGE   -- 0 concepts missing but still scored wrong. The metric, the
  authority requirement, or abstention handling failed a substantively complete
  answer. Treatment lives in scoring/adjudication, never in retrieval.
* MIXED       -- gold in window, middling headroom. Needs per-question reading;
  reported as a list, not averaged away.

The veto set (gains that must not be lost) is printed alongside: substantive
passes, IE abstention credits, the Q052 live pass, and the scoped-arm
regressed controls. Any treatment that cannot prove it leaves these untouched
is a proposal, not a plan.

Usage: python -m evaluation.segment_shortfall
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    gen = {r["qid"]: r for r in load_jsonl(OUT / "gen_C_hybrid.jsonl")}
    retr = {r["qid"]: r for r in load_jsonl(OUT / "retrieval.jsonl")}
    verdict = {r["qid"]: r for r in
               json.loads((OUT / "gate_or_answers_verdict.json").read_text(encoding="utf-8"))["rows"]}
    kg = {r["qid"]: r for r in
          json.loads((OUT / "kg_effect.json").read_text(encoding="utf-8"))["rows"]}
    headroom = {d["qid"]: d for d in
                json.loads((OUT / "answer_quality_headroom.json").read_text(encoding="utf-8"))["detail"]}

    incorrect = [q for q, r in gen.items()
                 if (retr.get(q) or {}).get("cohort") == "failure" and not r.get("binary_correct")]
    print(f"incorrect failures: {len(incorrect)}")

    segments: dict[str, list[str]] = Counter()
    rows = []
    for qid in sorted(incorrect):
        g, r = gen[qid], retr.get(qid, {})
        v, k, h = verdict.get(qid, {}), kg.get(qid, {}), headroom.get(qid)
        missing = g.get("concepts_missing") or []
        in_win = bool((r.get("C_hybrid") or {}).get("in_window"))
        rank = (r.get("C_hybrid") or {}).get("best_gold_rank")
        rh = float(v.get("retrieval_headroom") or 0)
        gd = float(v.get("gold_distinctive_cov") or 0)
        if not missing:
            seg = "GATE_EDGE"
        elif not in_win:
            seg = "RETRIEVAL"
        elif rh > 0.10:
            seg = "GENERATION"
        elif gd < 0.30:
            seg = "CORPUS"
        else:
            seg = "MIXED"
        segments[seg] += 1
        rows.append({
            "qid": qid, "segment": seg,
            "n_missing": len(missing), "missing": missing,
            "in_window": in_win, "rank": rank,
            "soft": g.get("answer_correctness"),
            "authority_hit": g.get("authority_hit"),
            "abstained": g.get("abstained"),
            "retrieval_headroom": round(rh, 3),
            "gold_cov": round(gd, 3),
            "kg_delta": (k.get("delta") if k else None),
            "verdict": v.get("verdict"),
        })

    print("\n=== SEGMENTS ===")
    for seg, n in segments.most_common():
        qs = [r["qid"] for r in rows if r["segment"] == seg]
        print(f"  {seg:<12} {n:>3}  {qs}")

    print("\n=== RETRIEVAL: rank distribution (is gold near or far?) ===")
    retr_ranks = sorted(r["rank"] for r in rows if r["segment"] == "RETRIEVAL" and r["rank"])
    print(f"  n={len(retr_ranks)} ranks: min={min(retr_ranks) if retr_ranks else '-'} "
          f"median={sorted(retr_ranks)[len(retr_ranks)//2] if retr_ranks else '-'} "
          f"max={max(retr_ranks) if retr_ranks else '-'}")
    print(f"  rank<=20 (loose window, one retry could reach): "
          f"{sum(1 for x in retr_ranks if x <= 20)}")
    print(f"  rank>20 (needs more than a window widen): "
          f"{sum(1 for x in retr_ranks if x > 20)}")
    print(f"  rank None (never retrieved): "
          f"{sum(1 for r in rows if r['segment']=='RETRIEVAL' and not r['rank'])}")

    print("\n=== GENERATION: what was dropped? ===")
    dropped = Counter()
    for r in rows:
        if r["segment"] == "GENERATION":
            for c in r["missing"]:
                dropped[c] += 1
    for c, n in dropped.most_common(12):
        print(f"  {n:>3}x  {c}")

    print("\n=== GATE_EDGE: why did complete answers fail? ===")
    for r in rows:
        if r["segment"] == "GATE_EDGE":
            print(f"  {r['qid']}: soft={r['soft']} auth={r['authority_hit']} "
                  f"abst={r['abstained']} verdict={r['verdict']}")

    print("\n=== AUTHORITY shortfall inside segments ===")
    for seg in ("RETRIEVAL", "GENERATION", "CORPUS", "MIXED"):
        sub = [r for r in rows if r["segment"] == seg]
        if sub:
            print(f"  {seg:<12} authority missed on "
                  f"{sum(1 for r in sub if not r['authority_hit'])}/{len(sub)}")

    print("\n=== VETO SET (gains that must survive) ===")
    print("  substantive failure passes : Q015, Q024, Q047 (+Q052 live-only pass, fragile)")
    print("  substantive control passes : Q002, Q081, Q093, Q095")
    print("  IE abstention credits      : 11 control + 1 failure (must keep abstaining)")
    print("  scoped-arm regressed ctrls : Q060, Q119, Q128 (veto for structural changes)")

    (OUT / "shortfall_segments.json").write_text(
        json.dumps({"segments": dict(segments), "rows": rows}, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'shortfall_segments.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
