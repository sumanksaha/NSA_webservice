import json

# Load the tabulation to get the 38 no-arm evaluator_miss qids
t = json.load(open("evaluation/out/ceiling_v5/full_review_tabulation.json"))
misses = [r for r in t["records"] if r.get("verdict") == "evaluator_miss"]
no_arm = [
    r for r in misses if not any((r.get("machine") or {}).get(a, {}).get("v2") for a in ["C-O3", "D2", "D3", "E1"])
]
no_arm_qids = set(r["qid"] for r in no_arm)
print("38 no-arm qids: " + str(sorted(no_arm_qids)))

# Load the existing v2 overlay
overlay = json.load(open("evaluation/evaluator_v2_overlay.json"))
widened = overlay.get("widened_conclusions", {})
print("Existing overlay entries: " + str(len(widened)))

# Check which of the 38 are already in the overlay
already_in_overlay = [q for q in no_arm_qids if q in widened]
print("Already in overlay: " + str(len(already_in_overlay)))
for q in already_in_overlay:
    add_text = widened[q].get("add", "")
    print("  " + q + ": " + ("" if not add_text else add_text[:60]) + "...")

# The remaining need overlay entries
remaining = no_arm_qids - set(already_in_overlay)
print("Remaining needing overlay: " + str(len(remaining)))
for q in sorted(remaining):
    print("  " + q)
