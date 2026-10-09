"""Create full gold answers for 11 provisions and populate enriched file."""

import json
import shutil

# Load data files
from gold_answer_data_part1 import NEW_GOLD_ANSWERS_PART1
from gold_answer_data_part2 import NEW_GOLD_ANSWERS_PART2

NEW_GOLD_ANSWERS = {**NEW_GOLD_ANSWERS_PART1, **NEW_GOLD_ANSWERS_PART2}

ENRICHED_PATH = "benchmark/stratified_eval_sample_v2_improved_v3_gold_enriched.json"

with open(ENRICHED_PATH, encoding="utf-8") as f:
    enriched = json.load(f)

# Also copy phase2 gold answers for the 17 overlapping questions
with open("benchmark/gold_answers_phase2_residual.json", encoding="utf-8") as f:
    phase2 = json.load(f)

phase2_lookup = {}
for q in phase2["questions"]:
    phase2_lookup[q["qid"]] = {
        "gold_answer": q.get("gold_answer"),
        "validity": q.get("validity"),
        "answer_type": q.get("answer_type"),
        "note": q.get("note"),
        "scoring": q.get("answer_scoring", {}),
    }

# Apply answers
for q in enriched["questions"]:
    qid = q["qid"]
    if q.get("gold_answer") is not None:
        continue  # Already has gold answer

    if qid in phase2_lookup:
        p2 = phase2_lookup[qid]
        q["gold_answer"] = p2["gold_answer"]
        q["validity"] = p2["validity"]
        q["answer_type"] = p2["answer_type"]
        q["note"] = p2["note"]
        q["answer_scoring"] = p2["scoring"]
        q["gold_source"] = "phase2_residual"
        print(f"[PHASE2] {qid}")
    elif qid in NEW_GOLD_ANSWERS:
        gold_data = NEW_GOLD_ANSWERS[qid]
        q["gold_answer"] = gold_data["gold_answer"]
        q["validity"] = gold_data["validity"]
        q["answer_type"] = "definition"
        q["note"] = f"Provision-level gold answer for {q.get('title', 'unknown')}."
        q["answer_scoring"] = {
            "2": "Legally correct answer that directly addresses the cited provision.",
            "1": "Substantially correct but incomplete or minor wording/qualification issue.",
            "0": "Incorrect answer or unsupported legal assertion.",
            "NA": "Use only if later source verification establishes that the question itself is invalid.",
        }
        q["gold_source"] = "manual_creation"
        print(f"[NEW] {qid}")

# Save backup and updated file
shutil.copy2(ENRICHED_PATH, ENRICHED_PATH + ".backup")
with open(ENRICHED_PATH, "w", encoding="utf-8") as f:
    json.dump(enriched, f, indent=2, ensure_ascii=False)

# Verify
print("\n--- VERIFICATION ---")
print(f"Total questions: {len(enriched['questions'])}")
with_gold = sum(1 for q in enriched["questions"] if q.get("gold_answer"))
without_gold = sum(1 for q in enriched["questions"] if not q.get("gold_answer"))
print(f"With gold answer: {with_gold}")
print(f"Without gold answer: {without_gold}")
