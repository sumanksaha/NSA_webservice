import json

with open(r'C:\github\NSA_webservice/evaluation/out/ceiling_v5/e2e_eval_v2.json', encoding='utf-8') as f:
    data = json.load(f)

# Check per-question failure classification
fc = data['failure_classification']
pq = fc.get('per_question', {})
print(f"Per-question entries: {len(pq)}")
print(f"Keys (first 10): {list(pq.keys())[:10]}")

# Count failures by stage for ce_v2_K500
from collections import Counter
ce_v1_stages = Counter()
ce_v2_stages = Counter()
for key, entry in pq.items():
    if key.startswith("ce_v1_"):
        ce_v1_stages[entry["failure_stage_name"]] += 1
    elif key.startswith("ce_v2_"):
        ce_v2_stages[entry["failure_stage_name"]] += 1

print(f"\nce_v1 failure stages: {dict(ce_v1_stages)}")
print(f"ce_v2_K500 failure stages: {dict(ce_v2_stages)}")

# Check a few ce_v2_K500 entries
print("\n=== ce_v2_K500 per-question (first 10) ===")
for key in sorted(pq.keys()):
    if key.startswith("ce_v2_K500_"):
        entry = pq[key]
        d = entry.get('details', {})
        print(f"  {entry['question_id']}: stage={entry['failure_stage']} ({entry['failure_stage_name']})")
        print(f"    gold_in_pool={d.get('gold_in_pool')}, gold_in_rrf={d.get('gold_in_rrf')}, "
              f"gold_in_ce_top10={d.get('gold_in_ce_top10')}, ans_correct={d.get('answer_correctness', 'N/A')}")

# Check: for ce_v2_K500, how many have gold_in_ce_top10=True?
gold_in_top10 = sum(1 for k, v in pq.items() if k.startswith("ce_v2_K500_") and v.get('details', {}).get('gold_in_ce_top10', False))
total = sum(1 for k in pq if k.startswith("ce_v2_K500_"))
print(f"\nce_v2_K500: gold_in_ce_top10=True for {gold_in_top10}/{total} ({gold_in_top10/total*100:.1f}%)")
gold_in_top10_v1 = sum(1 for k, v in pq.items() if k.startswith("ce_v1_") and v.get('details', {}).get('gold_in_ce_top10', False))
print(f"ce_v1: gold_in_ce_top10=True for {gold_in_top10_v1}/{total} ({gold_in_top10_v1/total*100:.1f}%)")
