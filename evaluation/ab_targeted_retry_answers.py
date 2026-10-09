"""RAG-TR-001 Stage-2 A/B: does recovered gold flip answers? (live LLM)

Follows the evaluation/ab_window_width.py generation pattern exactly:
same ContextBuilder -> _render_prompt -> _call_llm -> CitationTracker ->
ResponseSanitizer -> score_answer dual scorecard.

Conditions (same model, same evidence pool, only the prompt differs):
  baseline  top-10 cached C_hybrid arm order (pre-retry window)
  retry     baseline UNION V2 TargetPlan lexical matches, capped at 20

Population: the 22 recovered qids from targeted_retry_ab.json by default
(--qids to override, --limit for smoke). Reports binary flip + soft delta
+ citation R/P + groundedness + latency with paired stats.

Budget: 22 qids x 2 arms = 44 calls (well under the 150 cap).

Usage:
    python -m evaluation.ab_targeted_retry_answers --limit 3
    python -m evaluation.ab_targeted_retry_answers
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

from evaluation.answer_scoring import score_answer
from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.resolution import FamilyMap, matches_gold

STAGE1_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "targeted_retry_ab.json"
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "targeted_retry_answers_ab.json"
ARM = "C_hybrid"
BASELINE_K = 10
RETRY_K = 20
MAX_WORKERS = 3

_MARKER = re.compile(r"\[(\d+)\]")


class _Cit:
    __slots__ = ("chunk_id",)

    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id


def _retry_chunks(qtext: str, chunks: list, payload_index: dict) -> tuple[list, dict]:
    """Rebuild the Stage-1 retry pool: baseline + plan lexical matches."""
    from evaluation.ab_targeted_retry import _derive_failures, _plan_match_ids

    from app.rag.planning.targeted_retry import TargetedRetryPlanner

    baseline = chunks[:BASELINE_K]
    base_ids = {c.chunk_id for c in baseline}
    failures = _derive_failures(qtext, False)
    plan = TargetedRetryPlanner().target_plan(
        qtext, failures, "general",
        {"top_k": BASELINE_K, "chunks": [c.to_dict() for c in chunks], "answer": ""},
    )
    additions = _plan_match_ids(plan, chunks[BASELINE_K:], payload_index)
    want = additions[: max(0, RETRY_K - len(baseline))]
    extra = [c for c in chunks[BASELINE_K:] if c.chunk_id in set(want)]
    return baseline + extra, {"arm": plan.arm, "strategy": plan.strategy, "plan_query": plan.query}

def _one(task, payload_index, family_map, questions, retry):
    import time as _time

    from app.rag.generation.grounded_service import GroundedGenerationService

    qid, qtext, chunks = task
    q = questions[qid]
    pool = chunks[:BASELINE_K]
    plan_info = {"arm": "baseline", "strategy": "none", "plan_query": ""}
    if retry:
        pool, plan_info = _retry_chunks(qtext, chunks, payload_index)
    svc = GroundedGenerationService()
    svc.llm_client = SSLBypassLLMClient(model=os.environ.get("RAG_LLM_MODEL", ""))
    qt = (q.question_types or ["general"])[0] if getattr(q, "question_types", None) else "general"
    t0 = _time.monotonic()
    try:
        built = svc.context_builder.build(qtext, pool, query_type=qt)
        sys_p, user_p = svc._render_prompt(qtext, built)
        llm = svc._call_llm(sys_p, user_p)
        answer = getattr(llm, "text", "") or ""
        tracked = svc._extract_citations(llm, pool, built)
        san = svc.sanitizer.sanitize(answer, tracked, pool)
        in_prompt = {c["chunk_id"] for c in built.citations}
        gold = {c.chunk_id for c in pool if any(matches_gold(payload_index.get(c.chunk_id, {}), u, family_map) for u in q.recall_units())}
        m = score_answer(answer, q.acceptable_conclusion or "", q.insufficient_evidence)
        cited = {c.chunk_id for c in tracked}
        m["citation_recall"] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
        m["citation_precision"] = round(len(cited & gold) / max(len(cited), 1), 4) if cited else 0.0
        m["gold_in_prompt"] = int(bool(in_prompt & gold))
        m["groundedness_score"] = san.groundedness_score
        m["hallucination_detected"] = int(san.hallucination_detected)
        m["latency_s"] = round(_time.monotonic() - t0, 1)
        return {"qid": qid, "answer": answer, "m": m, "plan": plan_info, "llm_error": getattr(llm, "error", None)}
    except Exception as exc:
        return {"qid": qid, "exception": f"{type(exc).__name__}: {exc}", "m": None, "plan": plan_info}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--qids", default="")
    ap.add_argument("--arm", default=ARM)
    ap.add_argument("--progress", action="store_true",
                    help="write per-question progress after each completion")
    args = ap.parse_args()
    stage1 = json.loads(STAGE1_FILE.read_text(encoding="utf-8"))
    recovered = stage1.get("recovered_qids", [])
    want = [q.strip() for q in args.qids.split(",") if q.strip()] or recovered
    if args.limit:
        want = want[: args.limit]
    if not want:
        print("no qids (Stage-1 recovered list empty?)", file=sys.stderr)
        return 1
    questions = {q.question_id: q for q in load_questions()}
    payload_index = _load_payload_index()
    family_map = FamilyMap()
    recs = {r["question_id"]: r for r in _load_jsonl(RAW_DIR / f"{args.arm}.jsonl")}
    tasks = []
    for qid in want:
        if qid not in recs or qid not in questions:
            continue
        chunks = _chunks_for([str(c) for c in (recs[qid].get("chunk_ids") or [])], payload_index)
        if len(chunks) < 2:
            continue
        tasks.append((qid, recs[qid].get("query") or questions[qid].question, chunks))
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 1
    print(f"Stage-2 on {len(tasks)} questions, arm={args.arm}, model={os.environ.get('RAG_LLM_MODEL')}", flush=True)
    gate = Semaphore(MAX_WORKERS)

    def run(retry):
        def one(t):
            with gate:
                return _one(t, payload_index, family_map, questions, retry)
        out = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for fut in as_completed([ex.submit(one, t) for t in tasks]):
                try:
                    r = fut.result()
                    out.append(r)
                    if args.progress and r.get("qid"):
                        _P = OUT_FILE.with_suffix("_%s_%s.partial.json" % ("base" if not retry else "retry", r["qid"]))
                        open(_P, "w", encoding="utf-8").write(json.dumps(r, indent=1, ensure_ascii=False))
                except Exception as exc:
                    out.append({"qid": None, "exception": f"{type(exc).__name__}: {exc}", "m": None})
        return out

    B = {r["qid"]: r for r in run(False) if r.get("m")}
    R = {r["qid"]: r for r in run(True) if r.get("m")}
    common = sorted(set(B) & set(R))
    if not common:
        print("ABORT: no paired results", file=sys.stderr)
        return 1
    keys = ("binary_correct", "answer_correctness", "citation_recall", "citation_precision", "groundedness_score", "hallucination_detected", "latency_s")

    def agg(rows, qids):
        return {k: round(sum(rows[q]["m"][k] for q in qids) / max(len(qids), 1), 4) for k in keys}

    ab, ar = agg(B, common), agg(R, common)

    def paired(metric):
        d = [R[q]["m"][metric] - B[q]["m"][metric] for q in common]
        sd = statistics.stdev(d) if len(d) > 1 else 0.0
        se = sd / (len(d) ** 0.5) if sd else 0.0
        return {"n": len(d), "improved": sum(1 for x in d if x > 0), "regressed": sum(1 for x in d if x < 0),
                "net": round(sum(d), 4), "mean_delta": round(statistics.mean(d), 4) if d else None,
                "t": round(statistics.mean(d) / se, 3) if se else None}

    out = {"benchmark": "targeted_retry_stage2_ab", "n_questions": len(common),
           "qids": common, "model": os.environ.get("RAG_LLM_MODEL"),
           "aggregate": {"baseline": ab, "retry": ar},
           "delta": {k: round(ar[k] - ab[k], 4) for k in keys},
           "paired": {k: paired(k) for k in keys},
           "flips_up": sorted(q for q in common if R[q]["m"]["binary_correct"] == 1 and B[q]["m"]["binary_correct"] == 0),
           "flips_down": sorted(q for q in common if R[q]["m"]["binary_correct"] == 0 and B[q]["m"]["binary_correct"] == 1),
           "limitations": ["Live single-model run; soft deltas are noisy.",
                           "Recovered-only population measures recovery, not regression.",
                           "Mechanical binary here; adjudicated overlay in full gate."],
           "per_question": {"baseline": B, "retry": R}}
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("=" * 72)
    print(f"Stage-2 answers A/B  n={len(common)}  model={out['model']}")
    print("=" * 72)
    for k in keys:
        print(f"{k:<24} {ab[k]:>9.4f} {ar[k]:>9.4f} {ar[k] - ab[k]:>+9.4f}")
    print(f"flips up {len(out['flips_up'])}: {out['flips_up']}")
    print(f"flips down {len(out['flips_down'])}: {out['flips_down']}")
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

