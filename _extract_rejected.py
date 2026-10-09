import json

with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

rejected = [q for q in audit["questions"] if q.get("review_status") == "reject"]
print(f"Total rejected: {len(rejected)}")
print()
for r in rejected:
    print(f"qid: {r['qid']}")
    print(f"section: {r['section']}")
    print(f"act: {r['act']}")
    print(f"template: {r['template']}")
    print(f"reason: {r['review_reason']}")
    print()
    if "recommended_question" in r:
        print(f"recommended: {r['recommended_question']}")
    print("---" * 20)
    print()
