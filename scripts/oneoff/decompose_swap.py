"""Decompose haiku-vs-laguna soft into coverage/jaccard (offline, 0 LLM).

Both swap candidates lost to laguna, but for opposite length reasons if the
mechanism story holds: laguna answers are ~6x the reference (jaccard crushed,
coverage healthy); haiku answers run ~800 chars (possibly coverage-starved).
Scoring the cached answers' coverage and jaccard separately decides whether
the ideal sits between the two models -- i.e. a length-targeted laguna prompt
rather than any swap.
"""
import json
import statistics
from pathlib import Path

OUT = Path("evaluation/out/ceiling_v5/reeval_89")

ab = [json.loads(l) for l in open(OUT / "model_ab.jsonl", encoding="utf-8")]
lag = {}
for p in (OUT / "retry_live.jsonl", OUT / "kg_live_verify.jsonl"):
    if not p.exists():
        continue
    for l in open(p, encoding="utf-8"):
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("cond") in ("base", "veto") and not r.get("error"):
            lag.setdefault(r["qid"], r)

from evaluation.answer_scoring import _tokens
from evaluation.benchmark import load_questions
qs = {q.question_id: q for q in load_questions()}

print(f"{'qid':<6} {'model':<22} {'soft':>6} {'cov':>6} {'jac':>6} {'alen':>6}")
for r in sorted(ab, key=lambda r: (r["qid"], r["cond"])):
    if not (r.get("answer") or "").strip():
        continue
    q = qs[r["qid"]]
    ref = str(q.acceptable_conclusion or "")
    rt, at = _tokens(ref), _tokens(r["answer"])
    inter = len(rt & at)
    cov = inter / len(rt) if rt else 0.0
    jac = inter / len(rt | at) if (rt | at) else 0.0
    print(f"{r['qid']:<6} {r['cond']:<22} "
          f"{float(r.get('answer_correctness') or 0):>6.3f} {cov:>6.3f} {jac:>6.3f} "
          f"{r.get('answer_length', 0):>6}")
    b = lag.get(r["qid"])
    if b and (b.get("answer") or "").strip():
        bt, at2 = rt, _tokens(b["answer"])
        inter2 = len(rt & at2)
        cov2 = inter2 / len(rt) if rt else 0.0
        jac2 = inter2 / len(rt | at2) if (rt | at2) else 0.0
        print(f"{'':<6} {'laguna-base':<22} "
              f"{float(b.get('answer_correctness') or 0):>6.3f} {cov2:>6.3f} {jac2:>6.3f} "
              f"{b.get('answer_length', 0):>6}")
