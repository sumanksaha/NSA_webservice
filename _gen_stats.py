import json

b = json.load(open("benchmark/gold_provisions_templates_v1.json"))
qs = b["questions"]
none_qs = [q for q in qs if q.get("template") == "none"]
print(f"None template count: {len(none_qs)}")
for q in none_qs[:3]:
    print(f"  Q: {q['question'][:100]}...")
    print(f"  qid: {q['qid']}, section: {q['section']}, act: {q['act']}")
