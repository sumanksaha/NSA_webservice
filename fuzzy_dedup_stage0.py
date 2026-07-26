#!/usr/bin/env python3
"""
FBO Geocoding Pipeline - Stage 0: Fuzzy Deduplication
Using rapidfuzz for efficient batch fuzzy matching
"""

import re
import time
import warnings
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

warnings.filterwarnings("ignore")

# ============================================================================
# STEP 1: INSTALL & VERIFY
# ============================================================================
print("=" * 80)
print("STEP 1: INSTALL & VERIFY")
print("=" * 80)

try:
    import rapidfuzz

    print("[OK] rapidfuzz version:", rapidfuzz.__version__)
except ImportError:
    print("[FAIL] rapidfuzz not installed, installing...")
    import subprocess

    subprocess.run(["pip", "install", "rapidfuzz"], check=True)
    import rapidfuzz

    print("[OK] rapidfuzz installed:", rapidfuzz.__version__)

# ============================================================================
# STEP 2: LOAD DATA & VERIFY BLOCKING FIELD COVERAGE
# ============================================================================
print("\n" + "=" * 80)
print("STEP 2: LOAD DATA & VERIFY BLOCKING FIELD COVERAGE")
print("=" * 80)

# Load the exact-match deduped table
df = pd.read_csv("extracted_with_exact_groups.csv")
print("Total records in deduped-so-far table:", f"{len(df):,}")

# Filter to records NOT already grouped (single-record groups only)
# Groups with multiple records are already deduplicated by exact match
group_counts = df["dedup_group_id"].value_counts()
single_record_groups = group_counts[group_counts == 1].index.tolist()
df_single = df[df["dedup_group_id"].isin(single_record_groups)].copy()

print("Records NOT already grouped (single-record groups):", f"{len(df_single):,}")
print(
    "  - Records in multi-record groups (already deduplicated):",
    f"{len(df) - len(df_single):,}",
)


# Extract PIN and Ward from raw_address
def extract_pin(address: str) -> Optional[str]:
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


def extract_ward(address: str) -> Optional[str]:
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

print("\nBlocking Field Coverage:")
print(f"  Total records: {total_records:,}")
print(f"  Records with PIN: {with_pin:,} ({100 * with_pin / total_records:.1f}%)")
print(f"  Records with WARD: {with_ward:,} ({100 * with_ward / total_records:.1f}%)")
print(f"  Records with BOTH: {with_both:,} ({100 * with_both / total_records:.1f}%)")
print(
    f"  Records with NEITHER: {with_neither:,} ({100 * with_neither / total_records:.1f}%)"
)

# CRITICAL CHECK: If >10% lack both PIN and ward, STOP
coverage_gap_pct = 100 * with_neither / total_records if total_records > 0 else 0
if coverage_gap_pct > 10:
    print(f"\n[STOPPING] {coverage_gap_pct:.1f}% of records lack both PIN and ward")
    print("Proposed fallback blocking key: first 2 words of street name + locality")
    print("Waiting for confirmation before proceeding with fallback...")
    df_single = df_single[df_single["pin"].notna() | df_single["ward"].notna()].copy()
    print(f"Proceeding with {len(df_single):,} records that have at least PIN or ward")

# ============================================================================
# STEP 3: BLOCK
# ============================================================================
print("\n" + "=" * 80)
print("STEP 3: BLOCK")
print("=" * 80)


# Create blocking key: PIN preferred, ward as secondary
def create_blocking_key(row: pd.Series) -> Optional[str]:
    pin_val = row.get("pin")
    ward_val = row.get("ward")
    if pd.notna(pin_val) and pin_val:
        return f"pin_{pin_val}"
    elif pd.notna(ward_val) and ward_val:
        return f"ward_{ward_val}"
    return None


df_single["block_key"] = df_single.apply(create_blocking_key, axis=1)

# Group by block key
blocks = (
    df_single.groupby("block_key")
    .agg({"fbo_id": list, "raw_address": list, "pin": "first", "ward": "first"})
    .reset_index()
)

# Filter out None block keys
blocks = blocks[blocks["block_key"].notna()].reset_index(drop=True)

print(f"Number of blocks: {len(blocks):,}")

# Calculate block size distribution
block_sizes_list = [len(ids) for ids in blocks["fbo_id"]]
block_sizes = np.array(block_sizes_list, dtype=float)

