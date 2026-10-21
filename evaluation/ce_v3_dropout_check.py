"""Dropout-side check: where gold lands within v3's head-150 per promotion qid.

Uses only shared non-app imports (no section_prefix) so it runs offline. For
each promotion qid: base-RRF head and v3-reranked head, gold ranks, dropout flag.

Writes evaluation/out/ceiling_v5/ce_v3_dropout_check.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import torch

torch.set_num_threads(4)

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.eval_v2_checkpoint import load_jsonl, load_payload_index_simple
from evaluation.rerank_legal import build_pool, rerank, rrf_scores
from evaluation.resolution import FamilyMap, matches_gold

CE_V3 = PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v3_K500"
POOL_HEAD = 150
CE_BATCH = 64
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "ce_v3_dropout_check.json"

# Six all-model-fail promotion qids from the earlier gate analysis.
_DROPOUTS = {"Q024", "Q025", "Q063", "Q072", "Q122", "Q139"}


def _rrf_lists(d, s, k, rec):
    lists = [
        [{"key": c} for c in d.get("chunk_ids", [])[:500]],
        [{"key": c} for c in s.get("chunk_ids", [])[:500]],
        [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
    ]
    ids = [str(c) for c in rec.get("chunk_ids", [])[:500]]
    if ids:
        lists.append([{"key": c} for c in ids])
    return lists


def main() -> int:
    from sentence_transformers import CrossEncoder

    payload_index = load_payload_index_simple()
    print(f"[1/4] payload_index={len(payload_index)} points", flush=True)
    fm = FamilyMap()
    questions = {q.question_id: q for q in load_questions()}

    raw_dir = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "raw"
    dense = load_jsonl(raw_dir / "A_dense.jsonl")
    sparse = load_jsonl(raw_dir / "B_sparse.jsonl")
    kg = load_jsonl(raw_dir / "D_kg.jsonl")
    ident = load_jsonl(PROJECT_ROOT / "evaluation" / "out" / "cache" / "v55_ident" / "sparse_identifier.jsonl")

    ce = CrossEncoder(CE_V3.as_posix(), max_length=256)

    pool = json.loads((CACHE_DIR / "train_pool_v1.json").read_text(encoding="utf-8"))
    promo_all = list(pool["eval_gate"]["promotion"])

    print(f"[2/4] Scoring {len(promo_all)} promotion qids...", flush=True)
    out: dict[str, dict] = {}
    for qid in promo_all:
        q = questions.get(qid)
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        rec = ident.get(qid, {})
        if q is None or not (d and s and k):
            out[qid] = {"note": "missing question or arm data"}
            continue
        pool_items = build_pool(d, s, k, payload_index, fm, slice_depth=500, kg_slice=500)
        rrf = rrf_scores(_rrf_lists(d, s, k, rec))
        base = rerank(pool_items, q.question, fm, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
        head = base[:POOL_HEAD]

        pairs = [(q.question, str(it["payload"].get("chunk_text") or it["payload"].get("text") or "")) for it in head]
        scores = [float(x) for x in ce.predict(pairs, batch_size=CE_BATCH)]
        order = sorted(range(len(head)), key=lambda i: scores[i], reverse=True)
        reranked = [head[i] for i in order]

        units = q.recall_units()
        gold_in_head = [any(matches_gold(it["payload"], u, fm, text_fallback=True) for u in units) for it in head]
        gold_in_reranked = [any(matches_gold(it["payload"], u, fm, text_fallback=True) for u in units) for it in reranked]
        reranked_gold_depth = next((i + 1 for i, g in enumerate(gold_in_reranked) if g), None)

        out[qid] = {
            "head_gold_positions": [i + 1 for i, g in enumerate(gold_in_head) if g],
            "reranked_gold_positions": [i + 1 for i, g in enumerate(gold_in_reranked) if g],
            "reranked_gold_depth": reranked_gold_depth,
            "n_gold_units": len(units),
            "pool_size": len(pool_items),
            "is_dropout": qid in _DROPOUTS,
        }

    OUT_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[3/4] wrote {OUT_FILE}", flush=True)

    drops = {q: r for q, r in out.items() if r.get("is_dropout")}
    print(f"[4/4] dropout qids: {len(drops)}", flush=True)
    for qid, r in sorted(drops.items()):
        print(f"  {qid}: head_gold={r.get('head_gold_positions')} reranked_gold_depth={r.get('reranked_gold_depth')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
