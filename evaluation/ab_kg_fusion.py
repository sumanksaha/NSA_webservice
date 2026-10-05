"""A/B the knowledge-graph fusion path: does it improve answers?

The KG was inert.  ``provisions_for_query`` returned provisions for 5 of 30
benchmark questions because (a) concept keys did not match the graph's
``LegalConcept.name`` values, (b) the traversal matched 3 of the 15
provision->concept edges that actually exist, and (c) the full-text fallback
matched a whole question as a literal substring, which no provision can ever
satisfy.  All three failed inside ``try/except`` blocks that log a warning, so
the pipeline served vector-only results while appearing to have a KG.

kg/queries.py now fixes those.  This asks the question that matters: does the
KG contribute anything to answer quality once it actually returns evidence?

Conditions (same cached chunks, same model, same prompt budget -- the only
difference is whether KG provisions are RRF-fused into the candidate pool):

  ``kg_off``  vector retrieval only
  ``kg_on``   vector retrieval + KG provisions fused via rrf_fuse_chunks

Reports the dual scorecard plus groundedness, and singles out the questions
where fusion actually injected new gold evidence, since that is where a
difference must appear if the mechanism is real.

Usage:
    python -m evaluation.ab_kg_fusion --limit 0
    python -m evaluation.ab_kg_fusion --start 0 --limit 10 --shard-out s0.json
    python -m evaluation.ab_kg_fusion --merge-shards s*.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
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

from evaluation.answer_scoring import score_answer
from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.resolution import FamilyMap, matches_gold

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "kg_fusion_ab.json"
ARM = "C_hybrid"
MAX_WORKERS = 5


def _kg_chunks(query: str, limit: int) -> tuple[list, dict[str, Any]]:
    """KG provisions as RetrievedChunks, mirroring ``_generate_apply_kg_context``."""
    from kg.hybrid import provisions_to_retrieved_chunks
    from kg.queries import LegalKGQueries, provisions_for_query

    provisions = provisions_for_query(query, LegalKGQueries(), limit=limit)
    return provisions_to_retrieved_chunks(provisions, limit=limit), {
        "provisions": len(provisions),
        "injected": 0,
    }


def _fuse(vector_chunks: list, kg_chunks: list, top_k: int) -> list:
    if not kg_chunks:
        return vector_chunks
    from kg.hybrid import rrf_fuse_chunks

    return rrf_fuse_chunks([vector_chunks, kg_chunks], rrf_k=60.0, top_k=top_k)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", type=int, default=0, help="shard offset")
    ap.add_argument("--shard-out", type=str, default="", help="write paired rows here instead of the summary")
    ap.add_argument(
        "--merge-shards",
        nargs="+",
        default=[],
        help="recompute the summary from previously written shard files, no LLM calls",
    )
    ap.add_argument(
        "--rescore",
        nargs="+",
        default=[],
        help=(
            "re-derive citation_recall / gold_in_prompt from the answers stored in "
            "shards, then report. No LLM calls. Requires shards written by a "
            "version that persists 'answer' and 'gold_chunk_ids'."
        ),
    )
    args = ap.parse_args()

    if args.rescore:
        off_rows, on_rows = _load_shards(args.rescore)
        return _report(_rescore_rows(off_rows), _rescore_rows(on_rows), shard_out="")

    if args.merge_shards:
        return _report(*_load_shards(args.merge_shards), shard_out="")

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    family_map = FamilyMap()
    arms = {
        r["question_id"]: [str(c) for c in (r.get("chunk_ids") or [])] for r in _load_jsonl(RAW_DIR / f"{ARM}.jsonl")
    }

    tasks = []
    for qid, ids in arms.items():
        q = questions.get(qid)
        if not q or not ids:
            continue
        chunks = _chunks_for(ids, payload_index)
        if len(chunks) < 2:
            continue
        tasks.append((qid, q, chunks))
    tasks.sort(key=lambda t: t[0])
    if args.start:
        tasks = tasks[args.start :]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 1

    print(f"A/B KG fusion on {len(tasks)} questions (model={os.environ.get('RAG_LLM_MODEL')})", flush=True)

    def run(use_kg: bool) -> list[dict[str, Any]]:
        from app.rag.generation.context_builder import ContextBuilder
        from app.rag.generation.grounded_service import GroundedGenerationService

        sem = Semaphore(MAX_WORKERS)

        def one(task):
            qid, q, chunks = task
            qt = q.question_types[0] if q.question_types else ""
            gold = {
                c.chunk_id
                for c in chunks
                if any(matches_gold(payload_index.get(c.chunk_id, {}), u, family_map) for u in q.recall_units())
            }
            pool, kg_meta = list(chunks), {"provisions": 0, "injected": 0}
            with sem:
                if use_kg:
                    kg_chunks, kg_meta = _kg_chunks(q.question, limit=5)
                    if kg_chunks:
                        top_k = ContextBuilder(query_type=qt).max_context_chunks
                        pool = _fuse(pool, kg_chunks, top_k)
                        kg_meta["injected"] = len(kg_chunks)
                built = ContextBuilder(query_type=qt).build(q.question, pool, qt)
                svc = GroundedGenerationService()
                svc.llm_client = SSLBypassLLMClient(model=os.environ["RAG_LLM_MODEL"])
                sys_p, user_p = svc._render_prompt(q.question, built)
                llm = svc._call_llm(sys_p, user_p)
                answer = getattr(llm, "text", "") or ""
                tracked = svc._extract_citations(llm, pool, built)
                san = svc.sanitizer.sanitize(answer, tracked, pool)
                cited = {c.chunk_id for c in tracked}
                m = score_answer(answer, q.acceptable_conclusion or "", q.insufficient_evidence)
                m["citation_recall"] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
                m["groundedness_score"] = san.groundedness_score
                m["hallucination_detected"] = int(san.hallucination_detected)
                m["n_invalid_citations"] = len(san.invalid_citations)
                m["n_prompt_chunks"] = built.chunk_count
                m["gold_in_prompt"] = int(bool(cited & gold))
                return {
                    "qid": qid,
                    "answer_len": len(answer),
                    "llm_error": getattr(llm, "error", None),
                    "kg": kg_meta,
                    "m": m,
                    # Persist what the citation-dependent metrics are derived
                    # from.  citation_recall, groundedness_score and the
                    # gold-gained/lost counts all flow through
                    # CitationTracker, so fixing the tracker (e.g. the
                    # ``[Source n]`` form it used to drop) must not require
                    # re-spending LLM quota to re-measure them: with these
                    # fields a shard can be rescored offline.
                    "answer": answer,
                    "cited_chunk_ids": sorted(cited),
                    "gold_chunk_ids": sorted(gold),
                    "pool_chunk_ids": [c.chunk_id for c in pool],
                    "invalid_citations": len(san.invalid_citations),
                    "sanitizer_groundedness": san.groundedness_score,
                    "sanitizer_hallucination": int(san.hallucination_detected),
                }

        out = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            for fut in as_completed([ex.submit(one, t) for t in tasks]):
                try:
                    out.append(fut.result())
                except Exception as exc:
                    out.append({"qid": None, "exception": f"{type(exc).__name__}: {exc}", "m": None})
        return out

    return _report(run(use_kg=False), run(use_kg=True), shard_out=args.shard_out)


def _load_shards(paths: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rebuild both arm row lists from shard files (backgrounded runs do not survive)."""
    off: dict[str, Any] = {}
    on: dict[str, Any] = {}
    for path in paths:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        off.update(blob["kg_off"])
        on.update(blob["kg_on"])
    print(f"merged {len(off)} kg_off / {len(on)} kg_on rows from {len(paths)} shard(s)")
    return list(off.values()), list(on.values())