print("\nBlock size distribution:")
print(f"  Min: {int(block_sizes.min()):,}")
print(f"  Max: {int(block_sizes.max()):,}")
print(f"  Median: {float(np.median(block_sizes)):.0f}")
print(f"  Mean: {float(np.mean(block_sizes)):.2f}")

# Flag blocks over 2,000 records
large_count = sum(1 for s in block_sizes_list if s > 2000)
print(f"\n[WARNING] Blocks over 2,000 records: {large_count:,}")
if large_count > 0:
    print("  These may need secondary split for performance")

# ============================================================================
# STEP 4: FUZZY COMPARE - BATCHED
# ============================================================================
print("\n" + "=" * 80)
print("STEP 4: FUZZY COMPARE - BATCHED")
print("=" * 80)

# Prepare results storage
fuzzy_candidates: List[Dict[str, Any]] = []

# Track progress
total_comparisons = 0
total_candidate_pairs = 0
start_time = time.time()
progress_interval = 10

# Sort blocks by size (largest first for better progress visibility)
blocks_sorted = blocks.sort_values(
    "block_key", key=lambda x: [len(ids) for ids in x], ascending=False
)
total_blocks = len(blocks_sorted)

print(f"Processing {total_blocks:,} blocks...")
print("Using rapidfuzz.process.cdist for batched similarity computation")

blocks_processed = 0

for block_idx, (_, block) in enumerate(blocks_sorted.iterrows()):
    fbo_ids: List[str] = block["fbo_id"]
    addresses: List[str] = block["raw_address"]
    n = len(fbo_ids)

    if n < 2:
        blocks_processed += 1
        continue

    # Batched cdist call - compute full similarity matrix at once
    similarity_matrix = process.cdist(
        addresses, addresses, scorer=fuzz.token_sort_ratio
    )

    # Extract upper triangle pairs (i < j, excluding self-matches) with score >= 90
    for i in range(n):
        for j in range(i + 1, n):
            score = similarity_matrix[i, j]
            if score >= 90:
                fuzzy_candidates.append(
                    {
                        "fbo_id_1": fbo_ids[i],
                        "fbo_id_2": fbo_ids[j],
                        "raw_address_1": addresses[i],
                        "raw_address_2": addresses[j],
                        "similarity_score": round(float(score), 2),
                        "block_key": block["block_key"],
                    }
                )
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
                print(
                    f"\n[PROJECTION ALERT] Estimated runtime {projected_total / 60:.1f} minutes exceeds 20-minute threshold"
                )
                print("Stopping early. Results so far saved.")
                break

        print(
            f"  Block {blocks_processed:,}/{total_blocks:,} | "
            f"Cumulative pairs: {total_candidate_pairs:,} | "
            f"Elapsed: {elapsed:.1f}s | "
            f"Rate: {total_comparisons / max(elapsed, 0.01):,.0f} comparisons/sec"
        )

# ============================================================================
# STEP 5: CHECKPOINT
# ============================================================================
print("\n" + "=" * 80)
print("STEP 5: CHECKPOINT")
print("=" * 80)

end_time = time.time()
elapsed = end_time - start_time

# Create DataFrame from candidates
df_candidates = pd.DataFrame(fuzzy_candidates)

# Save to CSV
output_file = "fuzzy_candidates.csv"
df_candidates.to_csv(output_file, index=False)

print("\nCheckpoint Results:")
print(f"  Total blocks processed: {blocks_processed:,}")
print(f"  Total comparisons performed: {total_comparisons:,}")
print(f"  Total candidate pairs (score >= 90): {total_candidate_pairs:,}")
print(f"  Total elapsed time: {elapsed:.2f} seconds")
print(f"  Comparisons/sec achieved: {total_comparisons / max(elapsed, 0.01):,.2f}")
print(f"\n[OK] Results saved to: {output_file}")

if total_candidate_pairs > 0:
    print("\nSample fuzzy matches (first 10):")
    print(df_candidates.head(10).to_string())
else:
    print("\nNo fuzzy matches found (score >= 90)")

print("\n" + "=" * 80)
print("STAGE 0 COMPLETE - STOPPING as per instructions")
print("Fuzzy candidates ready for human review before merging")
print("=" * 80)
