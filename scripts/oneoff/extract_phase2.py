import json

# Load both files
with open("benchmark/stratified_eval_sample_v2_improved_v3_gold_enriched.json", encoding="utf-8") as f:
    enriched = json.load(f)
with open("benchmark/gold_answers_phase2_residual.json", encoding="utf-8") as f:
    phase2 = json.load(f)

# Build a lookup from phase2: qid -> (gold_answer, validity, answer_type, note)
phase2_lookup = {}
for q in phase2["questions"]:
    phase2_lookup[q["qid"]] = {
        "gold_answer": q.get("gold_answer"),
        "validity": q.get("validity"),
        "answer_type": q.get("answer_type"),
        "note": q.get("note"),
        "scoring_2": q.get("answer_scoring", {}).get("2"),
        "scoring_1": q.get("answer_scoring", {}).get("1"),
        "scoring_0": q.get("answer_scoring", {}).get("0"),
        "scoring_NA": q.get("answer_scoring", {}).get("NA"),
    }

print("Phase2 qids:", sorted(phase2_lookup.keys()))
print()

# Check which enriched items are in phase2
in_phase2 = 0
not_in_phase2 = 0
missing_qids = []
for q in enriched["questions"]:
    if not q.get("gold_answer"):
        qid = q["qid"]
        if qid in phase2_lookup:
            in_phase2 += 1
            p2 = phase2_lookup[qid]
            print(f"IN PHASE2: {qid} -> validity={p2['validity']}, has_gold={p2['gold_answer'] is not None}")
        else:
            not_in_phase2 += 1
            missing_qids.append(qid)

print(f"\nTotal missing: {in_phase2 + not_in_phase2}")
print(f"Covered by phase2: {in_phase2}")
print(f"Need NEW gold answers ({not_in_phase2}):")
for qid in missing_qids:
    print(f"  {qid}")
