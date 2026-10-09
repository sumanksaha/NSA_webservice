import json

with open("evaluation/out/cache/ce_v2_error_analysis.json") as f:
    d = json.load(f)

print("=== Failure Categories ===")
for cat, cnt in d["categories"].items():
    print(f"  {cat}: {cnt}")

print("\n=== Hierarchy Version Failures ===")
hier_failures = [q for q in d["per_query"] if q["type"] == "hierarchy_version"]
for q in hier_failures:
    print(f"  QID: {q['qid']}, Domain: {q['domain']}, Difficulty: {q['difficulty']}")
    print(f"    v1_rank: {q['v1_rank']}, v2_rank: {q['v2_rank']}")
    print(f"    n_candidates: {q['n_candidates']}, n_gold: {q['n_gold']}")

print("\n=== Same Section Hard Neg Failures ===")
same_sec_failures = [q for q in d["per_query"] if q["type"] == "same_section_hard_neg"]
for q in same_sec_failures:
    print(f"  QID: {q['qid']}, Domain: {q['domain']}, Difficulty: {q['difficulty']}")
    print(f"    v1_rank: {q['v1_rank']}, v2_rank: {q['v2_rank']}")
    print(f"    n_candidates: {q['n_candidates']}, n_gold: {q['n_gold']}")
