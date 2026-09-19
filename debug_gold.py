import json, sys, os, re
sys.path.insert(0, r'C:\github\NSA_webservice')

from dotenv import load_dotenv
load_dotenv(os.path.join(r'C:\github\NSA_webservice', '.env'), override=True)
os.environ['RAG_USE_STUB_LLM'] = 'false'

import torch
torch.set_num_threads(4)

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.rerank_legal import build_pool, rerank, rrf_scores, rank_of
from evaluation.eval_e2e_v2 import score_pool, to_retrieved_chunk
from app.rag.retrieval.result import RetrievedChunk
from sentence_transformers import CrossEncoder

# Load data
payload_index = {}
with open(CACHE_DIR / "payload_index.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rec = json.loads(line)
            payload_index[rec["id"]] = rec["payload"]

family_map = FamilyMap()
questions = {q.question_id: q for q in load_questions()}

raw_dir = r'C:\github\NSA_webservice/evaluation/out/ceiling_v5/raw'
def load_raw(arm):
    recs = {}
    p = os.path.join(raw_dir, f"{arm}.jsonl")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs

dense = load_raw("A_dense")
sparse = load_raw("B_sparse")
kg = load_raw("D_kg")

# Load CE v2
ce_v2 = CrossEncoder(r'C:\github\NSA_webservice/evaluation/out/models/legal_ce_v2_K500', max_length=256)

# Check a few questions
checked = 0
gold_found = 0
gold_in_top10 = 0
for qid in sorted(questions.keys())[:30]:
    q = questions[qid]
    d, s, k = dense.get(qid), sparse.get(qid), kg.get(qid)
    if not (d and s and k):
        continue
    pool = build_pool(d, s, k, payload_index, family_map, slice_depth=500, kg_slice=500)
    rrf = rrf_scores([
        [{"key": c} for c in d.get("chunk_ids", [])[:500]],
        [{"key": c} for c in s.get("chunk_ids", [])[:500]],
        [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
    ])
    base_ranked = rerank(pool, q.question, family_map, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
    rrf_top150 = base_ranked[:150]
    ce_ranked = score_pool(rrf_top150, q.question, ce_v2)

    # Find gold
    gold_ids = set()
    for unit in q.relevant_units():
        for pid, payload in payload_index.items():
            if matches_gold(payload, unit, family_map):
                gold_ids.add(pid)
                break

    top10_keys = {it["key"] for it in ce_ranked[:10] if it["kind"] == "chunk"}
    top10_all_keys = {it["key"] for it in ce_ranked[:10]}

    has_gold = bool(gold_ids & top10_keys)
    checked += 1
    if gold_ids:
        gold_found += 1
    if has_gold:
        gold_in_top10 += 1

    print(f"{qid}: n_gold={len(gold_ids)}, gold_in_top10={has_gold}, "
          f"top10_chunk_keys={len(top10_keys)}, top10_all_keys={len(top10_all_keys)}, "
          f"gold_in_rrfrank={any(g in {it['key'] for it in rrf_top150} for g in gold_ids)}")

print(f"\nSummary: checked={checked}, gold_found={gold_found}, gold_in_top10={gold_in_top10}")
print(f"Gold in top-10 rate: {gold_in_top10}/{checked} = {gold_in_top10/checked:.4f}")
