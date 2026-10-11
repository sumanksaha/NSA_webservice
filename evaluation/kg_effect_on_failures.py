"""Does the knowledge graph move the metric, on the questions that fail?

This is the experiment the KG's value rests on, and it has never been run on the
questions that matter.

``run_kg_contribution`` measured it on five *food-intent* questions where
retrieval recall was already 1.0. On those, the gold clause was already in the
retrieved chunks, so the KG's injected provisions could not be observed doing
anything -- ``kg_injected_gold`` was 0 by construction. A contribution
measurement on a population where retrieval already succeeded cannot detect a
contribution, and that is what that result is.

The 89 model_wrong failures are the opposite population: gold-in-top10 is
45/89 (51%), so there is room for anything to add something. That is where the
KG's mechanism -- generation-time RRF fusion of expanded provisions into the LLM
context, which does *not* re-rank retrieval -- can actually be seen.

Measured per question, offline, no LLM:

* ``ctx_cov``  -- distinctive coverage of the reference by the retrieved context
  alone (the pipeline as it stands);
* ``kg_cov``   -- the same, with the KG's injected provision text added;
* ``delta``    -- what the KG contributes to the metric's input.

``delta`` is not soft/binary correctness -- that needs an LLM answer, and the
ll budget is zero. It is the input to it: if the KG does not raise the
distinctive coverage of the context, no LLM can convert it into correctness.

Drives the production ``KGContextExpander`` unchanged.

Usage::

    python -m evaluation.kg_effect_on_failures
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from math import log
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
REPORT = OUT / "kg_effect.json"

#: Legal boilerplate -- see decide_gate_or_answers for why this set and not a
#: plain stopword list. Shared, not copied, so the two measurements cannot drift.
BOILERPLATE = {
    "shall", "may", "section", "sections", "act", "food", "the", "and", "or",
    "of", "in", "to", "for", "any", "such", "be", "is", "are", "as", "by",
    "with", "that", "not", "no", "person", "if", "under", "thereof", "provided",
    "fine", "officer", "authority", "rule", "rules", "regulation",
    "regulations", "inserted", "omitted", "substituted", "words", "clause",
    "sub", "subsection", "proviso", "namely", "following", "subject",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", default="failure",
                    choices=("failure", "control", "all"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from kg.hybrid import KGContextExpander

    expander = KGContextExpander()
    if not expander.configured():
        print("Neo4j is not configured: NEO4J_URI/USERNAME/PASSWORD are required")
        print("in .env before the KG's contribution can be measured.")
        return 1

    # Reachability is checked before anything else, because an unreachable graph
    # and a reachable-but-empty graph both produce "the KG adds nothing", and
    # only one of those is a statement about the KG.
    try:
        probe = expander._execute("MATCH (c:Chunk) RETURN count(c) AS n")
        n_chunks = probe[0]["n"] if probe else 0
        print(f"KG reachable: {n_chunks} chunk nodes in the graph")
        if n_chunks == 0:
            print("The graph has no Chunk nodes, so expansion cannot match")
            print("anything. Rebuild with scripts/build_kg_corpus.py first.")
            print("A zero contribution here would mean the KG is absent, not")
            print("that the KG is useless.")
            return 1
    except Exception as exc:  # noqa: BLE001
        print(f"KG unreachable: {type(exc).__name__}: {exc}")
        print()
        print("This is the blocker. The configured Aura instance")
        print(f"  {os.environ.get('NEO4J_URI')}")
        print("does not resolve in DNS from this environment, so no KG")
        print("measurement, and no KG optimisation, can be performed against it.")
        return 1

    from evaluation.answer_scoring import _tokens
    from evaluation.bench_p0_1_packing import _load_payload_index
    from evaluation.benchmark import load_questions
    from evaluation.decide_gate_or_answers import BOILERPLATE as _BP  # noqa: F401

    mirror = _load_payload_index()
    qs = {q.question_id: q for q in load_questions()}
    retr = {r["qid"]: r for r in (
        json.loads(l) for l in (OUT / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip())}

    df: dict[str, int] = {}
    n_docs = 0
    for p in mirror.values():
        t = p.get("chunk_text") or p.get("text") or ""
        if not t:
            continue
        n_docs += 1
        for tok in _tokens(t):
            df[tok] = df.get(tok, 0) + 1
    idf = {t: log(n_docs / c) for t, c in df.items()}

    ids = [q for q, r in retr.items()
           if args.cohort == "all" or r.get("cohort") == args.cohort]
    ids.sort()
    if args.limit:
        ids = ids[: args.limit]
    print(f"\nmeasuring {len(ids)} questions (cohort={args.cohort})\n")

    rows = []
    for i, qid in enumerate(ids, 1):
        q = qs.get(qid)
        r = retr.get(qid) or {}
        if q is None:
            continue
        ref = str(getattr(q, "acceptable_conclusion", "") or "")
        if not ref:
            continue

        chunk_ids = ((r.get("C_hybrid") or {}).get("chunk_ids") or [])[: args.top_k]
        ctx_text = " ".join(
            (mirror.get(c) or {}).get("chunk_text")
            or (mirror.get(c) or {}).get("text") or ""
            for c in chunk_ids
        )

        kg = expander.expand_chunks(chunk_ids)
        kg_provs = kg.get("provisions") or []
        kg_text = " ".join(
            f"{p.get('title') or ''} {p.get('text') or ''}" for p in kg_provs
        )

        rt = _tokens(ref)
        weighted = {t: idf.get(t, log(n_docs / 1)) for t in rt}
        w_boiler = sum(v for t, v in weighted.items() if t in BOILERPLATE)
        w_total = (sum(weighted.values()) or 1.0) - w_boiler

        def cov(text: str) -> float:
            toks = _tokens(text)
            hit = sum(v for t, v in weighted.items()
                      if t not in BOILERPLATE and t in toks)
            return hit / w_total if w_total > 0 else 0.0

        c_ctx, c_kg = cov(ctx_text), cov(ctx_text + " " + kg_text)
        rows.append({
            "qid": qid,
            "cohort": r.get("cohort"),
            "ctx_cov": round(c_ctx, 4),
            "kg_cov": round(c_kg, 4),
            "delta": round(c_kg - c_ctx, 4),
            "kg_enabled": bool(kg.get("enabled")),
            "kg_matched_chunks": kg.get("matched_chunks"),
            "kg_provisions": len(kg_provs),
            "kg_error": kg.get("error"),
            "already_correct_ctx": c_ctx >= 0.5,
        })
        if i % 25 == 0:
            print(f"  {i}/{len(ids)}")

    d = [r["delta"] for r in rows]
    gained = [r for r in rows if r["delta"] > 0.01]
    crossed = [r for r in rows
               if not r["already_correct_ctx"] and r["kg_cov"] >= 0.5]
    print("\n" + "=" * 74)
    print("KG CONTRIBUTION TO CONTEXT COVERAGE")
    print("=" * 74)
    print(f"  questions                : {len(rows)}")
    print(f"  mean context coverage    : {statistics.mean([r['ctx_cov'] for r in rows]):.3f}")
    print(f"  mean with KG             : {statistics.mean([r['kg_cov'] for r in rows]):.3f}")
    print(f"  mean delta               : {statistics.mean(d):+.4f}")
    print(f"  positive on              : {sum(1 for x in d if x > 0.01)}/{len(rows)}")
    print(f"  questions where the KG crosses the 0.5 line: {len(crossed)}")
    if crossed:
        print(f"    {[r['qid'] for r in crossed]}")
    errs = [r for r in rows if r.get("kg_error")]
    print(f"  expansion errors         : {len(errs)}")
    if errs[:3]:
        for r in errs[:3]:
            print(f"    {r['qid']}: {r['kg_error']}")

    print("\n" + "=" * 74)
    print("READ-OUT")
    print("=" * 74)
    if statistics.mean(d) > 0.01:
        print("  The KG does raise the distinctive coverage of the context that")
        print("  reaches the LLM. That is the precondition for it helping binary")
        print("  and soft correctness -- not proof, since an LLM still has to use")
        print("  it. Route: enable the KG arm, re-run generation, compare.")
    else:
        print("  The KG does not raise the context's distinctive coverage for")
        print("  these questions. Whatever else it does, it is not adding the")
        print("  content the references ask for, so it cannot be expected to move")
        print("  binary or soft correctness. Optimising the graph's structure")
        print("  before fixing this would be tuning a channel that carries")
        print("  nothing.")

    REPORT.write_text(json.dumps({
        "cohort": args.cohort, "n": len(rows),
        "mean_ctx_cov": statistics.mean([r["ctx_cov"] for r in rows]) if rows else None,
        "mean_kg_cov": statistics.mean([r["kg_cov"] for r in rows]) if rows else None,
        "mean_delta": statistics.mean(d) if d else None,
        "crossed_half": [r["qid"] for r in crossed],
        "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())