def _rescore_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute citation-derived metrics from the stored answer text.

    ``citation_recall``, ``gold_in_prompt`` and the gold gained/lost sets are
    all products of ``CitationTracker``.  When the tracker changes (e.g. it
    used to drop the ``[Source n]`` form that ``ContextBuilder`` actually
    renders), those numbers change too — and re-deriving them must not cost
    LLM quota, so the shard keeps the answer text and gold ids needed.

    Rows that lack the stored fields are returned untouched, so mixing old and
    new shards degrades to "unchanged" rather than to a wrong number.
    """
    out: list[dict[str, Any]] = []
    n_rescored = n_skipped = 0
    for row in rows:
        answer = row.get("answer")
        gold = set(row.get("gold_chunk_ids") or [])
        if not answer or "gold_chunk_ids" not in row:
            n_skipped += 1
            out.append(row)
            continue
        try:
            cited = _cited_ids_from_answer(answer, row)
        except Exception:
            n_skipped += 1
            out.append(row)
            continue
        m = dict(row.get("m") or {})
        m["citation_recall"] = round(len(cited & gold) / max(len(gold), 1), 4) if gold else 0.0
        m["gold_in_prompt"] = int(bool(cited & gold))
        new_row = dict(row)
        new_row["m"] = m
        new_row["cited_chunk_ids"] = sorted(cited)
        out.append(new_row)
        n_rescored += 1
    print(f"rescored {n_rescored} row(s), skipped {n_skipped} without stored answers", file=sys.stderr)
    return out


def _cited_ids_from_answer(answer: str, row: dict[str, Any]) -> set[str]:
    """Map citation markers in *answer* back to chunk ids.

    The shard stores which chunks were in the prompt (``cited_chunk_ids`` from
    the original run is the cited set, not the pool), so the pool must be
    rebuilt.  Marker *numbers* are prompt positions, so the stored
    ``n_prompt_chunks`` plus the original ordering is required; when that is
    unavailable the row is left alone by the caller.
    """
    # Without the prompt ordering there is no way to turn "[Source 3]" back
    # into a chunk id, so signal that clearly rather than guessing.
    pool = row.get("pool_chunk_ids")
    if not pool:
        raise ValueError("shard has no pool_chunk_ids; cannot map markers to chunk ids")
    from app.rag.generation.citation_tracker import CitationTracker

    from app.rag.retrieval.result import RetrievedChunk

    chunks = [RetrievedChunk(chunk_id=cid, score=0.0, text="", document_title="") for cid in pool]
    return {c.chunk_id for c in CitationTracker().extract(answer, chunks)}


def _report(off_rows: list[dict[str, Any]], on_rows: list[dict[str, Any]], shard_out: str = "") -> int:
    A = {r["qid"]: r for r in off_rows if r.get("m")}
    B = {r["qid"]: r for r in on_rows if r.get("m")}
    common = sorted(set(A) & set(B))
    if not common:
        print("ABORT: no paired results", file=sys.stderr)
        return 1

    changed = [q for q in common if A[q]["m"]["gold_in_prompt"] == 0 and B[q]["m"]["gold_in_prompt"] == 1]
    lost = [q for q in common if A[q]["m"]["gold_in_prompt"] == 1 and B[q]["m"]["gold_in_prompt"] == 0]
    injected = [q for q in common if (B[q].get("kg") or {}).get("injected", 0) > 0]

    keys = (
        "binary_correct",
        "answer_correctness",
        "citation_recall",
        "groundedness_score",
        "hallucination_detected",
        "n_invalid_citations",
        "n_prompt_chunks",
    )

    def agg(rows, qids):
        return {k: round(statistics.fmean(rows[q]["m"][k] for q in qids), 4) for k in keys} if qids else {}

    ao, bo = agg(A, common), agg(B, common)
    co, cbo = agg(A, changed), agg(B, changed)

    def paired(qids, metric="binary_correct"):
        d = [B[q]["m"][metric] - A[q]["m"][metric] for q in qids]
        sd = statistics.stdev(d) if len(d) > 1 else 0.0
        return {
            "n": len(d),
            "improved": sum(1 for x in d if x > 0),
            "regressed": sum(1 for x in d if x < 0),
            "net": sum(d),
            "mean_delta": round(statistics.fmean(d), 4) if d else None,
            "t": round(statistics.fmean(d) / (sd / len(d) ** 0.5), 3) if sd else None,
        }

    out = {
        "model": os.environ.get("RAG_LLM_MODEL"),
        "n_paired": len(common),
        "kg_injected_qids": injected,
        "n_kg_injected": len(injected),
        "kg_provisions_mean": round(statistics.fmean((B[q].get("kg") or {}).get("provisions", 0) for q in common), 2),
        "gold_gained_qids": changed,
        "gold_lost_qids": lost,
        "aggregate_all": {"kg_off": ao, "kg_on": bo},
        "aggregate_changed_subset": {"kg_off": co, "kg_on": cbo},
        "paired_all": paired(common),
        "paired_changed": paired(changed) if changed else None,
        "paired_all_by_metric": {
            k: paired(common, k)
            for k in ("binary_correct", "answer_correctness", "citation_recall", "groundedness_score")
        },
        "limitations": [
            "Single free-tier model, one run per arm, no seed replication.",
            "Soft correctness reproduced on only 1/89 questions across two runs of the "
            "window A/B, so answer_correctness deltas at this size are model noise.",
            "KG fusion reorders the candidate pool via RRF, so a difference here "
            "confounds 'KG evidence added' with 'vector ranking perturbed'.",
        ],
    }

    if shard_out:
        Path(shard_out).write_text(
            json.dumps(
                {"kg_off": {q: A[q] for q in common}, "kg_on": {q: B[q] for q in common}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"shard written: {shard_out} ({len(common)} paired)")
        return 0

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("=" * 76)
    print(f"KG-fusion A/B  n={len(common)}  model={out['model']}")
    print("=" * 76)
    print(
        f"mean KG provisions/query: {out['kg_provisions_mean']}  "
        f"questions with KG injected: {len(injected)}/{len(common)}"
    )
    print(f"gold newly in prompt: {len(changed)}  gold lost: {len(lost)}")
    print()
    print(f"{'metric':<26} {'kg_off':>9} {'kg_on':>9} {'delta':>9}")
    print("-" * 58)
    for k in keys:
        print(f"{k:<26} {ao[k]:>9.4f} {bo[k]:>9.4f} {bo[k] - ao[k]:>+9.4f}")
    print()
    pa = out["paired_all"]
    print(f"paired binary (all n={pa['n']}): improved {pa['improved']}, regressed {pa['regressed']}, t={pa['t']}")
    if changed:
        pc = out["paired_changed"]
        print(f"paired binary (gold-gained n={pc['n']}): improved {pc['improved']}, regressed {pc['regressed']}")
    print()
    print(f"written: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
