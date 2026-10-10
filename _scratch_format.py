import json

o = json.load(open("evaluation/evaluator_v2_overlay.json"))
widened = o["widened_conclusions"]
# Show first entry
qid = list(widened.keys())[0]
entry = widened[qid]
print("First entry:")
print("  qid:", qid)
print("  keys:", list(entry.keys()))
print("  add (first 80 chars):", entry["add"][:80])
print("  note:", entry["note"])
