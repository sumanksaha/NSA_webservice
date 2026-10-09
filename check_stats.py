import json

# Check current training pairs stats
with open("evaluation/out/cache/ce_training_stats.json") as f:
    d = json.load(f)

print("=== Current CE Training Stats ===")
print("Questions:", d["questions"])
print("Questions with positives:", d["questions_with_positives"])
print("Total positive pairs:", d["total_positive_pairs"])
print("Total negative pairs:", d["total_negative_pairs"])
print("Ratio:", d["ratio"])
print("Notes:", d["notes"])
