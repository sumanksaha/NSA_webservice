import json

with open("benchmark/stratified_eval_sample_v2_improved_v3_gold_enriched.json", encoding="utf-8") as f:
    data = json.load(f)

print("=== FINAL VERIFICATION ===")
print()

# Check all manual creation questions
manual = [q for q in data["questions"] if q.get("gold_source") == "manual_creation"]
print("Manual creation questions (all should have gold answers):", len(manual))
for q in manual:
    print("  QID:", q["qid"], "- Section:", q.get("section", "N/A"), "- Gold:", q.get("gold_answer", "")[:100])

print()
print("All questions have gold answers:", all(q.get("gold_answer") for q in data["questions"]))
print("All questions have gold source:", all(q.get("gold_source") for q in data["questions"]))

# Verify specific corrections
print()
print("=== SPECIFIC CORRECTIONS ===")
print("fssai:s51:", [q for q in data["questions"] if q["qid"] == "fssai:s51"][0]["gold_answer"][:150])
print("fssai:s60:", [q for q in data["questions"] if q["qid"] == "fssai:s60"][0]["gold_answer"][:150])
