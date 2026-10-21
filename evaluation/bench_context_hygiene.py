"""Context-hygiene benchmark: does dropping amendment prose free slots?

Deterministic, offline, no LLM calls.  Replays the cached retrieval arms
(``evaluation/out/ceiling_v5/raw/``) through the production ``ContextBuilder``
with the amendment-prose drop on vs off and reports the two numbers that
matter:

``gold_in_prompt``
    Whether the gold provision survives into the packed window.  The guard
    rail: hygiene that drops gold is a regression even when it frees slots.
    The stamper measurement (``evaluation/FINDINGS_89_STATUS.md``) predicted
    no gold loss — no gold unit ever resolved through amendment prose alone —
    and this replays the packing effect against the same cached pools.

``slots_reclaimed``
    How many window slots amendment prose occupied per question.  The corpus
    census found 27.3% of chunks under 40 chars; index-chunk dropping already
    removes the sub-60-char tail, so what remains in the window are longer
    amendment notifications that the length floor cannot catch.

Usage:
    python -m evaluation.bench_context_hygiene
    python -m evaluation.bench_context_hygiene --arms C_hybrid --top-k 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "context_hygiene_bench.json"


def run(arms: list[str], top_k: int) -> int:
    from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index, _load_jsonl, RAW_DIR
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap, matches_gold
    from app.rag.generation.context_builder import ContextBuilder, is_amendment_prose
    from app.shared.config import cfg

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    family_map = FamilyMap()
    if not payload_index:
        print("missing payload_index.jsonl — cannot rebuild chunk text", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    for arm in arms:
        recs = {r["question_id"]: r for r in _load_jsonl(RAW_DIR / f"{arm}.jsonl")}
        if not recs:
            continue
        builder = ContextBuilder(max_chunks=top_k)

        cfg.context_drop_amendment_prose = False
        off: dict[str, Any] = {}
        for qid, rec in recs.items():
            chunks = _chunks_for([str(c) for c in (rec.get("chunk_ids") or [])][:top_k], payload_index)
            if len(chunks) < 2:
                continue
            built = builder.build(rec.get("query", ""), chunks, query_type="general")
            off[qid] = [c["chunk_id"] for c in built.citations]

        cfg.context_drop_amendment_prose = True
        on: dict[str, Any] = {}
        for qid, rec in recs.items():
            chunks = _chunks_for([str(c) for c in (rec.get("chunk_ids") or [])][:top_k], payload_index)
            if len(chunks) < 2:
                continue
            built = builder.build(rec.get("query", ""), chunks, query_type="general")
            on[qid] = [c["chunk_id"] for c in built.citations]

        # Amendment-prose census over the arm's own pool (pre-packing).
        amendment_in_pool = 0
        pool_total = 0
        for qid in off:
            rec = recs[qid]
            for cid in [str(c) for c in (rec.get("chunk_ids") or [])][:top_k]:
                payload = payload_index.get(cid)
                if payload is None:
                    continue
                pool_total += 1
                if is_amendment_prose(payload.get("chunk_text", payload.get("text", "")) or ""):
                    amendment_in_pool += 1

        gold = {qid: set(off[qid]) != set(on[qid]) for qid in off}

        def gold_prompt(ids_by_qid: dict[str, list[str]]) -> tuple[int, int]:
            n_scored = 0
            n_hit = 0
            for qid, ids in ids_by_qid.items():
                question = questions.get(qid)
                if question is None:
                    continue
                units = question.recall_units()
                if not units:
                    continue
                n_scored += 1
                for cid in ids:
                    payload = payload_index.get(str(cid))
                    if payload is None:
                        continue
                    if any(matches_gold(payload, u, family_map, text_fallback=True) for u in units):
                        n_hit += 1
                        break
            return n_hit, n_scored

        off_hit, off_scored = gold_prompt(off)
        on_hit, on_scored = gold_prompt(on)
        slots_off = sum(len(v) for v in off.values())
        slots_on = sum(len(v) for v in on.values())
        results.append({
            "arm": arm,
            "n_questions": len(off),
            "amendment_share_of_pool": round(amendment_in_pool / pool_total, 4) if pool_total else None,
            "gold_in_prompt_off": off_hit,
            "gold_in_prompt_on": on_hit,
            "gold_scored": off_scored,
            "gold_rate_off": round(off_hit / off_scored, 4) if off_scored else None,
            "gold_rate_on": round(on_hit / on_scored, 4) if on_scored else None,
            "window_slots_off": slots_off,
            "window_slots_on": slots_on,
            "questions_window_changed": sum(1 for v in gold.values() if v),
        })

    cfg.context_drop_amendment_prose = True
    output = {
        "benchmark": "context_hygiene",
        "method": (
            "Deterministic, offline replay of cached retrieval arms through the "
            "production ContextBuilder with the amendment-prose drop off vs on. "
            "No LLM calls: measures the PROMPT EFFECT of the drop and the "
            "gold-in-prompt guard rail, not answer quality."
        ),
        "top_k": top_k,
        "arms": results,
        "limitations": [
            "Cached arms carry chunk_ids but not retrieval scores; ordering is arm rank.",
            "Gold is matched at PROVISION level via matches_gold, not by chunk id.",
            "window_slots_on < window_slots_off counts freed slots; a gold_rate drop is a regression.",
        ],
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 92)
    print("Context hygiene — amendment-prose drop (deterministic, 0 LLM)")
    print("=" * 92)
    hdr = (
        f"{'arm':<11}{'n':>4}{'amendShare':>11}{'goldOff':>9}{'goldOn':>8}{'slotsOff':>10}{'slotsOn':>9}{'changed':>9}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        share = f"{r['amendment_share_of_pool']:.3f}" if r["amendment_share_of_pool"] is not None else "--"
        print(
            f"{r['arm']:<11}{r['n_questions']:>4}{share:>11}{r['gold_in_prompt_off']:>9}"
            f"{r['gold_in_prompt_on']:>8}{r['window_slots_off']:>10}{r['window_slots_on']:>9}"
            f"{r['questions_window_changed']:>9}"
        )
    print("\ngoldOff/goldOn = questions with gold in the packed window (must not drop)")
    print(f"written: {OUT_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arms", default="C_hybrid,V3_hybrid,V4_hybrid")
    parser.add_argument("--top-k", type=int, default=20)
    args = parser.parse_args()
    return run([a.strip() for a in args.arms.split(",") if a.strip()], args.top_k)


if __name__ == "__main__":
    raise SystemExit(main())
