"""Diagnose WHERE gold is lost per promotion qid.

For each gate qid, measures:
  pool_gold_depth   first gold hit rank in the FULL union pool (@500 arms)
  head_gold_depth   first gold hit rank in the base-RRF head-150
  v3_gold_depth     first gold hit rank after v3 CE rerank of the head
  gold_in_prompt_v3 ContextBuilder admission (the gate metric)

Buckets (mirrors failure_attribution):
  never_in_pool / lost_in_head / demoted_by_rerank / builder_dropped / ok
Output: evaluation/out/ceiling_v5/ce_v3_promotion_diagnosis.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import torch

torch.set_num_threads(4)

from app.rag.retrieval.section_prefix import prefix_passage
from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.eval_v2_checkpoint import load_jsonl, load_payload_index_simple
from evaluation.failure_attribution import _gold_in_prompt
from evaluation.rerank_legal import build_pool, rank_of, rerank, rrf_scores
from evaluation.resolution import FamilyMap

CE_V3 = PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v3_K500"
POOL_HEAD = 150
CE_BATCH = 64
OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "ce_v3_promotion_diagnosis.json"

_POOL = json.loads((CACHE_DIR / "train_pool_v1.json").read_text(encoding="utf-8"))
_PROMOTION: list[str] = list(_POOL["eval_gate"]["promotion"])
_GUARD: list[str] = list(_POOL["eval_gate"]["guard_check"])


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

    print("[2/4] Loading legal_ce_v3_K500...", flush=True)
    ce = CrossEncoder(CE_V3.as_posix(), max_length=256)

    def rerank_with_v3(head, query):
        pairs = [
            (
                query,
                prefix_passage(
                    str(it["payload"].get("chunk_text") or it["payload"].get("text") or ""),
                    it["payload"].get("section_number"),
                    it["payload"].get("clause_number"),
                ),
            )
            for it in head
        ]
        scores = ce.predict(pairs, batch_size=CE_BATCH)
        order = sorted(range(len(head)), key=lambda i: float(scores[i]), reverse=True)
        return [head[i] for i in order]

    gate_qids = _PROMOTION + _GUARD
    print(f"[3/4] Diagnosing {len(gate_qids)} qids...", flush=True)
    per_q: dict[str, dict] = {}
    n_done = 0
    for qid in gate_qids:
        q = questions.get(qid)
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        rec = ident.get(qid, {})
        if q is None or not (d and s and k):
            per_q[qid] = {"note": "missing question or arm data"}
            n_done += 1
            continue
        units = q.recall_units()
        pool = build_pool(d, s, k, payload_index, fm, slice_depth=500, kg_slice=500)
        rrf = rrf_scores(_rrf_lists(d, s, k, rec))
        base = rerank(pool, q.question, fm, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
        head = base[:POOL_HEAD]
        reranked = rerank_with_v3(head, q.question)

        def _depth(items):
            best = None
            for u in units:
                r = rank_of(items, u, payload_index, fm)
                if r is not None and (best is None or r < best):
                    best = r
            return best

        pool_depth = _depth(pool)
        head_depth = _depth(head)
        v3_depth = _depth(reranked)

        v3_ids = [str(it.get("key") or it.get("chunk_id") or "") for it in reranked]
        v3_ids = [c for c in v3_ids if c in payload_index]
        gip = bool(_gold_in_prompt(qid, q, {qid: v3_ids}, payload_index, fm))

        if pool_depth is None:
            bucket = "never_in_pool"
        elif head_depth is None:
            bucket = "lost_in_head"
        elif v3_depth is None or v3_depth > head_depth:
            bucket = "demoted_by_rerank"
        elif not gip:
            bucket = "builder_dropped"
        else:
            bucket = "ok"

        per_q[qid] = {
            "n_gold_units": len(units),
            "pool_size": len(pool),
            "pool_gold_depth": pool_depth,
            "head_gold_depth": head_depth,
            "v3_gold_depth": v3_depth,
            "gold_in_prompt_v3": gip,
            "bucket": bucket,
        }
        n_done += 1
        if n_done % 20 == 0 or n_done == len(gate_qids):
            print(f"  {n_done}/{len(gate_qids)} diagnosed", flush=True)

    print("[4/4] Writing diagnosis...", flush=True)
    OUT_FILE.write_text(json.dumps(per_q, indent=2), encoding="utf-8")
    buckets: dict[str, int] = {}
    for rec in per_q.values():
        b = rec.get("bucket")
        if b:
            buckets[b] = buckets.get(b, 0) + 1
    print("bucket counts:", json.dumps(buckets), flush=True)
    print(f"wrote {OUT_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
