#!/usr/bin/env python3
"""FBO Geocoding Pipeline - Stage 0: Fuzzy Deduplication
Using rapidfuzz for efficient batch fuzzy matching
"""

import re
import time
import warnings
from typing import Any

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

warnings.filterwarnings("ignore")

# ============================================================================
# STEP 1: INSTALL & VERIFY
# ============================================================================

try:
    import rapidfuzz

except ImportError:
    import subprocess

    subprocess.run(["pip", "install", "rapidfuzz"], check=True)


# ============================================================================
# STEP 2: LOAD DATA & VERIFY BLOCKING FIELD COVERAGE
# ============================================================================

# Load the exact-match deduped table
df = pd.read_csv("extracted_with_exact_groups.csv")

# Filter to records NOT already grouped (single-record groups only)
# Groups with multiple records are already deduplicated by exact match
group_counts = df["dedup_group_id"].value_counts()
single_record_groups = group_counts[group_counts == 1].index.tolist()
df_single = df[df["dedup_group_id"].isin(single_record_groups)].copy()


# Extract PIN and Ward from raw_address
def extract_pin(address: str) -> str | None:
    """Extract 6-digit PIN from address"""
    if pd.isna(address):
        return None
    match = re.search(r"KOL-(\d{6})", str(address).upper())
    if match:
        return match.group(1)
    match = re.search(r"(\d{6})", str(address))
    if match:
        return match.group(1)
    return None


def extract_ward(address: str) -> str | None:
    """Extract ward number from address"""
    if pd.isna(address):
        return None
    patterns = [
        r"WARD\s*(?:NO\.?|NUMBER)?\s*(\d+)",
        r"WD\s*(\d+)",
        r"WORD\s*No\s*(\d+)",
        r"WARD\s*-\s*(\d+)",
    ]
    address_upper = str(address).upper()
    for pattern in patterns:
        match = re.search(pattern, address_upper)
        if match:
            return match.group(1).lstrip("0")
    return None


# Extract PIN and Ward
df_single["pin"] = df_single["raw_address"].apply(extract_pin)
df_single["ward"] = df_single["raw_address"].apply(extract_ward)

# Calculate coverage
total_records = len(df_single)
with_pin = int(df_single["pin"].notna().sum())
with_ward = int(df_single["ward"].notna().sum())
both_mask = df_single["pin"].notna() & df_single["ward"].notna()
with_both = int(both_mask.sum())
neither_mask = df_single["pin"].isna() & df_single["ward"].isna()
with_neither = int(neither_mask.sum())


# CRITICAL CHECK: If >10% lack both PIN and ward, STOP
coverage_gap_pct = 100 * with_neither / total_records if total_records > 0 else 0
if coverage_gap_pct > 10:
    df_single = df_single[df_single["pin"].notna() | df_single["ward"].notna()].copy()

# ============================================================================
# STEP 3: BLOCK
# ============================================================================


# Create blocking key: PIN preferred, ward as secondary
def create_blocking_key(row: pd.Series) -> str | None:
    pin_val = row.get("pin")
    ward_val = row.get("ward")
    if pd.notna(pin_val) and pin_val:
        return f"pin_{pin_val}"
    if pd.notna(ward_val) and ward_val:
        return f"ward_{ward_val}"
    return None


df_single["block_key"] = df_single.apply(create_blocking_key, axis=1)

# Group by block key
blocks = (
    df_single
    .groupby("block_key")
    .agg({"fbo_id": list, "raw_address": list, "pin": "first", "ward": "first"})
    .reset_index()
)

# Filter out None block keys
blocks = blocks[blocks["block_key"].notna()].reset_index(drop=True)


# Calculate block size distribution
block_sizes_list = [len(ids) for ids in blocks["fbo_id"]]
block_sizes = np.array(block_sizes_list, dtype=float)


# Flag blocks over 2,000 records
large_count = sum(1 for s in block_sizes_list if s > 2000)
if large_count > 0:
    pass

# ============================================================================
# STEP 4: FUZZY COMPARE - BATCHED
# ============================================================================

# Prepare results storage
fuzzy_candidates: list[dict[str, Any]] = []

# Track progress
total_comparisons = 0
total_candidate_pairs = 0
start_time = time.time()
progress_interval = 10

# Sort blocks by size (largest first for better progress visibility)
blocks_sorted = blocks.sort_values("block_key", key=lambda x: [len(ids) for ids in x], ascending=False)
total_blocks = len(blocks_sorted)


blocks_processed = 0

for _block_idx, (_, block) in enumerate(blocks_sorted.iterrows()):
    fbo_ids: list[str] = block["fbo_id"]
    addresses: list[str] = block["raw_address"]
    n = len(fbo_ids)

    if n < 2:
        blocks_processed += 1
        continue

    # Batched cdist call - compute full similarity matrix at once
    similarity_matrix = process.cdist(addresses, addresses, scorer=fuzz.token_sort_ratio)

    # Extract upper triangle pairs (i < j, excluding self-matches) with score >= 90
    for i in range(n):
        for j in range(i + 1, n):
            score = similarity_matrix[i, j]
            if score >= 90:
                fuzzy_candidates.append({
                    "fbo_id_1": fbo_ids[i],
                    "fbo_id_2": fbo_ids[j],
                    "raw_address_1": addresses[i],
                    "raw_address_2": addresses[j],
                    "similarity_score": round(float(score), 2),
                    "block_key": block["block_key"],
                })
                total_candidate_pairs += 1

    # Count comparisons (n*(n-1)/2 for each block)
    total_comparisons += n * (n - 1) // 2
    blocks_processed += 1

    # Progress report every 10 blocks
    if blocks_processed % progress_interval == 0 or blocks_processed == total_blocks:
        elapsed = time.time() - start_time

        # Project total time if needed
        if blocks_processed >= 10:
            projected_total = elapsed * total_blocks / blocks_processed
            if projected_total > 1200:  # 20 minutes
                break


# ============================================================================
# STEP 5: CHECKPOINT
# ============================================================================

end_time = time.time()
elapsed = end_time - start_time

# Create DataFrame from candidates
df_candidates = pd.DataFrame(fuzzy_candidates)

# Save to CSV
output_file = "fuzzy_candidates.csv"
df_candidates.to_csv(output_file, index=False)


if total_candidate_pairs > 0:
    pass
else:
    pass
