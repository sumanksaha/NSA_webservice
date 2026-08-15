"""CE_RERANK_EVAL — cross-encoder rerank on the P1 pool (base + identifier route).

Measures R@10/20/50 for three rerankers over the SAME candidate pool, so the
comparison is apples-to-apples:

  pool        dense@500 ∪ sparse@500 ∪ KG@500 ∪ question-ident@500
              (P1 from v55_rerank), truncated to the top-150 by base RRF —
              the head a production reranker would actually see.

  rerankers   - sec_act    deterministic legal features (sec=2, act=1.5)
              - ce_base    cross-encoder/ms-marco-MiniLM-L-6-v2 (untuned)
              - ce_finetuned  evaluation/out/models/legal_ce_v1 (mined pairs)

Also reports conversions: gold units at base-RRF rank 11..150 that each
reranker promotes into top-10.

Resumable: per-question metrics are appended to a checkpoint JSONL
(``evaluation/out/ceiling_v5/ce_rerank_eval.checkpoint.jsonl``) as each
question finishes.  A rerun skips questions already in the checkpoint, so an
interrupted run (this host killed a full run ~40% in, losing everything)
resumes where it left off.  On completion the aggregate is written to
``evaluation/out/ceiling_v5/ce_rerank_eval.json``.

Output: evaluation/out/ceiling_v5/ce_rerank_eval.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Imported at module level so _measure() can use it (importing rerank_legal
# is cheap and side-effect free).
from evaluation.rerank_legal import rank_of  # noqa: E402

from dotenv import load_dotenv

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)

CE_BASE = "cross-encoder/ms-marco-MiniLM-L-6-v2"
CE_FINETUNED = PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v1"
POOL_HEAD = 150
CE_BATCH = 64
CHECKPOINT = OUT / "ce_rerank_eval.checkpoint.jsonl"


def load_jsonl(path: Path) -> dict[str, dict]:
    recs = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                recs[r["question_id"]] = r
    return recs


def load_checkpoint() -> dict[str, dict]:
    """Return per-question reranker metrics already computed."""
    done: dict[str, dict] = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.open(encoding="utf-8"):
            line = line.strip()
            if line:
                rec = json.loads(line)
                done[rec["question_id"]] = rec
    return done


def append_checkpoint(rec: dict) -> None:
    with CHECKPOINT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    import torch

    # Same bound as finetune_ce.py: on this Windows host, PyTorch's default
    # one-thread-per-core pool thrashes for small per-step work.  4 threads
    # is the measured sweet spot.
    torch.set_num_threads(4)

    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.resolution import FamilyMap
    from evaluation.report_ceiling import load_payload_index
    from evaluation.rerank_legal import build_pool, rrf_scores, rerank

    app = create_app()
    with app.app_context():
        payload_index = load_payload_index()
        family_map = FamilyMap()
        questions = {q.question_id: q for q in load_questions()}

        raw_dir = Path("evaluation/out/ceiling_v5/raw")
        dense = load_jsonl(raw_dir / "A_dense.jsonl")
        sparse = load_jsonl(raw_dir / "B_sparse.jsonl")
        kg = load_jsonl(raw_dir / "D_kg.jsonl")
        ident = load_jsonl(Path("evaluation/out/cache/v55_ident/sparse_identifier.jsonl"))

        sec_act_w = {"sec": 2.0, "act": 1.5, "exact": 0.0, "lex": 0.0}

        # ---- build per-question head-of-pool (top-150 by base RRF) ----
        per_q = {}
        for qid, q in questions.items():
            d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
            if not (d and s and k):
                continue
            pool = build_pool(d, s, k, payload_index, family_map, slice_depth=500, kg_slice=500)
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:500]],
                [{"key": c} for c in s.get("chunk_ids", [])[:500]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
            ])
            rec = ident.get(qid, {})
            ids = [str(c) for c in rec.get("chunk_ids", [])[:500]]
            if ids:
                rrf = rrf_scores([
                    [{"key": c} for c in d.get("chunk_ids", [])[:500]],
                    [{"key": c} for c in s.get("chunk_ids", [])[:500]],
                    [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
                    [{"key": c} for c in ids],
                ])
            base = rerank(pool, q.question, family_map, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
            head = base[:POOL_HEAD]
            if not head:
                continue
            per_q[qid] = (q, head, rrf)

        print(f"[ce_rerank_eval] {len(per_q)} questions, pool head = {POOL_HEAD}", file=sys.stderr)

        done = load_checkpoint()
        todo = [qid for qid in per_q if qid not in done]
        print(
            f"[ce_rerank_eval] checkpoint has {len(done)} questions; "
            f"{len(todo)} to compute",
            file=sys.stderr,
        )

        # ---- rerankers ----
        from sentence_transformers import CrossEncoder

        ce_models = {
            "ce_base": CrossEncoder(CE_BASE, max_length=256),
            "ce_finetuned": CrossEncoder(CE_FINETUNED.as_posix(), max_length=256),
        }

        def rank_with_ce(items: list[dict], query: str, ce) -> list[dict]:
            pairs = [
                (query, str(it["payload"].get("chunk_text") or it["payload"].get("text") or ""))
                for it in items
            ]
            scores = ce.predict(pairs, batch_size=CE_BATCH)
            scored = sorted(zip(scores, items), key=lambda x: float(x[0]), reverse=True)
            return [it for _, it in scored]

        t0 = time.time()
        n_done = len(done)
        for qid in todo:
            q, head, rrf = per_q[qid]
            # base-any-10 state: is any relevant gold unit in the top-10 of the
            # head (which is base-RRF order)?  Stored once so conversions can
            # be aggregated later without re-ranking the head.
            rel = q.relevant_units()
            base_any_10 = any(
                (rank_of(head, u, payload_index, family_map) or 1 << 30) <= 10
                for u in rel
            )
            rec = {"question_id": qid, "base_any_10": base_any_10, "rerankers": {}}

            def _one(items):
                return _measure_one(items, q, payload_index, family_map)

            rec["rerankers"]["sec_act"] = _one(
                rerank(head, q.question, family_map, rrf, sec_act_w)
            )
            for name, ce in ce_models.items():
                rec["rerankers"][name] = _one(rank_with_ce(head, q.question, ce))

            append_checkpoint(rec)
            done[qid] = rec
            n_done += 1
            if n_done % 10 == 0 or n_done == len(per_q):
                eta = (time.time() - t0) / n_done * (len(per_q) - n_done)
                print(
                    f"[ce_rerank_eval] {n_done}/{len(per_q)} questions "
                    f"({time.time() - t0:.0f}s elapsed, ETA {eta:.0f}s)",
                    file=sys.stderr,
                )

        # ---- aggregate ----
        results = {name: _aggregate(done, name) for name in ("sec_act", "ce_base", "ce_finetuned")}
        results["_meta"] = {
            "pool": "P1 dense@500 ∪ sparse@500 ∪ KG@500 ∪ question-ident@500, head-150 by base RRF",
            "n_questions": len(done),
            "rerankers": {
                "sec_act": "deterministic legal features (sec=2, act=1.5)",
                "ce_base": "cross-encoder/ms-marco-MiniLM-L-6-v2 (untuned)",
                "ce_finetuned": "evaluation/out/models/legal_ce_v1 (2,131 mined pairs)",
            },
            "checkpoint": CHECKPOINT.name,
        }

        (OUT / "ce_rerank_eval.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=1))
    return 0


def _measure_one(items, q, payload_index, family_map) -> dict:
    """Per-question metrics for one reranked list."""
    rel = q.relevant_units()
    unit_hits = {10: 0, 20: 0, 50: 0}
    any_hit = {10: 0, 20: 0, 50: 0}
    for unit in rel:
        r = rank_of(items, unit, payload_index, family_map)
        for kk in (10, 20, 50):
            if r is not None and r <= kk:
                unit_hits[kk] += 1
                any_hit[kk] = 1
    # conversion: gold at base-RRF rank 11..150 -> top-10 by this reranker.
    # head is already in base-RRF order, so a unit's base rank is its
    # index+1 in head when not promoted (rank_of on head gives the same).
    # The caller only passes the reranked list, so base-any-10 is computed by
    # the caller against the head (stored as ``base_any_10`` on the question).
    return {
        "n_rel": len(rel),
        "unit_hits": unit_hits,
        "any_hit": any_hit,
    }


def _aggregate(done: dict[str, dict], name: str) -> dict:
    recall = {10: 0.0, 20: 0.0, 50: 0.0}
    any_hits = {10: 0, 20: 0, 50: 0}
    conversions = 0
    n = 0
    for qid, rec in done.items():
        rr = rec["rerankers"].get(name)
        if rr is None:
            continue
        n += 1
        n_rel = max(rr["n_rel"], 1)
        for kk in (10, 20, 50):
            recall[kk] += rr["unit_hits"][kk] / n_rel
            any_hits[kk] += rr["any_hit"][kk]
        # Conversion needs the base-RRF head state.  We store it on the
        # checkpoint record at eval time (see main()); fall back to 0 if a
        # legacy record lacks it.
        if rec.get("base_any_10") is False and rr.get("any_hit", {}).get(10):
            conversions += 1
    return {
        "R@10": round(recall[10] / max(n, 1), 4),
        "R@20": round(recall[20] / max(n, 1), 4),
        "R@50": round(recall[50] / max(n, 1), 4),
        "any_hit_R@10": round(any_hits[10] / max(n, 1), 4),
        "conversions_into_top10": conversions,
        "n": n,
    }


if __name__ == "__main__":
    raise SystemExit(main())
