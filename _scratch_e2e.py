import json

o = json.load(open("evaluation/out/ceiling_v5/e2e_eval_v2.json"))
print(list(o.keys()))
records = o.get("records", o.get("pipeline_output", {}).get("records", []))
if records:
    entry = records[0]
    print("First record keys:", list(entry.keys()))
    if "metrics" in entry:
        print("metrics keys:", list(entry["metrics"].keys()))
    print("question_id:", entry.get("question_id", "N/A"))
    print("answer preview:", (entry.get("answer", "") or "")[:200])
