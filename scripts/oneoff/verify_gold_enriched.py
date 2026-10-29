import json
from collections import Counter

with open("benchmark/stratified_eval_sample_v2_improved_v3_gold_enriched.json", encoding="utf-8") as f:
    data = json.load(f)

print("Valid JSON: True")
n = len(data["questions"])
print("Total questions:", n)
print()

missing = [q["qid"] for q in data["questions"] if not q.get("gold_answer")]
print("Questions missing gold_answer:", len(missing))
if missing:
    print("Missing:", missing)

invalid = [q["qid"] for q in data["questions"] if not q.get("validity")]
print("Questions missing validity:", len(invalid))

no_source = [q["qid"] for q in data["questions"] if not q.get("gold_source")]
print("Questions missing gold_source:", len(no_source))

print()
print("--- VALIDITY SUMMARY ---")
validities = Counter(q.get("validity") for q in data["questions"])
for v, c in validities.most_common():
    print("  %s: %d" % (v, c))

print()
print("--- GOLD SOURCE SUMMARY ---")
sources = Counter(q.get("gold_source") for q in data["questions"])
for s, c in sources.most_common():
    print("  %s: %d" % (s, c))

print()
print("--- SAMPLE GOLD ANSWER (fssai:regs/contaminants) ---")
for q in data["questions"]:
    if q["qid"] == "fssai:regs/contaminants":
        print("  Q:", q["question"])
        print("  Ans:", q["gold_answer"][:200], "...")
        break

print()
print("--- SAMPLE PHASE2 GOLD ANSWER (epa:s2(e)) ---")
for q in data["questions"]:
    if q["qid"] == "epa:s2(e)":
        print("  Q:", q["question"])
        print("  Ans:", q["gold_answer"][:200], "...")
        print("  Source:", q["gold_source"], "| Validity:", q["validity"])
        break
