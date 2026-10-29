import json

with open("benchmark/gold_provisions_templates_v1.json") as f:
    templates = json.load(f)
qs = templates["questions"]

# Split by schema
with_t = [q for q in qs if q.get("template") not in (None, "none")]
with_u = [q for q in qs if q.get("template") in (None, "none")]
print("with template field:", len(with_t))
print("with None/none template:", len(with_u))

# Group by qid
from collections import defaultdict

by_qid = defaultdict(list)
for q in qs:
    by_qid[q["qid"]].append(q)

print("\nExample qid with two variants (wbpt:s46):")
for q in by_qid["wbpt:s46"]:
    print(json.dumps(q, indent=2))

# Check which qids have BOTH schema types
both = [qid for qid, items in by_qid.items() if len(items) >= 2]
print("\nQids with >=2 variants:", len(both))

# Check content of 'template_used' questions
print("\nSample of None-template questions (first 3):")
for q in with_u[:3]:
    print(json.dumps(q, indent=2))

# Check question text similarity between the two variants of same qid
print("\nComparing question text per qid pair:")
for qid in both[:5]:
    a, b = sorted(by_qid[qid], key=lambda q: 0 if q.get("template") else 1)
    print(f"{qid}:")
    print("  v1:", a["question"])
    print("  v2:", b["question"])
    print("  same text:", a["question"] == b["question"])
