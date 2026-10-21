"""v3 promotion-gate runner: offline gold_in_prompt on 26 promotion + 61 guard qids.

Per gate qid (all cached/offline, no Qdrant/Neo4j/LLM):
  1. P1 pool (dense@sparse@KG@ident 500, head-150 by base RRF) — same as
     evaluation/eval_v2_checkpoint.py.
  2. Rerank head-150 with legal_ce_v3_K500 (CrossEncoder, max_length 256).
  3. Feed reranked head through ContextBuilder (same as
     failure_attribution._gold_in_prompt) -> gold_in_prompt bool.

Gate (train_pool_v1.json eval_gate): every promotion qid flips True AND no
guard qid that was True goes False (vs frozen failure_attribution.json).

Output: evaluation/out/ceiling_v5/ce_v3_promotion_gate.json for the default
v3 run (``--model v3``); ``--model`` comparisons write
evaluation/out/ceiling_v5/ce_promotion_gate_<model>.json instead, so a
calibration run cannot overwrite the v3 verdict.
Usage:  python evaluation/ce_v3_promotion_gate.py
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
from evaluation.failure_attribution import _gold_in_prompt
from evaluation.rerank_legal import build_pool, rerank, rrf_scores
from evaluation.resolution import FamilyMap

MODELS = {
    "base": "cross-encoder/ms-marco-MiniLM-L-6-v2",
    "v1": str(PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v1"),
    "v2": str(PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v2_K500"),
    "v3": str(PROJECT_ROOT / "evaluation" / "out" / "models" / "legal_ce_v3_K500"),
}
POOLED_OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "ce_v3_promotion_gate.json"
POOL_HEAD = 150
CE_BATCH = 64

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
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(MODELS), default="v3")
    ap.add_argument("--qids-only", action="store_true", help="score only the 26 promotion qids")
    args = ap.parse_args()

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

    frozen = json.loads(
        (PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "failure_attribution.json").read_text(encoding="utf-8")
    )
    frozen_gip = {r["qid"]: r.get("gold_in_prompt") for r in frozen.get("per_question") or []}

    model_name = args.model
    model_path = MODELS[model_name]
    print(f"[2/4] Loading {model_name}: {model_path}", flush=True)
    ce = CrossEncoder(model_path, max_length=256)

    def rerank_with_v3(head, query):
        from app.rag.retrieval.section_prefix import prefix_passage

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

    gate_qids = list(_PROMOTION) if args.qids_only else _PROMOTION + _GUARD
    print(f"[3/4] Scoring {len(gate_qids)} qids with {model_name}...", flush=True)
    per_q: dict[str, dict] = {}
    n_done = 0
    for qid in gate_qids:
        q = questions.get(qid)
        d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
        rec = ident.get(qid, {})
        if q is None or not (d and s and k):
            per_q[qid] = {"gold_in_prompt": None, "note": "missing question or arm data"}
            continue
        pool = build_pool(d, s, k, payload_index, fm, slice_depth=500, kg_slice=500)
        rrf = rrf_scores(_rrf_lists(d, s, k, rec))
        base = rerank(pool, q.question, fm, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
        head = base[:POOL_HEAD]
        if not head:
            per_q[qid] = {"gold_in_prompt": None, "note": "empty head"}
            continue
        reranked = rerank_with_v3(head, q.question)
        v3_ids = [str(it.get("key") or it.get("chunk_id") or "") for it in reranked]
        v3_ids = [c for c in v3_ids if c in payload_index]
        per_q[qid] = {"gold_in_prompt": bool(_gold_in_prompt(qid, q, {qid: v3_ids}, payload_index, fm))}
        n_done += 1
        if n_done % 10 == 0 or n_done == len(gate_qids):
            print(f"  {n_done}/{len(gate_qids)} scored", flush=True)

    print("[4/4] Writing results...", flush=True)
    per_model_file = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / f"ce_promotion_gate_{model_name}.json"
    out_file = POOLED_OUT if model_name == "v3" else per_model_file

    promotion_failures = [q for q in _PROMOTION if per_q.get(q, {}).get("gold_in_prompt") is not True]
    if args.qids_only:
        # The guard half of the gate needs the 61 guard qids; do not report a
        # pass from a promotion-only run.
        guard_regressions: list[str] | None = None
        gate_pass: bool | None = None
    else:
        guard_regressions = [
            q
            for q in _GUARD
            if frozen_gip.get(q) is True and per_q.get(q, {}).get("gold_in_prompt") is not True
        ]
        gate_pass = not promotion_failures and not guard_regressions

    payload = {
        "model": model_name,
        "model_path": model_path,
        "gate": {
            "pass": gate_pass,
            "promotion_total": len(_PROMOTION),
            "promotion_pass": len(_PROMOTION) - len(promotion_failures),
            "promotion_failures": promotion_failures,
            "guard_total": 0 if args.qids_only else len(_GUARD),
            "guard_baseline_true": sum(1 for q in _GUARD if frozen_gip.get(q) is True),
            "guard_regressions": guard_regressions,
        },
        "per_qid": per_q,
    }
    out_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    n_true = len(_PROMOTION) - len(promotion_failures)
    print(f"{model_name}: {n_true}/{len(_PROMOTION)} promotion qids with gold_in_prompt=True", flush=True)
    if gate_pass is None:
        print("gate: not evaluated (--qids-only skips the guard check)", flush=True)
    else:
        print(f"gate: {'PASS' if gate_pass else 'FAIL'} ({len(guard_regressions or [])} guard regressions)", flush=True)
    print(f"wrote {out_file}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
