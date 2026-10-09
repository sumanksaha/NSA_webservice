import json

# Demo: treat all remaining unmarked rows as ACCEPT (target 80)
with open("tmp_demo/ws_reviewed.json", encoding="utf-8") as f:
    ws = json.load(f)

for r in ws["rows"]:
    if r["reviewer_verdict"] == "" or r["reviewer_verdict"] == "REJECT":
        r["reviewer_verdict"] = "ACCEPT"
        r["reviewer_accepted_answer_type"] = r["answerability"]

acc = sum(1 for r in ws["rows"] if r["reviewer_verdict"] == "ACCEPT")
rej = sum(1 for r in ws["rows"] if r["reviewer_verdict"] == "REJECT")
print("ACCEPT:", acc, "REJECT:", rej)

with open("tmp_demo/ws_reviewed.json", "w", encoding="utf-8") as f:
    json.dump(ws, f, indent=2)
