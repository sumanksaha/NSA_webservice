import json

with open("benchmark/stratified_eval_sample_v2_audit.json", encoding="utf-8") as f:
    audit = json.load(f)

with open("benchmark/gold_provisions_templates_v1.json", encoding="utf-8") as f:
    gold_data = json.load(f)

gold_questions = gold_data.get("questions", [])
gold_by_qid = {gq["qid"]: gq for gq in gold_questions}

rejected = [q for q in audit["questions"] if q.get("review_status") == "reject"]

print(f"GOLD IDENTICAL ({0}):")  # placeholder
print("=" * 80)

cat1 = 0  # gold identical
cat2 = 0  # gold different
cat3 = 0  # no gold

for item in rejected:
    qid = item["qid"]
    if qid in gold_by_qid:
        gold = gold_by_qid[qid]
        if gold.get("question", "").strip() == item["question"].strip():
            cat1 += 1
        else:
            cat2 += 1
            print("GOLD DIFFERENT [{}] {}:".format(qid, gold.get("template", "?")))
            print("  Rejected: {}".format(item["question"][:75]))
            print("  Gold:     {}".format(gold.get("question", "?")[:75]))
    else:
        cat3 += 1

print(f"Total categories: identical={cat1}, different={cat2}, no_gold={cat3}")
print()
print("GOLD IDENTICAL QUIDS:")
for item in rejected:
    qid = item["qid"]
    if qid in gold_by_qid:
        gold = gold_by_qid[qid]
        if gold.get("question", "").strip() == item["question"].strip():
            print(" ", qid)
