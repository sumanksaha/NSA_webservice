import json

with open("benchmark/stratified_eval_sample_v2_improved_v3_gold_enriched.json", encoding="utf-8") as f:
    data = json.load(f)

print("Total questions:", len(data["questions"]))
with_gold = [q for q in data["questions"] if q.get("gold_answer")]
without_gold = [q for q in data["questions"] if not q.get("gold_answer")]
print("With gold answer:", len(with_gold))
print("Missing gold answer:", len(without_gold))
print()
print("--- Missing Gold Answers ---")
for q in without_gold:
    qid = q["qid"]
    sec = q.get("section", "")
    title = q.get("title", "")
    act = q.get("act", "")
    print(f"{qid} | {act} | {sec} | {title}")
