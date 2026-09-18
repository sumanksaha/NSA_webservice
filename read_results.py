import json

with open(r'C:\github\NSA_webservice/evaluation/out/ceiling_v5/e2e_eval_v2.json', encoding='utf-8') as f:
    data = json.load(f)

# Check retrieval diagnostics
print("=== Retrieval Diagnostics ===")
for mname, m in data['retrieval_diagnostics'].items():
    print(f"{mname}: R@1={m['R@1']}, R@20={m['R@20']}, R@50={m['R@50']}, "
          f"R@100={m['R@100']}, MRR={m['MRR']}, NDCG@10={m['NDCG@10']}")

# Check LLM metrics
print("\n=== LLM Metrics ===")
for key, m in data['llm_generation']['aggregate'].items():
    print(f"{key}: n={m.get('n')}, ans_correct={m.get('answer_correctness_avg')}, "
          f"ctx_rec={m.get('context_recall_at_10')}, cite_rec={m.get('citation_recall')}, "
          f"grounded={m.get('groundedness')}, abstain_correct={m.get('abstain_correct')}/{m.get('n_abstain')}")

# Check failure distribution
print("\n=== Failure Stage Distribution ===")
for mname, s in data['failure_classification']['stage_summary'].items():
    print(f"{mname}: total={s['total']}")
    for stage, pct in s['percentages'].items():
        print(f"  {stage}: {pct}%")

# Check oracle analysis
print("\n=== Oracle Analysis ===")
oa = data['oracle_analysis']
print(f"gold_in_pool_rate: {oa.get('gold_in_pool_rate')}")
for mname, g in oa.get('retrieved_context_recall', {}).items():
    print(f"  {mname} ctx_recall@10: {g}")
print(f"pool_size_avg: {oa.get('pool_size_avg')}")

# Check a few per-question LLM results
print("\n=== Sample LLM Results (first 5 per model/mode) ===")
for mname in ("ce_v1", "ce_v2_K500"):
    retrieved = data['llm_generation'].get('aggregate', {})
    # Check if per-question results are in the file
    # They might not be stored directly

# Check structure of llm_generation
print("\n=== LLM Generation Structure ===")
print(f"Keys: {list(data['llm_generation'].keys())}")
lg = data['llm_generation']
if 'per_question' in lg:
    print(f"per_question entries: {len(lg['per_question'])}")
    for entry in lg['per_question'][:3]:
        print(f"  {entry['question_id']}_{entry['model']}_{entry['mode']}: "
              f"ans_correct={entry['metrics']['answer_correctness']}, "
              f"ctx_rec={entry['metrics']['context_recall_at_10']}, "
              f"answer_len={entry['metrics']['answer_length']}")
else:
    print("No per_question data in llm_generation")
    for key in lg:
        print(f"  {key}: {type(lg[key])}")

# Check improvement ranking
print("\n=== Improvement Ranking ===")
for item in data.get('improvement_ranking', []):
    print(f"  {item['priority']}: {item['area']}")
    print(f"    failure_pct={item.get('failure_pct')}, current={item.get('current_R@20', item.get('current_recall10', item.get('current_recall', 'N/A')))}")
