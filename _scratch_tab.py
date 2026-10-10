import json

t = json.load(open("evaluation/out/ceiling_v5/full_review_tabulation.json"))
misses = [r for r in t["records"] if r.get("verdict") == "evaluator_miss"]
print(f"n_misses={len(misses)}")
# show first 3
for r in misses[:3]:
    print(
        json.dumps(
            {k: v for k, v in r.items() if k in ("qid", "machine", "verdict", "answer", "model_wrong_flag")}, indent=2
        )
    )
    print("---")
