"""TASK B — AUTO-MERGE HIGH-CONFIDENCE PAIRS (house number match)

Step 1: Isolate high-confidence subset (12,036 pairs)
Step 2: Draw 5% random sample (~600 pairs) -> spot_check_sample.csv
Step 3: STOP for human sign-off
"""

import numpy as np
import pandas as pd

np.random.seed(42)  # reproducible sample

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")

# Step 1: Isolate high-confidence pairs
hc = df_fuzzy[df_fuzzy["high_confidence"]].copy()

# Step 2: Draw 5% random sample
sample_size = max(1, int(len(hc) * 0.05))
sample = hc.sample(n=sample_size, random_state=42).copy()


# Build a clean, human-readable output
# Columns requested: both raw addresses, house numbers, similarity scores
sample["house_number_1"] = sample["house_number_1"].fillna("N/A")
sample["house_number_2"] = sample["house_number_2"].fillna("N/A")

# Write spot_check_sample.csv with formatted columns
output_cols = [
    "fbo_id_1",
    "fbo_id_2",
    "house_number_1",
    "house_number_2",
    "similarity_score",
    "raw_address_1",
    "raw_address_2",
    "block_key",
]

sample[output_cols].to_csv("spot_check_sample.csv", index=False)


# Print first 10 entries for immediate visual inspection
for _i, (_, _row) in enumerate(sample.head(10).iterrows()):
    pass
