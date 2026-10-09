import json

with open("benchmark/stratified_eval_sample_v2_improved_v3_gold_enriched.json", encoding="utf-8") as f:
    data = json.load(f)

print("=== Checking all FSSAI and other manual gold answers ===")
print()

# Check all questions with gold_source = manual_creation
manual = [q for q in data["questions"] if q.get("gold_source") == "manual_creation"]
for q in manual:
    print("QID:", q["qid"])
    print("  Section:", q.get("section", ""))
    print("  Gold Answer:", q.get("gold_answer", "")[:150], "...")
    print("  Validity:", q.get("validity", ""))
    print()

# Also check for any potential issues
print("=== Checking all questions with section field ===")
for q in data["questions"]:
    section = q.get("section", "")
    gold = q.get("gold_answer", "")
    if section and gold:
        # Check if answer contains section reference
        contains_section = section in gold or "Section " + section in gold
        if not contains_section:
            print("Potential mismatch:", q["qid"], "- Section:", section, "- Answer:", gold[:100])
