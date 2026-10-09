import json

# Read first few records from pairwise_training_v2.jsonl
with open("evaluation/out/cache/pairwise_training_v2.jsonl") as f:
    for i, line in enumerate(f):
        if i >= 3:
            break
        rec = json.loads(line)
        print(f"--- Record {i + 1} ---")
        print(json.dumps({k: v for k, v in rec.items() if k != "pos_text" and k != "neg_text"}, indent=2))
        break  # just first record
