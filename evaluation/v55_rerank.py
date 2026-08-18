"""RANKING_CEILING_V5.5 — sec_act rerank on the question-derived expanded pool.

Measures whether adding the production-realistic (question-derived) identifier
route to the base union pool converts to R@10 under the existing sec_act legal
reranker.  Reuses rerank_legal.py's feature/rerank machinery verbatim; the only
difference from the V5 rerank is the pool composition:

  P1 pool = dense@500 ∪ sparse@500 ∪ KG@500 ∪ question-ident(sparse@500)

The question-ident arm is the question-text-only identifier query
(v55_identifier_route.identifier_query).  RRF credit for the identifier arm
uses its own sparse ranking so ident-recovered chunks can actually surface.

Output: evaluation/out/ceiling_v5/v55_rerank.json — R@10/20/50 per weight grid,
P0 (base) vs P1 (base+ident) under each grid.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT.mkdir(parents=True, exist_ok=True)


def load_jsonl(path: Path) -> dict[str, dict]:
    recs = {}
    if path.exists():
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                recs[r["question_id"]] = r
    return recs


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    from evaluation.benchmark import load_questions
    from evaluation.report_ceiling import load_payload_index
    from evaluation.rerank_legal import build_pool, rank_of, rerank, rrf_scores
    from evaluation.resolution import FamilyMap

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

        grids = {
            "base_rrf": {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0},
            "sec_only": {"sec": 3.0, "act": 0.0, "exact": 0.0, "lex": 0.0},
            "sec_act": {"sec": 2.0, "act": 1.5, "exact": 0.0, "lex": 0.0},
            "full_legal": {"sec": 2.0, "act": 1.0, "exact": 4.0, "lex": 0.5},
        }

        def pool_for(qid, q, with_ident: bool):
            d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
            if not (d and s and k):
                return None, None
            pool = build_pool(d, s, k, payload_index, family_map, slice_depth=500, kg_slice=500)
            rrf = rrf_scores([
                [{"key": c} for c in d.get("chunk_ids", [])[:500]],
                [{"key": c} for c in s.get("chunk_ids", [])[:500]],
                [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
            ])
            if with_ident:
                rec = ident.get(qid, {})
                ids = [str(c) for c in rec.get("chunk_ids", [])[:500]]
                if ids:
                    rrf = rrf_scores([
                        [{"key": c} for c in d.get("chunk_ids", [])[:500]],
                        [{"key": c} for c in s.get("chunk_ids", [])[:500]],
                        [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
                        [{"key": c} for c in ids],
                    ])
            return pool, rrf

        out = {}
        for with_ident, label in ((False, "P0_base"), (True, "P1_base_plus_ident")):
            per_q = {}
            for qid, q in questions.items():
                pool, rrf = pool_for(qid, q, with_ident)
                if pool is None:
                    continue
                base_ranked = rerank(pool, q.question, family_map, rrf, grids["base_rrf"])
                per_q[qid] = (q, pool, rrf, base_ranked)

            grid_out = {}
            for gname, w in grids.items():
                recall = {10: 0.0, 20: 0.0, 50: 0.0}
                any_hits = {10: 0, 20: 0, 50: 0}
                conversions = 0
                n = 0
                for qid, (q, pool, rrf, base_ranked) in per_q.items():
                    reranked = rerank(pool, q.question, family_map, rrf, w)
                    n += 1
                    rel = q.relevant_units()
                    for kk in (10, 20, 50):
                        uh = 0
                        for unit in rel:
                            r = rank_of(reranked, unit, payload_index, family_map)
                            if r is not None and r <= kk:
                                uh += 1
                        recall[kk] += uh / max(len(rel), 1)
                        any_hits[kk] += int(uh > 0)
                    # conversion: any relevant gold in 11..500 under base RRF ->
                    # top-10 under this grid
                    base_any_10 = any(
                        (rank_of(base_ranked, u, payload_index, family_map) or 1 << 30) <= 10
                        for u in rel
                    )
                    new_any_10 = any(
                        (rank_of(reranked, u, payload_index, family_map) or 1 << 30) <= 10
                        for u in rel
                    )
                    if not base_any_10 and new_any_10:
                        conversions += 1
                grid_out[gname] = {
                    "R@10": round(recall[10] / max(n, 1), 4),
                    "R@20": round(recall[20] / max(n, 1), 4),
                    "R@50": round(recall[50] / max(n, 1), 4),
                    "any_hit_R@10": round(any_hits[10] / max(n, 1), 4),
                    "conversions_into_top10": conversions,
                    "n": n,
                }
            out[label] = grid_out

        (OUT / "v55_rerank.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
