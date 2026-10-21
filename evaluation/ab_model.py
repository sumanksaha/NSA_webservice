"""Paired LLM swap test: same contexts, different model (judicious spend).

Compares candidate models against cached laguna top-20 baselines on the same
retrieval pools: for each QID, the ONLY thing that changes is the model. New
spend is 1 call per (QID, model); laguna side is reused from cache.

QID set is built to discriminate, not to impress:
* must-hold passes (a swap that loses these is vetoed no matter its mean),
* near-misses (soft 0.40-0.50: where a better model actually flips),
* one IE control (a chattier model can LOSE abstention credits).

The gate interaction to watch is answer LENGTH: soft=(jaccard+coverage)/2 and
jaccard punishes verbosity ~6x answers already. A stronger-but-chattier model
can score worse. Length is reported per model for exactly this reason.

Usage:
    python -m evaluation.ab_model --dry-run
    python -m evaluation.ab_model --models apodex/apodex-1.1-mini:free,nvidia/nemotron-3.5-lightning:free --max-calls 30
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
CACHE = OUT / "model_ab.jsonl"

#: 4 must-hold + 6 near-miss + 2 continuity + 1 IE abstention guard.
QIDS = [
    "Q052", "Q015", "Q047", "Q002",
    "Q007", "Q025", "Q084", "Q023", "Q020", "Q123",
    "Q016", "Q014",
    "Q060",
]
MODELS = [
    "apodex/apodex-1.1-mini:free",
    "nvidia/nemotron-3.5-lightning:free",
]
LAGUNA = "poolside/laguna-s-2.1:free"


def load_cache() -> dict[tuple[str, str], dict]:
    if not CACHE.exists():
        return {}
    out = {}
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["qid"], r["cond"])] = r
    return out


def short(model: str) -> str:
    return model.split("/")[-1].replace(":free", "")


def laguna_base(qid: str) -> dict | None:
    """Cached laguna top-20 baseline for qid, newest first across harnesses.

    Accepts cond "base" (paired harnesses) and "veto" (identical top-20
    single-pass construction via _run_single) plus laguna runs of this
    harness itself (cond "laguna-s-2.1", used to fill QIDs with no prior
    top-20 record so paired deltas stay complete).
    """
    for path, conds in ((OUT / "retry_live.jsonl", ("base", "veto")),
                        (OUT / "kg_live_verify.jsonl", ("base",))):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("qid") == qid and r.get("cond") in conds and not r.get("error"):
                return r
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("qid") == qid and r.get("cond") == short(LAGUNA) and not r.get("error"):
                return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-calls", type=int, default=30)
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--qids", default="")
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--requeue-errors", default="",
                    help="comma QIDs whose error records are dropped and re-run")
    args = ap.parse_args()

    if args.requeue_errors:
        wanted = {q.strip() for q in args.requeue_errors.split(",") if q.strip()}
        kept, dropped = [], 0
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                # Empty answers with no error are client-compat failures (200
                # with null content, e.g. haiku 7/13), not model verdicts —
                # re-running them is correction, not cherry-picking. NOTE:
                # compare with `is None`, not truthiness: answer_length 0 is
                # falsy, and `(0 or -1) == 0` never matches (shipped once).
                _len = r.get("answer_length")
                if r.get("qid") in wanted and (r.get("error") or _len == 0):
                    dropped += 1
                    continue
                kept.append(line)
            CACHE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        print(f"requeued {dropped} error/empty records for {sorted(wanted)}")

    qids = [q.strip() for q in args.qids.split(",") if q.strip()] or QIDS
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    cached = load_cache()
    todo = [(q, m) for q in qids for m in models if (q, short(m)) not in cached]
    missing_base = [q for q in qids if laguna_base(q) is None]
    print(f"qids={len(qids)} models={len(models)} to_run={len(todo)} cap={args.max_calls}")
    print(f"laguna baselines cached for {len(qids)-len(missing_base)}/{len(qids)}"
          + (f"; MISSING base for {missing_base} (paired deltas skip those)" if missing_base else ""))
    if args.dry_run:
        for q, m in todo[: args.max_calls]:
            print(f"  {q} [{short(m)}]")
        return 0
    if len(todo) > args.max_calls:
        print(f"ABORT: {len(todo)} > cap {args.max_calls}.")
        return 1
    if not todo:
        _report(cached)
        return 0

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["RAG_USE_STUB_LLM"] = "false"

    # Lazy import FIRST: skip the module-level production boot
    # (APP_LAZY_IMPORT, app/__init__.py) — this harness is DB-free and drives
    # the factory explicitly against a scratch DB below.
    os.environ["APP_LAZY_IMPORT"] = "true"
    from app import create_app
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="model_ab_")) / "eval.db"
    app = create_app(db_uri=f"sqlite:///{scratch}")
    with app.app_context():
        from evaluation.benchmark import load_questions
        from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index
        from evaluation.reeval_89 import _grade
        from app.rag.generation import GroundedGenerationService
        from app.rag.generation.context_builder import ContextBuilder
        from evaluation.llm_ssl_client import SSLBypassLLMClient

        questions = {q.question_id: q for q in load_questions()}
        payload_index = _load_payload_index()
        retr = {json.loads(l)["qid"]: json.loads(l)
                for l in (OUT / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()}

        with open(CACHE, "a", encoding="utf-8") as out:
            for i, (qid, model) in enumerate(todo, 1):
                q = questions.get(qid)
                ids = list((retr.get(qid, {}).get("C_hybrid") or {}).get("chunk_ids") or [])[:20]
                chunks = _chunks_for(ids, payload_index)
                service = GroundedGenerationService(
                    llm_client=SSLBypassLLMClient(model=model),
                    context_builder=ContextBuilder(),
                )
                qt = q.question_types[0] if q.question_types else ""
                t0 = time.monotonic()
                try:
                    resp = service.generate(q.question, chunks, query_type=qt)
                    answer, err = (resp.answer or ""), (resp.debug or {}).get("error")
                except Exception as exc:
                    answer, err = "", f"{type(exc).__name__}: {exc}"
                grade = _grade(q, answer)
                rec = {"qid": qid, "cond": short(model), "model": model,
                       "n_chunks": len(chunks), "answer_length": len(answer),
                       "latency_s": round(time.monotonic() - t0, 2),
                       "error": err, "answer": answer, **grade}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                cached[(qid, short(model))] = rec
                print(f"[{i}/{len(todo)}] {qid} [{short(model)}] elim={grade.get('eliminated')} "
                      f"bin={grade.get('binary_correct')} soft={grade.get('answer_correctness')} "
                      f"len={len(answer)} err={err or ''}", flush=True)
                if args.delay:
                    time.sleep(args.delay)

    _report(load_cache())
    return 0


def _report(cached: dict[tuple[str, str], dict]) -> None:
    import statistics as _s

    models = sorted({c for _, c in cached})
    print("\n" + "=" * 70)
    print("MODEL SWAP, PAIRED vs CACHED LAGUNA BASE (same pools)")
    for m in models:
        rows = [(q, laguna_base(q), cached[(q, m)]) for q, c in cached if c == m]
        rows = [(q, b, k) for q, b, k in rows
                if b and not b.get("error") and not k.get("error")]
        if not rows:
            print(f"  [{m}] no complete pairs yet")
            continue
        d = [float(k.get("answer_correctness") or 0) - float(b.get("answer_correctness") or 0)
             for _, b, k in rows]
        bl = [len(b.get("answer") or "") for _, b, _ in rows]
        kl = [len(k.get("answer") or "") for _, _, k in rows]
        print(f"  [{m}] n={len(rows)} soft {float(_s.mean([float(b.get('answer_correctness') or 0) for _, b, _ in rows])):.3f} "
              f"-> {float(_s.mean([float(k.get('answer_correctness') or 0) for _, _, k in rows])):.3f} "
              f"(delta {float(_s.mean(d)):+.4f})")
        print(f"    binary {sum(1 for _, b, _ in rows if b.get('binary_correct'))} "
              f"-> {sum(1 for _, _, k in rows if k.get('binary_correct'))} | elim "
              f"{sum(1 for _, b, _ in rows if b.get('eliminated'))} -> "
              f"{sum(1 for _, _, k in rows if k.get('eliminated'))} | "
              f"answer len {int(_s.mean(bl))} -> {int(_s.mean(kl))} chars")
        for q, b, k in rows:
            ds = float(k.get("answer_correctness") or 0) - float(b.get("answer_correctness") or 0)
            flip = ((not b.get("eliminated") and k.get("eliminated"))
                    or (b.get("eliminated") and not k.get("eliminated")))
            if abs(ds) > 0.03 or flip or (b.get("binary_correct") != k.get("binary_correct")):
                print(f"    {q}: {float(b.get('answer_correctness') or 0):.3f} -> "
                      f"{float(k.get('answer_correctness') or 0):.3f} ({ds:+.3f}) "
                      f"elim {bool(b.get('eliminated'))}->{bool(k.get('eliminated'))} "
                      f"bin {bool(b.get('binary_correct'))}->{bool(k.get('binary_correct'))}")
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
