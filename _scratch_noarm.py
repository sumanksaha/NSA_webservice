import json

t = json.load(open("evaluation/out/ceiling_v5/full_review_tabulation.json"))
misses = [r for r in t["records"] if r.get("verdict") == "evaluator_miss"]
arms = [r for r in misses if any((r.get("machine") or {}).get(a, {}).get("v2") for a in ["C-O3", "D2", "D3", "E1"])]
no_arm = [
    r for r in misses if not any((r.get("machine") or {}).get(a, {}).get("v2") for a in ["C-O3", "D2", "D3", "E1"])
]
print(f"total_misses={len(misses)}")
print(f"has_arm={len(arms)}")
print(f"no_arm={len(no_arm)}")
print("no_arm qids:")
for r in no_arm:
    print(r["qid"])
