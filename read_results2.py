import json

with open(r'C:\github\NSA_webservice/evaluation/out/ceiling_v5/e2e_eval_v2.json', encoding='utf-8') as f:
    data = json.load(f)

# Check the full structure
print("=== Top-level keys ===")
for k in data:
    if isinstance(data[k], dict):
        print(f"  {k}: {list(data[k].keys())}")
    elif isinstance(data[k], list):
        print(f"  {k}: list[{len(data[k])}]")
    else:
        print(f"  {k}: {type(data[k]).__name__}")

# Check failure classification per question
print("\n=== Failure Classification (ce_v2_K500, first 20) ===")
fc = data['failure_classification']
pq = fc.get('per_question', {})
# per_question is keyed by "model_qid"
for key in sorted(pq.keys())[:20]:
    entry = pq[key]
    if entry.get('model') == 'ce_v2_K500' if 'model' in entry else 'ce_v2_K500' in key:
        print(f"  {key}: stage={entry['failure_stage']} ({entry['failure_stage_name']})")
        d = entry.get('details', {})
        print(f"    gold_in_pool={d.get('gold_in_pool')}, gold_in_rrf={d.get('gold_in_rrf')}, "
              f"gold_in_ce_top10={d.get('gold_in_ce_top10')}, "
              f"answer_correctness={d.get('answer_correctness', 'N/A')}")

# Check oracle analysis
print("\n=== Oracle Analysis ===")
oa = data['oracle_analysis']
for k, v in oa.items():
    print(f"  {k}: {v}")

# Check LLM generation
print("\n=== LLM Generation Aggregate ===")
for key, m in data['llm_generation'].get('aggregate', {}).items():
    print(f"  {key}:")
    for k2, v2 in m.items():
        print(f"    {k2}: {v2}")

# Check improvement ranking
print("\n=== Improvement Ranking ===")
for item in data.get('improvement_ranking', []):
    print(f"  [{item['priority']}] {item['area']}")
    for k, v in item.items():
        if k not in ('area', 'priority'):
            print(f"    {k}: {v}")

# Check max recoverable
print("\n=== Max Recoverable ===")
mr = data.get('max_recoverable_performance', {})
for k, v in mr.items():
    print(f"  {k}: {v}")
