"""Domain distribution of the current pairwise training data (P4 sizing)."""
import collections
import json

rows = [json.loads(l) for l in open("evaluation/out/cache/pairwise_training_v2.jsonl", encoding="utf-8")]
dom = collections.Counter()
for r in rows:
    gu = str(r.get("gold_unit") or "")
    dom[gu.split(":", 1)[0] if ":" in gu else (gu or "?")] += 1
print("total:", len(rows))
for d, c in dom.most_common():
    print(f"  {d:<14} {c}")
