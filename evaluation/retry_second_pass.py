"""Live test of the conditional retry second pass (judicious LLM spend).

Stage-1 offline (`targeted_retry_ab.json`, 0 LLM) measured: baseline top-10
UNION TargetPlan lexical matches capped at 20 recovers gold-in-window on 22/150
with 0 regressed. This runs the live half it could not: does recovered gold
convert to soft/binary/eliminated?

Design (paired, same-pool where it matters):
* `base`  = live top-20 C_hybrid -> generate. This is the live pipeline's
  effective window (ContextBuilder packs up to 20 chunks), NOT Stage-1's
  offline top-10 proxy. A top-10 base was tried first and the veto guard
  caught it excluding gold at ranks 11-20 (Q015 rank 19, Q052 loose-window):
  7 calls discarded, documented in KG_STATUS.md. Fidelity to the live pipeline
  beats fidelity to the offline proxy.
* `retry` = RRF-fuse(base top-20, plan-matched from the cached 500-pool)
  top 20 -> generate, via production `rrf_fuse_chunks` (same fusion the KG
  path uses). Matches compete by merit instead of truncating the base window,
  so a retry that finds nothing changes nothing. No matches = skip, no LLM.
  (Matching runs over the retrieval.jsonl 500-pools, exactly the pool Stage-1
  measured on — a live top-50 re-retrieval starved the matcher on Q025.)
* `veto`  = single-pass re-run of veto QIDs (noise floor + regression guard).

Trigger discipline (the do-no-harm mechanism under test):
* Retry fires only when cached baseline shows missing concepts AND the QID is
  not insufficient_evidence. Passing QIDs and IE QIDs record `skipped` with no
  LLM call. A plan resolving to arm=none/abstain is also a skip, never a query.

Spend discipline (900-call day, ~33 already spent by the KG live test):
* Treatment: 24 QIDs x 2 conds = 48 calls max.
* Veto: 5 QIDs x 1 = 5 calls.
* Cap enforced via --max-calls (default 60); resumable cache makes re-runs free.
* Concurrency 2, delay 3s (429s were seen at 3/1.0 in the last full sweep).

Usage:
    python -m evaluation.retry_second_pass --dry-run
    python -m evaluation.retry_second_pass --max-calls 60 --delay 3.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"
CACHE = OUT / "retry_live.jsonl"

#: 11 RETRIEVAL-recovered + Q049/Q085 (MIXED) + Q053/Q058/Q059 (GENERATION, gold
#: at 11-20) + remaining 8 GENERATION-segment QIDs. 16 have gold reachable at
#: cap-20 by measurement; 8 test whether the loop helps where gold is in-window
#: but the answer dropped it.
TREAT_QIDS = [
    "Q005", "Q007", "Q020", "Q025", "Q040", "Q076", "Q096", "Q110",
    "Q127", "Q141", "Q149",
    "Q049", "Q085",
    "Q053", "Q058", "Q059",
    "Q014", "Q031", "Q032", "Q054", "Q073", "Q074", "Q075", "Q112",
]

#: Veto guard: 2 failure substantive + 1 control substantive + 1 IE control
#: (must keep abstaining) + Q052 (fragile live-only pass on current retrieval).
VETO_QIDS = ["Q015", "Q047", "Q002", "Q060", "Q052"]

BASE_K = 20
RETRY_LIVE_K = 50
RETRY_CAP = 20


def load_cache() -> dict[tuple[str, str], dict]:
    if not CACHE.exists():
        return {}
    out = {}
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["qid"], r["cond"])] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-calls", type=int, default=60)
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--qids", default="")
    ap.add_argument("--requeue-errors", default="",
                    help="comma QIDs whose error records (429 etc.) are dropped and re-run")
    args = ap.parse_args()

    if args.requeue_errors:
        import json as _j

        wanted = {q.strip() for q in args.requeue_errors.split(",") if q.strip()}
        kept, dropped = [], 0
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = _j.loads(line)
                if r.get("qid") in wanted and (r.get("error") or r.get("skipped")):
                    dropped += 1
                    continue
                kept.append(line)
            CACHE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        print(f"requeued {dropped} error/skip records for {sorted(wanted)}")
        if args.dry_run:
            return 0

    treat = [q.strip() for q in args.qids.split(",") if q.strip()] or TREAT_QIDS
    cached = load_cache()
    todo: list[tuple[str, str]] = []
    for q in treat:
        for cond in ("base", "retry"):
            if (q, cond) not in cached:
                todo.append((q, cond))
    for q in VETO_QIDS:
        if (q, "veto") not in cached:
            todo.append((q, "veto"))

    n_llm = len(todo)  # every todo item is exactly one LLM call (skips cached, no call)
    print(f"treat: {len(treat)} x 2 + veto {len(VETO_QIDS)} x 1 = "
          f"{len(treat)*2 + len(VETO_QIDS)} total")
    print(f"cached: {len(cached)} | to run: {n_llm} | cap: {args.max_calls}")
    if args.dry_run:
        print("DRY RUN - no LLM calls. Planned:")
        for q, c in todo[: args.max_calls]:
            print(f"  {q} [{c}]")
        if len(todo) > args.max_calls:
            print(f"  ... +{len(todo)-args.max_calls} deferred (over cap)")
        return 0
    if n_llm > args.max_calls:
        print(f"ABORT: {n_llm} needed > cap {args.max_calls}.")
        return 1
    if not todo:
        print("nothing to run - all cached.")
        _report(cached)
        return 0

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    # Force live LLM (see verify_kg_live_llm: .env sets stub=true).
    os.environ["RAG_USE_STUB_LLM"] = "false"
    # Lazy import BEFORE any `from app import`: skips the module-level
    # production boot (APP_LAZY_IMPORT, app/__init__.py), which races on
    # healthy DBs ~1 in 4-5 boots. This harness is DB-free and drives the
    # factory explicitly against a scratch DB below.
    os.environ["APP_LAZY_IMPORT"] = "true"
    from app import create_app
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="retry_eval_")) / "eval.db"
    app = create_app(db_uri=f"sqlite:///{scratch}")
    with app.app_context():
        from evaluation.ab_targeted_retry import _derive_failures, _plan_match_ids
        from evaluation.arms import arm_c_hybrid
        from evaluation.benchmark import load_questions
        from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index
        from evaluation.reeval_89 import _grade
        from app.rag.generation import GroundedGenerationService
        from app.rag.generation.context_builder import ContextBuilder
        from app.rag.planning.targeted_retry import TargetedRetryPlanner
        from app.rag.retrieval.result import RetrievedChunk
        from evaluation.llm_ssl_client import SSLBypassLLMClient

        questions = {q.question_id: q for q in load_questions()}
        payload_index = _load_payload_index()
        gen_cache = {}
        for line in (OUT / "gen_C_hybrid.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                gen_cache[r["qid"]] = r

        service = GroundedGenerationService(
            llm_client=SSLBypassLLMClient(),
            context_builder=ContextBuilder(),
        )
        planner = TargetedRetryPlanner()

        with open(CACHE, "a", encoding="utf-8") as out:
            for i, (qid, cond) in enumerate(todo, 1):
                q = questions.get(qid)
                if cond == "veto":
                    rec = _run_single(q, None, service, payload_index, cond, {})
                    _write(out, rec, cached)
                    print(f"[{i}/{len(todo)}] {qid} [veto] elim={rec.get('eliminated')} "
                          f"bin={rec.get('binary_correct')} soft={rec.get('answer_correctness')}",
                          flush=True)
                elif cond == "base":
                    ids = _live_ids(q, BASE_K)
                    rec = _run_single(q, ids, service, payload_index, cond,
                                      {"pool": "live-top10"})
                    _write(out, rec, cached)
                    print(f"[{i}/{len(todo)}] {qid} [base] elim={rec.get('eliminated')} "
                          f"bin={rec.get('binary_correct')} soft={rec.get('answer_correctness')}",
                          flush=True)
                else:  # retry
                    rec = _run_retry(q, gen_cache.get(qid), service, payload_index,
                                     planner, cached)
                    if rec.get("skipped"):
                        _write(out, rec, cached)
                        print(f"[{i}/{len(todo)}] {qid} [retry] SKIPPED "
                              f"({rec['skipped']}) - no LLM call", flush=True)
                        continue
                    _write(out, rec, cached)
                    print(f"[{i}/{len(todo)}] {qid} [retry] elim={rec.get('eliminated')} "
                          f"bin={rec.get('binary_correct')} soft={rec.get('answer_correctness')} "
                          f"plan={rec.get('plan_arm')}/{rec.get('plan_strategy')} "
                          f"union={rec.get('union_size')}", flush=True)
                if args.delay:
                    time.sleep(args.delay)

    _report(load_cache())
    return 0


def _live_ids(q, top_k: int) -> list[str]:
    from evaluation.arms import arm_c_hybrid

    res = arm_c_hybrid(q.raw, top_k)
    return [str(c) for c in (res.get("chunk_ids") or [])]


def _run_single(q, ids: list[str] | None, service, payload_index, cond: str,
                extra: dict) -> dict:
    import time as _t

    from evaluation.bench_p0_1_packing import _chunks_for

    if ids is None:  # veto: same single-pass construction as base
        ids = _live_ids(q, BASE_K)
    chunks = _chunks_for(ids, payload_index)
    qt = q.question_types[0] if q.question_types else ""
    t0 = _t.monotonic()
    try:
        resp = service.generate(q.question, chunks, query_type=qt)
        answer, err = (resp.answer or ""), (resp.debug or {}).get("error")
    except Exception as exc:
        answer, err = "", f"{type(exc).__name__}: {exc}"
    from evaluation.reeval_89 import _grade

    rec = {"qid": q.question_id, "cond": cond, "n_chunks": len(chunks),
           "answer_length": len(answer), "latency_s": round(_t.monotonic() - t0, 2),
           "error": err, "answer": answer, **_grade(q, answer), **extra}
    return rec


def _run_retry(q, cached_gen: dict | None, service, payload_index, planner,
               cached: dict) -> dict:
    """One conditional retry second pass. No LLM call on any skip path."""
    from evaluation.bench_p0_1_packing import _chunks_for

    if getattr(q, "insufficient_evidence", False):
        return {"qid": q.question_id, "cond": "retry", "skipped": "insufficient_evidence"}
    cached = cached_gen or {}
    missing = (cached.get(concepts_missing\) or []) + (cached.get(uthority_missing\) or cached.get(uthorities_missing\) or [])
    if not missing:
        return {"qid": q.question_id, "cond": "retry", "skipped": "no_missing_concepts_or_authority"}
    # Same heuristic failure derivation Stage-1 used (imported seam, §4.1).
    from evaluation.ab_targeted_retry import _derive_failures, _plan_match_ids

    failures = _derive_failures(q.question, True)
    # Production-faithful ctx (linear.py passes chunks+answer; the first harness
    # version passed only top_k, which starved _mine_defined_term and no-op'd
    # Q007/Q058 as definition_search — a harness artifact, not a planner verdict).
    base_rec = cached.get((q.question_id, "base")) or {}
    base_ids = _live_ids(q, BASE_K)
    ctx_chunks = [
        {"chunk_id": c.chunk_id, "text": c.text,
         "document_title": getattr(c, "document_title", "")}
        for c in _chunks_for(base_ids, payload_index)
    ]
    plan = planner.target_plan(query=q.question, failures=failures,
                               query_type=q.question_types[0] if q.question_types else "",
                               context={"top_k": BASE_K, "chunks": ctx_chunks,
                                        "answer": base_rec.get("answer") or ""})
    if plan.arm in ("none",) or plan.strategy in ("abstain", "unmapped"):
        return {"qid": q.question_id, "cond": "retry",
                "skipped": f"plan_noop:{plan.arm}/{plan.strategy}",
                "plan": plan.to_dict()}
    # Live retrieval with the targeted query, matched lexically per Stage-1.
    # Correction 2026-10-12: the first version re-retrieved live top-50 with
    # the targeted query and matched over that. Q025's plan then matched
    # nothing -- a narrower, re-ranked pool starves the lexical matcher that
    # Stage-1 ran over the frozen 500-pool. Match over the cached 500-pool
    # from retrieval.jsonl instead (post-remediation live pools, 0 extra
    # retrieval calls): same matching Stage-1 measured, same base window.
    from evaluation.bench_p0_1_packing import _chunks_for as _cf
    from evaluation.reeval_89 import _read_jsonl as _read_retr

    pool_ids = list(
        ((_read_retr(OUT / "retrieval.jsonl").get(q.question_id) or {})
         .get("C_hybrid") or {}).get("chunk_ids") or []
    )
    pool_chunks = _cf(pool_ids, payload_index)
    matched = _plan_match_ids(plan, pool_chunks, payload_index)
    if not matched:
        return {"qid": q.question_id, "cond": "retry",
                "skipped": "no_plan_matches",
                "plan": plan.to_dict()}
    base_ids = _live_ids(q, BASE_K)
    base_chunks = _chunks_for(base_ids, payload_index)
    matched_chunks = [c for c in pool_chunks if c.chunk_id in set(matched)]
    # Production fusion, not truncation: matches compete with the base window
    # by merit (same rrf_fuse_chunks the KG path uses). A retry that finds
    # weak matches changes little; one that finds gold promotes it.
    from kg.hybrid import rrf_fuse_chunks

    fused = rrf_fuse_chunks([base_chunks, matched_chunks], rrf_k=60.0, top_k=RETRY_CAP)
    rec = _run_single(q, [c.chunk_id for c in fused], service, payload_index, "retry",
                      {"pool": f"rrf(base{len(base_ids)},matched{len(matched)})",
                       "plan_arm": plan.arm, "plan_strategy": plan.strategy,
                       "plan_query": plan.query[:120], "union_size": len(fused)})
    rec["plan"] = plan.to_dict()
    return rec


def _write(out, rec: dict, cached: dict) -> None:
    import json as _j

    out.write(_j.dumps(rec, ensure_ascii=False) + "\n")
    out.flush()
    cached[(rec["qid"], rec["cond"])] = rec


def _report(cached: dict[tuple[str, str], dict]) -> None:
    import statistics as _s

    qids = sorted({q for q, c in cached if c in ("base", "retry")})
    pairs = [(q, cached.get((q, "base")), cached.get((q, "retry"))) for q in qids]
    pairs = [(q, b, k) for q, b, k in pairs
             if b and k and not b.get("error") and not k.get("skipped") and not k.get("error")]
    skipped = [(q, (cached.get((q, "retry")) or {}).get("skipped")) for q in qids
               if (cached.get((q, "retry")) or {}).get("skipped")]
    print("\n" + "=" * 70)
    print(f"RETRY SECOND PASS, PAIRED LIVE ({len(pairs)} complete pairs, "
          f"{len(skipped)} skipped-no-LLM)")
    if skipped:
        print(f"  skipped: {[(q, s) for q, s in skipped]}")
    if not pairs:
        print("  no complete pairs yet.")
    else:
        d = [float(k.get("answer_correctness") or 0) - float(b.get("answer_correctness") or 0)
             for _, b, k in pairs]
        b_el = sum(1 for _, b, _ in pairs if b.get("eliminated"))
        k_el = sum(1 for _, _, k in pairs if k.get("eliminated"))
        b_bin = sum(1 for _, b, _ in pairs if b.get("binary_correct"))
        k_bin = sum(1 for _, _, k in pairs if k.get("binary_correct"))
        print(f"  soft  base {_s.mean([float(b.get('answer_correctness') or 0) for _, b, _ in pairs]):.3f} "
              f"-> retry {_s.mean([float(k.get('answer_correctness') or 0) for _, _, k in pairs]):.3f} "
              f"(mean delta {(_s.mean(d) if d else 0):+.4f})")
        print(f"  binary base {b_bin}/{len(pairs)} -> retry {k_bin}/{len(pairs)}")
        print(f"  elim   base {b_el}/{len(pairs)} -> retry {k_el}/{len(pairs)}")
        for q, b, k in pairs:
            ds = float(k.get("answer_correctness") or 0) - float(b.get("answer_correctness") or 0)
            flip = (not b.get("eliminated") and k.get("eliminated"))
            if abs(ds) > 0.01 or flip or (b.get("eliminated") and not k.get("eliminated")):
                print(f"    {q}: {float(b.get('answer_correctness') or 0):.3f} -> "
                      f"{float(k.get('answer_correctness') or 0):.3f} ({ds:+.3f}) "
                      f"elim {bool(b.get('eliminated'))}->{bool(k.get('eliminated'))}"
                      f"{' <-- FLIP' if flip else ''}")
    veto = [(q, cached.get((q, "veto"))) for q in VETO_QIDS if cached.get((q, "veto"))]
    if veto:
        print(f"  veto guard ({len(veto)}): "
              + ", ".join(f"{q}:elim={bool(v.get('eliminated'))}/bin={bool(v.get('binary_correct'))}"
                          + (f" ERR={str(v.get('error'))[:40]}" if v.get("error") else "")
                          for q, v in veto))
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
