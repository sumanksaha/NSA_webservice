"""Debug: Check gold chunk matching for first 30 questions with ce_v2."""

import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"
PROJECT_ROOT = r"C:\github\NSA_webservice"
sys.path.insert(0, PROJECT_ROOT)
from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

import torch

torch.set_num_threads(4)

from sentence_transformers import CrossEncoder

from evaluation.benchmark import load_questions
from evaluation.config import CACHE_DIR
from evaluation.rerank_legal import build_pool, rerank, rrf_scores
from evaluation.resolution import FamilyMap, matches_gold


def load_jsonl(path):
    recs = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    r = json.loads(line)
                    recs[r["question_id"]] = r
    return recs


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

raw_dir = os.path.join(PROJECT_ROOT, "evaluation/out/ceiling_v5/raw")
dense = load_jsonl(os.path.join(raw_dir, "A_dense.jsonl"))
sparse = load_jsonl(os.path.join(raw_dir, "B_sparse.jsonl"))
kg = load_jsonl(os.path.join(raw_dir, "D_kg.jsonl"))
ident = load_jsonl(os.path.join(PROJECT_ROOT, "evaluation/out/cache/v55_ident/sparse_identifier.jsonl"))

ce_v2 = CrossEncoder(os.path.join(PROJECT_ROOT, "evaluation/out/models/legal_ce_v2_K500"), max_length=256)

checked = 0
gold_in_top10 = 0
gold_found = 0

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
    rec = ident.get(qid, {})
    ids = [str(c) for c in rec.get("chunk_ids", [])[:500]]
    if ids:
        rrf = rrf_scores([
            [{"key": c} for c in d.get("chunk_ids", [])[:500]],
            [{"key": c} for c in s.get("chunk_ids", [])[:500]],
            [{"key": str(p.get("provision_id") or "")} for p in k.get("kg_provisions", [])[:500]],
            [{"key": c} for c in ids],
        ])

    base_ranked = rerank(pool, q.question, family_map, rrf, {"sec": 0.0, "act": 0.0, "exact": 0.0, "lex": 0.0})
    rrf_top150 = base_ranked[:150]

    pairs = [(q.question, str(it["payload"].get("chunk_text") or it["payload"].get("text") or "")) for it in rrf_top150]
    scores = ce_v2.predict(pairs, batch_size=64)
    scored = sorted(zip(scores, rrf_top150, strict=False), key=lambda x: float(x[0]), reverse=True)
    ce_ranked = []
    for sc, it in scored:
        item = dict(it)
        item["ce_score"] = float(sc)
        ce_ranked.append(item)

    # Find gold
    gold_ids = set()
    for unit in q.relevant_units():
        for pid, payload in payload_index.items():
            if matches_gold(payload, unit, family_map):
                gold_ids.add(pid)
                break

    top10_chunk_keys = {it["key"] for it in ce_ranked[:10] if it["kind"] == "chunk"}
    top10_all_keys = {it["key"] for it in ce_ranked[:10]}

    has_gold = bool(gold_ids & top10_chunk_keys)
    checked += 1
    if gold_ids:
        gold_found += 1
    if has_gold:
        gold_in_top10 += 1

    print(
        f"{qid}: n_gold={len(gold_ids)}, gold_in_top10={has_gold}, "
        f"n_chunk_top10={len(top10_chunk_keys)}, n_all_top10={len(top10_all_keys)}"
    )

print(f"\nSummary: checked={checked}, gold_found={gold_found}, gold_in_top10={gold_in_top10}")
print(f"Rate: {gold_in_top10}/{checked} = {gold_in_top10 / max(checked, 1):.4f}")
