"""Phase 1: length-targeted laguna prompt (paired, judicious spend).

The three model swaps triangulated the finding: laguna covers but rambles
(~3000 chars, jaccard ~0.10), haiku is terse but starves coverage, and Q002
proves the middle wins (coverage 1.0 both, haiku shorter, haiku takes it).
This tests the middle directly: the SAME model and SAME top-20 pools, with
only a length+completeness instruction added to the system prompt.

Seam: `generate_with_prompts` (caller-supplied prompts, no production change).
The user template (context + question) is byte-identical; only the system
prompt gains the length contract. Citation contract, abstention behavior, and
context construction are untouched — so any delta is the instruction, and the
veto guard stays meaningful.

Treatment set: 1–2 concepts missing, gold in window, soft 0.30–0.50 (near-miss
band where length plausibly blocks the gate). QIDs lacking a cached laguna
top-20 base get one (paired deltas stay complete).

Go-criteria: ≥3 eliminations with 0 veto flips. Manipulation check: mean
concise length must land well below baseline (~3000) — if the model ignores
the instruction, a null result means nothing.

Usage:
    python -m evaluation.prompt_length_ab --dry-run
    python -m evaluation.prompt_length_ab --max-calls 35 --delay 5.0
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
CACHE = OUT / "prompt_length_ab.jsonl"

TREAT_QIDS = [
    "Q129", "Q057", "Q075", "Q126", "Q086", "Q036", "Q131", "Q049",
    "Q078", "Q074", "Q085", "Q059", "Q109", "Q022", "Q054", "Q053",
    "Q105", "Q073",
]
VETO_QIDS = ["Q015", "Q047", "Q002", "Q060", "Q052"]

LENGTH_CONTRACT = (
    " Length contract: keep the whole response under ~1500 characters. "
    "State every distinct legal conclusion the quotes support — one cited "
    "sentence per conclusion — and omit background, restatement of the "
    "question, and any conclusion already stated."
)

#: Refined contract (v2, from analyze_length.py autopsy 2026-10-14): the flat
#: cap cut the answer (Step 2, where concepts live) MORE than the quotes
#: (Step 1, 58% of chars) and collapsed the two-step structure entirely on the
#: over-shortened answers (Q052/Q078/Q105/Q109/Q126: no Step 2 at all). So
#: budget the sections separately — cap the quotes, protect the answer — and
#: apply conditionally (skip when the base already passes or is short enough
#: that only risk remains). Q053's flip came through coverage (+0.152), not
#: length; the instruction must demand completeness first, brevity second.
LENGTH_CONTRACT_V2 = (
    " Structure and length contract. Step 1 — quotes: at most 800 characters "
    "total; quote only the shortest passages that directly bear on the "
    "question, verbatim. Step 2 — answer: one cited sentence per distinct "
    "legal conclusion the quotes support; state every conclusion completely "
    "and precisely, then stop — no background, no restatement of the question, "
    "no conclusion stated twice. Keep both steps: never skip Step 2."
)

#: Synthesis demand (v3, from gold_labelling_audit.py 2026-10-14): on 6/85
#: incorrect failures the reference is covered >=0.5 by NON-GOLD provisions
#: already in the top-20 window (Q005 Q026 Q031 Q052 Q082 Q096) — the content
#: is in front of the model under another name, and single-provision answers
#: systematically under-cite it. This clause demands cross-source synthesis;
#: it is appended to the v2 contract, not a replacement, so length behaviour
#: stays comparable and the pairing stays clean.
SYNTHESIS_CLAUSE = (
    " Synthesis requirement: when the quoted passages come from more than one "
    "distinct source provision, ground each legal conclusion in its own "
    "source — cite at least two distinct [n] sources across the answer, one "
    "cited sentence per source relied on. Do not answer from a single source "
    "while other quoted sources bear on the question."
)


def load_cache() -> dict[tuple[str, str], dict]:
    if not CACHE.exists():
        return {}
    out = {}
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[(r["qid"], r["cond"])] = r
    return out


def laguna_base(qid: str) -> dict | None:
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
            if r.get("qid") == qid and r.get("cond") == "laguna-base" and not r.get("error"):
                return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-calls", type=int, default=35)
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--qids", default="")
    ap.add_argument("--models", default="poolside/laguna-s-2.1:free")
    ap.add_argument("--requeue-errors", default="")
    ap.add_argument("--variant", default="v1", choices=("v1", "v2", "v3"),
                    help="v1=flat ~1500-char cap (cond concise); v2=section "
                    "budgets + conditional application (cond concise2); "
                    "v3=v2 + multi-provision synthesis demand (cond synthesis)")
    args = ap.parse_args()

    treat = [q.strip() for q in args.qids.split(",") if q.strip()] or TREAT_QIDS
    cached = load_cache()

    cond = {"v1": "concise", "v2": "concise2", "v3": "synthesis"}[args.variant]
    contract = {"v1": LENGTH_CONTRACT, "v2": LENGTH_CONTRACT_V2,
                "v3": LENGTH_CONTRACT_V2 + SYNTHESIS_CLAUSE}[args.variant]

    if args.requeue_errors:
        wanted = {q.strip() for q in args.requeue_errors.split(",") if q.strip()}
        kept, dropped = [], 0
        for line in CACHE.read_text(encoding="utf-8").splitlines() if CACHE.exists() else []:
            if not line.strip():
                continue
            r = json.loads(line)
            _len = r.get("answer_length")
            if r.get("qid") in wanted and (r.get("error") or _len == 0):
                dropped += 1
                continue
            kept.append(line)
        CACHE.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        print(f"requeued {dropped} error/empty records for {sorted(wanted)}")
        cached = load_cache()

    # Pairing: concise needs a laguna base; missing bases are added as laguna-base.
    todo: list[tuple[str, str]] = []
    for q in treat:
        if laguna_base(q) is None and (q, "laguna-base") not in cached:
            todo.append((q, "laguna-base"))
        if (q, cond) not in cached:
            if args.variant in ("v2", "v3"):
                # Conditional application (do-no-harm by construction, mirroring
                # the retry trigger): skip QIDs whose base already passes or is
                # short enough that only risk remains. Skips are recorded with
                # no LLM call so the report distinguishes spared from missing.
                b = laguna_base(q)
                if b is not None and (b.get("eliminated") or (b.get("answer_length") or 0) < 2000):
                    cached[(q, cond)] = {
                        "qid": q, "cond": cond, "skipped":
                        f"conditional: base elim={bool(b.get('eliminated'))} "
                        f"len={b.get('answer_length')}",
                    }
                    continue
            todo.append((q, cond))
    for q in VETO_QIDS:
        if (q, cond) not in cached:
            if args.variant in ("v2", "v3"):
                b = laguna_base(q)
                if b is not None and (b.get("eliminated") or (b.get("answer_length") or 0) < 2000):
                    cached[(q, cond)] = {
                        "qid": q, "cond": cond, "skipped":
                        f"conditional: base elim={bool(b.get('eliminated'))} "
                        f"len={b.get('answer_length')}",
                    }
                    continue
            todo.append((q, cond))

    print(f"treat={len(treat)} veto={len(VETO_QIDS)} to_run={len(todo)} cap={args.max_calls}")
    # Persist conditional skips so re-runs and the report see them as spared,
    # not missing (same convention as retry_second_pass.py skip records).
    # Only skips absent from the file are appended — load_cache dict already
    # dedupes, so re-running never duplicates.
    _file_keys = set(load_cache())
    _new = [(q, c) for q, c in cached
            if c == cond and (cached[(q, c)] or {}).get("skipped")
            and (q, c) not in _file_keys]
    # NOTE: load_cache() just re-read the file, so _file_keys ⊇ file content;
    # skips created this run are exactly those not in it. (Since `cached`
    # started as load_cache(), membership test is precise.)
    if _new:
        with open(CACHE, "a", encoding="utf-8") as _f:
            for _q, _c in _new:
                _f.write(json.dumps(cached[(_q, _c)], ensure_ascii=False) + "\n")
        print(f"  recorded {len(_new)} conditional skips: {[q for q, _ in _new]}")
    if args.dry_run:
        for q, c in todo[: args.max_calls]:
            print(f"  {q} [{c}]")
        if len(todo) > args.max_calls:
            print(f"  ... +{len(todo)-args.max_calls} deferred")
        return 0
    if len(todo) > args.max_calls:
        print(f"ABORT: {len(todo)} > cap {args.max_calls}.")
        return 1
    if not todo:
        _report(cached, cond)
        return 0

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ["RAG_USE_STUB_LLM"] = "false"
    os.environ["APP_LAZY_IMPORT"] = "true"
    from app import create_app
    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="plen_ab_")) / "eval.db"
    app = create_app(db_uri=f"sqlite:///{scratch}")
    with app.app_context():
        from evaluation.benchmark import load_questions
        from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index
        from evaluation.reeval_89 import _grade
        from app.rag.generation import GroundedGenerationService
        from app.rag.generation.context_builder import ContextBuilder
        from app.rag.generation.prompt_template import PromptTemplate
        from evaluation.llm_ssl_client import SSLBypassLLMClient

        questions = {q.question_id: q for q in load_questions()}
        payload_index = _load_payload_index()
        retr = {json.loads(l)["qid"]: json.loads(l)
                for l in (OUT / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()}
        template = PromptTemplate()

        with open(CACHE, "a", encoding="utf-8") as out:
            for i, (qid, cond) in enumerate(todo, 1):
                q = questions.get(qid)
                ids = list((retr.get(qid, {}).get("C_hybrid") or {}).get("chunk_ids") or [])[:20]
                chunks = _chunks_for(ids, payload_index)
                qt = q.question_types[0] if q.question_types else ""
                service = GroundedGenerationService(
                    llm_client=SSLBypassLLMClient(model=args.models),
                    context_builder=ContextBuilder(),
                )
                t0 = time.monotonic()
                try:
                    if cond in ("concise", "concise2", "synthesis"):
                        built = service.context_builder.build(q.question, chunks, qt)
                        sys_p, user_p = template.render_default(
                            q.question, built.context, query_type=qt)
                        use_contract = (LENGTH_CONTRACT_V2 + SYNTHESIS_CLAUSE
                                        if cond == "synthesis"
                                        else (LENGTH_CONTRACT_V2 if cond == "concise2"
                                              else LENGTH_CONTRACT))
                        resp = service.generate_with_prompts(
                            q.question, chunks, query_type=qt,
                            prompts=(sys_p + use_contract, user_p))
                    else:
                        resp = service.generate(q.question, chunks, query_type=qt)
                    answer, err = (resp.answer or ""), (resp.debug or {}).get("error")
                except Exception as exc:
                    answer, err = "", f"{type(exc).__name__}: {exc}"
                from evaluation.reeval_89 import _grade as _g
                grade = _g(q, answer)
                rec = {"qid": qid, "cond": cond, "n_chunks": len(chunks),
                       "answer_length": len(answer),
                       "latency_s": round(time.monotonic() - t0, 2),
                       "error": err, "answer": answer, **grade}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                cached[(qid, cond)] = rec
                print(f"[{i}/{len(todo)}] {qid} [{cond}] elim={grade.get('eliminated')} "
                      f"bin={grade.get('binary_correct')} soft={grade.get('answer_correctness')} "
                      f"len={len(answer)} err={err or ''}", flush=True)
                if args.delay:
                    time.sleep(args.delay)

    _report(load_cache(), cond)
    return 0


def _report(cached: dict[tuple[str, str], dict], cond: str = "concise") -> None:
    import statistics as _s

    skipped = sorted(q for q, c in cached
                     if c == cond and (cached[(q, c)] or {}).get("skipped"))
    qids = sorted({q for q, c in cached if c == cond
                   and not (cached[(q, c)] or {}).get("skipped")})
    veto_hits = [q for q in VETO_QIDS if (q, cond) in cached
                 and not (cached[(q, cond)] or {}).get("skipped")]
    treat = [q for q in qids if q not in VETO_QIDS]
    pairs = []
    for q in treat:
        b = laguna_base(q)
        k = cached.get((q, cond))
        if b and k and not b.get("error") and not k.get("error"):
            pairs.append((q, b, k))
    print("\n" + "=" * 70)
    print(f"LENGTH-TARGETED PROMPT [{cond}], PAIRED ({len(pairs)} pairs, "
          f"{len(skipped)} conditional skips)")
    if skipped:
        print(f"  spared by conditional rule: {skipped}")
    if not pairs:
        print("  no complete pairs yet.")
    else:
        d = [float(k.get("answer_correctness") or 0) - float(b.get("answer_correctness") or 0)
             for _, b, k in pairs]
        bl = [b.get("answer_length", 0) for _, b, _ in pairs]
        kl = [k.get("answer_length", 0) for _, _, k in pairs]
        print(f"  soft  base {_s.mean([float(b.get('answer_correctness') or 0) for _, b, _ in pairs]):.3f} "
              f"-> concise {_s.mean([float(k.get('answer_correctness') or 0) for _, _, k in pairs]):.3f} "
              f"(delta {float(_s.mean(d)):+.4f})")
        print(f"  binary {sum(1 for _, b, _ in pairs if b.get('binary_correct'))} "
              f"-> {sum(1 for _, _, k in pairs if k.get('binary_correct'))} | elim "
              f"{sum(1 for _, b, _ in pairs if b.get('eliminated'))} -> "
              f"{sum(1 for _, _, k in pairs if k.get('eliminated'))}")
        print(f"  answer len base {int(_s.mean(bl))} -> concise {int(_s.mean(kl))} "
              f"(manipulation check: concise must be well below ~3000)")
        for q, b, k in pairs:
            ds = float(k.get("answer_correctness") or 0) - float(b.get("answer_correctness") or 0)
            flip = (not b.get("eliminated") and k.get("eliminated"))
            unflip = (b.get("eliminated") and not k.get("eliminated"))
            if abs(ds) > 0.03 or flip or unflip or (b.get("binary_correct") != k.get("binary_correct")):
                print(f"    {q}: {float(b.get('answer_correctness') or 0):.3f} -> "
                      f"{float(k.get('answer_correctness') or 0):.3f} ({ds:+.3f}) "
                      f"len {b.get('answer_length')} -> {k.get('answer_length')} "
                      f"elim {bool(b.get('eliminated'))}->{bool(k.get('eliminated'))}"
                      f"{' <-- FLIP' if flip else ''}{' <-- UNFLIP' if unflip else ''}")
    if veto_hits:
        print(f"  veto guard ({len(veto_hits)}): "
              + ", ".join(f"{q}:elim={bool(cached[(q,cond)].get('eliminated'))}"
                          f"/bin={bool(cached[(q,cond)].get('binary_correct'))}"
                          for q in veto_hits))
    print("=" * 70)


if __name__ == "__main__":
    raise SystemExit(main())
