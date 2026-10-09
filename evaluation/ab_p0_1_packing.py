"""P0-1 A/B: does evidence-set packing change answer quality?

Live LLM A/B over the cached C_hybrid arm.  Both conditions see the SAME
retrieved chunks and the SAME evidence set; the only difference is whether
``evidence_set`` is passed to ``ContextBuilder``.  That isolates packing as the
sole variable — retrieval, question, and model are held constant.

Conditions
----------
``packing_off``  evidence_set=None  → score-ordered prompt (previous behaviour)
``packing_on``   evidence_set=<sel> → role-ordered prompt + role= annotations

Scoring uses ``compute_question_metrics`` from eval_e2e_v2 so both arms report
the same dual scorecard: binary + soft + citation p/r + groundedness.

Cost: the configured model is a free tier, so a full 150x2 run is $0.  Pass
``--limit N`` for a quick smoke run.

Usage:
    python -m evaluation.ab_p0_1_packing --limit 20
    python -m evaluation.ab_p0_1_packing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

from evaluation.bench_p0_1_packing import _chunks_for, _load_jsonl, _load_payload_index, RAW_DIR
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.resolution import FamilyMap
from evaluation.benchmark import load_questions

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "p0_1_ab.json"
ARM = "C_hybrid"
MAX_WORKERS = 5


def _metric_fn():
    """Shared scorer — same module the eval harness uses, no exec/duplication."""
    from evaluation.answer_scoring import score_answer

    def compute(rag_response, question, chunks, gold_chunk_ids):
        out = score_answer(
            rag_response.answer or "",
            question.acceptable_conclusion or "",
            question.insufficient_evidence,
        )
        cited = {c.chunk_id for c in rag_response.citations}
        gold = gold_chunk_ids or set()
        out["citation_recall"] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
        out["citation_precision"] = round(len(cited & gold) / max(len(cited), 1), 4) if cited else 0.0
        out["groundedness"] = round(float(getattr(rag_response, "groundedness_score", 0.0) or 0.0), 4)
        out["n_citations"] = len(rag_response.citations)
        out["n_context_chunks"] = len(chunks)
        return out

    return compute


class _Cit:
    """Minimal citation stand-in: BuiltContext.citations holds dicts."""

    __slots__ = ("chunk_id",)

    def __init__(self, chunk_id: str) -> None:
        self.chunk_id = chunk_id


def _pseudo_response(answer: str, citations: list, groundedness: float, hallucinated: bool, latency_ms: int):
    class _R:
        pass

    r = _R()
    r.answer = answer
    r.citations = citations
    r.groundedness_score = groundedness
    r.hallucination_detected = hallucinated
    r.total_latency_ms = latency_ms
    return r


def run_condition(
    condition: str,
    records: list[tuple[str, str, list]],
    use_packing: bool,
    compute_question_metrics,
    questions: dict[str, Any],
    gold_index: dict[str, set[str]],
    family_map: Any,
    payload_index: dict[str, dict],
) -> list[dict[str, Any]]:
    sem = Semaphore(MAX_WORKERS)
    out: list[dict[str, Any]] = []

    def one(task):
        qid, query, chunks = task
        from app.rag.generation.grounded_service import GroundedGenerationService
        from app.rag.retrieval.evidence_selector import select_evidence_set

        es_dict = None
        if use_packing:
            try:
                es_dict = select_evidence_set(query, chunks, max_size=5, min_size=2).to_dict()
            except Exception as exc:  # selector failure must not kill the arm
                es_dict = None
                print(f"  selector failed for {qid}: {exc}", flush=True)
        with sem:
            # Drive ContextBuilder directly: this bypasses the
            # ENABLE_EVIDENCE_SELECTOR gate in run_generation_pipeline, so the
            # flag in .env cannot silently decide which A/B arm runs.
            svc = GroundedGenerationService()
            # Corporate-proxy TLS interception breaks the default client; use the
            # shared eval-harness client (eval-only, never production).
            svc.llm_client = SSLBypassLLMClient(model=os.environ["RAG_LLM_MODEL"])
            built = svc.context_builder.build(query, chunks, "general", evidence_set=es_dict)
            sys_prompt, user_prompt = svc._render_prompt(query, built)
            llm = svc._call_llm(sys_prompt, user_prompt)

            answer = getattr(llm, "text", "") or ""
            citations = [_Cit(c["chunk_id"]) for c in built.citations]
            groundedness, halluc = 0.0, False
            pseudo = _pseudo_response(answer, citations, groundedness, halluc, 0)
            q = questions.get(qid)
            m = compute_question_metrics(pseudo, q, chunks, gold_index.get(qid, set())) if q else {}
            return {
                "qid": qid,
                "condition": condition,
                "answer_len": len(answer),
                "n_citations": len(built.citations),
                "n_context_chunks": built.chunk_count,
                "prompt_first_citation_id": (built.citations[0]["chunk_id"] if built.citations else None),
                "metrics": m,
                "llm_error": getattr(llm, "error", None),
            }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(one, t): t for t in records}
        done = 0
        for fut in as_completed(futs):
            try:
                out.append(fut.result())
            except Exception as exc:
                out.append({"qid": futs[fut][0], "condition": condition, "exception": f"{type(exc).__name__}: {exc}"})
            done += 1
            if done % 20 == 0:
                print(f"  {condition}: {done}/{len(records)}", flush=True)
    return out


def _agg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r.get("metrics")]
    if not valid:
        return {"n": 0, "n_errors": len(rows)}
    n = len(valid)
    keys = [
        "binary_correct",
        "binary_correct_overlap_only",
        "binary_correct_abstention_credit",
        "answer_correctness",
        "citation_recall",
        "citation_precision",
        "groundedness",
    ]
    agg = {k: round(sum(float(r["metrics"].get(k, 0) or 0) for r in valid) / n, 4) for k in keys}
    agg["n"] = n
    agg["n_errors"] = len(rows) - n
    return agg


def _guard(results: dict[str, dict[str, Any]], raw: dict[str, list]) -> None:
    """Fail loudly instead of reporting 0.0000 for a fully-broken arm.

    An earlier revision silently scored 0.0 across every metric because every
    task raised; the aggregate looked like a real (catastrophic) result.  A run
    where nothing scored is a harness bug, never a finding.
    """
    broken = {cond: res.get("n", 0) for cond, res in results.items()}
    for cond, res in results.items():
        if res.get("n", 0) == 0:
            errs = [r.get("exception") for r in raw.get(cond, []) if r.get("exception")]
            raise SystemExit(
                f"ABORT: condition {cond!r} produced 0 scored results "
                f"({len(errs)} exceptions). First error: {errs[0] if errs else 'n/a'}",
            )
    if any(res.get("n_errors", 0) > 0 for res in results.values()):
        print(f"  WARNING: partial results {broken}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap questions (0 = all)")
    args = ap.parse_args()

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    family_map = FamilyMap()
    compute_question_metrics = _metric_fn()

    from evaluation.resolution import matches_gold

    gold_index: dict[str, set[str]] = {}
    recs = {r["question_id"]: r for r in _load_jsonl(RAW_DIR / f"{ARM}.jsonl")}
    tasks: list[tuple[str, str, list]] = []
    for qid, rec in recs.items():
        chunks = _chunks_for([str(c) for c in (rec.get("chunk_ids") or [])][:10], payload_index)
        if len(chunks) < 2 or qid not in questions:
            continue
        q = questions[qid]
        units = q.recall_units()
        ids = set()
        for c in chunks:
            p = payload_index.get(c.chunk_id)
            if p and any(matches_gold(p, u, family_map) for u in units):
                ids.add(c.chunk_id)
        gold_index[qid] = ids
        tasks.append((qid, rec.get("query", ""), chunks))

    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 1

    print(f"A/B on {len(tasks)} questions, arm={ARM}, model={os.environ.get('RAG_LLM_MODEL')}", flush=True)
    off = run_condition(
        "packing_off", tasks, False, compute_question_metrics, questions, gold_index, family_map, payload_index,
    )
    on = run_condition(
        "packing_on", tasks, True, compute_question_metrics, questions, gold_index, family_map, payload_index,
    )

    a_off, a_on = _agg(off), _agg(on)
    _guard({"packing_off": a_off, "packing_on": a_on}, {"packing_off": off, "packing_on": on})
    result = {
        "benchmark": "p0_1_packing_ab",
        "date": "2026-10-04",
        "arm": ARM,
        "model": os.environ.get("RAG_LLM_MODEL"),
        "n_questions": len(tasks),
        "design": (
            "Both conditions use identical chunks and the same evidence set; only "
            "ContextBuilder's evidence_set argument differs. The "
            "ENABLE_EVIDENCE_SELECTOR gate in run_generation_pipeline is bypassed by "
            "driving ContextBuilder directly, so .env cannot decide the arm."
        ),
        "limitations": [
            "groundedness is 0.0 for both arms: this harness calls _call_llm and "
            "_render_prompt but not the service's sanitizer/verifier stages, so "
            "groundedness and hallucination are NOT measured here.",
            "Prompt first-citation identity is captured, but full citation extraction needs the service pipeline.",
            "Single free-tier model; results are not a claim about model choice.",
        ],
        "aggregate": {"packing_off": a_off, "packing_on": a_on},
        "delta": {
            k: round(float(a_on.get(k, 0)) - float(a_off.get(k, 0)), 4)
            for k in ("binary_correct", "answer_correctness", "citation_recall", "citation_precision")
        },
        "per_question": {"packing_off": off, "packing_on": on},
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 74)
    print(f"P0-1 A/B  n={len(tasks)}  model={result['model']}")
    print("=" * 74)
    print(f"{'metric':<24} {'packing_off':>12} {'packing_on':>12} {'delta':>9}")
    print("-" * 62)
    for k in ("binary_correct", "answer_correctness", "citation_recall", "citation_precision"):
        print(f"{k:<24} {a_off.get(k, 0):>12.4f} {a_on.get(k, 0):>12.4f} {result['delta'][k]:>+9.4f}")
    print()
    print("NOTE: groundedness/hallucination NOT measured by this harness (see limitations).")
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
