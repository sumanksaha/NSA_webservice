import json

d = json.load(open("evaluation/out/aggregate_metrics.json"))
best = max((v.get("recall@10", 0) for k, v in d["retrieval"].items()), default=0)
print("Best recall@10:", best)
for arm, data in d["retrieval"].items():
    print(f"{arm}: recall@10={data.get('recall@10', 0):.4f}")
