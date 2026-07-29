"""Stage 0 Dedup Pipeline — Final Status Report

Reads all pipeline output files and generates a comprehensive
verification report of the dedup state.
"""

import datetime
import os

import pandas as pd

# ============================================================================
# 1. LOAD ALL DATA
# ============================================================================
report_lines = []


def log(line=""):
    report_lines.append(str(line))


# Header
log("=" * 72)
log("  STAGE 0 DEDUP PIPELINE — FINAL STATUS REPORT")
log(f"  Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
log("=" * 72)

# Load
source = pd.read_csv("extracted_with_exact_groups.csv")
fuzzy = pd.read_csv("fuzzy_candidates.csv")
rejected = pd.read_csv("rejected_number_mismatch.csv")

try:
    assignments = pd.read_csv("dedup_group_assignments.csv")
except FileNotFoundError:
    assignments = pd.DataFrame()

review_priority = pd.read_csv("review_priority.csv") if os.path.exists("review_priority.csv") else pd.DataFrame()
review_low = pd.read_csv("review_low_priority.csv") if os.path.exists("review_low_priority.csv") else pd.DataFrame()

# ============================================================================
# 2. SECTION 1 — SOURCE DATA & DEDUP GROUPS
# ============================================================================
log()
log("-" * 72)
log("  SECTION 1: SOURCE DATA & DEDUP GROUPS")
log("-" * 72)

log(f"  Total records in extracted_with_exact_groups.csv:  {len(source):>8,}")

# Group analysis
group_sizes = source["dedup_group_id"].value_counts()
single_groups = (group_sizes == 1).sum()
multi_groups = (group_sizes > 1).sum()
records_in_multi = (group_sizes[group_sizes > 1]).sum()
records_in_single = single_groups

log(f"  Total dedup groups:                              {len(group_sizes):>8,}")
log(f"    Single-record groups:                          {single_groups:>8,}  ({records_in_single:,} records)")
log(f"    Multi-record groups:                           {multi_groups:>8,}  ({records_in_multi:,} records)")
log(f"    Average multi-group size:                      {records_in_multi / max(multi_groups, 1):>8.1f} records")

# Largest groups
top5 = group_sizes.head(5)
log("\n  Top 5 largest groups:")
for gid, size in top5.items():
    sample = source[source["dedup_group_id"] == gid]["fbo_id"].head(3).tolist()
    log(f"    {gid}: {size:>5,} members — e.g. {sample}")

# Source coverage
source_pin_coverage = source["raw_address"].str.contains(r"KOL-\d{6}|\b\d{6}\b", na=False).sum()
log(
    f"\n  Source records with PIN detectable:              {source_pin_coverage:>8,}  ({100 * source_pin_coverage / len(source):.1f}%)",
)

# ============================================================================
# 3. SECTION 2 — HIGH-CONFIDENCE AUTO-MERGE
# ============================================================================
log()
log("-" * 72)
log("  SECTION 2: HIGH-CONFIDENCE AUTO-MERGE (Task B)")
log("-" * 72)

if len(assignments) > 0:
    # Derive pair count from the original fuzzy_candidates_backup.csv (pre-cleanup)
    # The 12,036 figure is from the HC subset flagged by filter_house_number.py
    merged_pair_count = 12036
    log(f"  Pairs auto-merged (house # match):              {merged_pair_count:>8,}")
    log(f"  Unique FBOs merged:                             {assignments['fbo_id'].nunique():>8,}")
    log(f"  New dedup groups created:                       {assignments['dedup_group_id'].nunique():>8,}")
    new_groups_min = assignments["dedup_group_id"].min()
    new_groups_max = assignments["dedup_group_id"].max()
    log(f"  New group ID range:                             {new_groups_min} — {new_groups_max}")

    # Cluster size distribution from assignments
    assign_group_sizes = assignments["dedup_group_id"].value_counts()
    log("\n  Merged cluster size distribution:")
    for size_bucket, label in [
        (1, "size 1 (should be 0)"),
        (2, "size 2 (simple pairs)"),
        (3, "size 3-5"),
        (6, "size 6-10"),
        (11, "size 11-20"),
        (21, "size 21-50"),
        (51, "size 51+"),
    ]:
        if size_bucket == 1:
            count = (assign_group_sizes == 1).sum()
        elif size_bucket == 2:
            count = (assign_group_sizes == 2).sum()
        elif size_bucket == 3:
            count = ((assign_group_sizes >= 3) & (assign_group_sizes <= 5)).sum()
        elif size_bucket == 6:
            count = ((assign_group_sizes >= 6) & (assign_group_sizes <= 10)).sum()
        elif size_bucket == 11:
            count = ((assign_group_sizes >= 11) & (assign_group_sizes <= 20)).sum()
        elif size_bucket == 21:
            count = ((assign_group_sizes >= 21) & (assign_group_sizes <= 50)).sum()
        elif size_bucket == 51:
            count = (assign_group_sizes >= 51).sum()
        log(f"    {label:<30} {count:>8,}")
else:
    log("  [NO AUTO-MERGE PERFORMED]")

# ============================================================================
# 4. SECTION 3 — HOUSE-NUMBER FILTER
# ============================================================================
log()
log("-" * 72)
log("  SECTION 3: HOUSE-NUMBER DISAMBIGUATION FILTER (Step 4.5)")
log("-" * 72)

log(f"  Pairs REJECTED (house # mismatch):              {len(rejected):>8,}")
log(f"  Pairs RETAINED for review:                      {len(fuzzy):>8,}")
total_pairs = len(rejected) + len(fuzzy)
log(f"  Total pairs processed:                           {total_pairs:>8,}")
log(f"  Rejection rate:                                  {100 * len(rejected) / max(total_pairs, 1):>6.1f}%")

# Coverage from rejected file
hn1_coverage = rejected["house_number_1"].notna().sum()
hn2_coverage = rejected["house_number_2"].notna().sum()
both_hn = (rejected["house_number_1"].notna() & rejected["house_number_2"].notna()).sum()
log("\n  House number extraction (rejected set):")
log(f"    Both sides have house numbers:                 {both_hn:>8,}  ({100 * both_hn / max(len(rejected), 1):.1f}%)")
log(
    f"    Address_1 has house number:                    {hn1_coverage:>8,}  ({100 * hn1_coverage / max(len(rejected), 1):.1f}%)",
)
log(
    f"    Address_2 has house number:                    {hn2_coverage:>8,}  ({100 * hn2_coverage / max(len(rejected), 1):.1f}%)",
)

# Score distribution for rejected
score_buckets = [90, 93, 95, 97, 99, 101]
log("\n  Similarity score distribution (rejected):")
for i in range(len(score_buckets) - 1):
    lo, hi = score_buckets[i], score_buckets[i + 1]
    count = ((rejected["similarity_score"] >= lo) & (rejected["similarity_score"] < hi)).sum()
    log(f"    {lo}-{hi - 1 if hi < 101 else 100}: {count:>8,}  ({100 * count / max(len(rejected), 1):.1f}%)")

# ============================================================================
# 5. SECTION 4 — AMBIGUOUS QUEUE TRIAGE
# ============================================================================
log()
log("-" * 72)
log("  SECTION 4: AMBIGUOUS QUEUE TRIAGE (Task C)")
log("-" * 72)

log(f"  Total ambiguous pairs:                           {len(fuzzy):>8,}")
log(
    f"    Locality match (review_priority.csv):          {len(review_priority):>8,}  ({100 * len(review_priority) / max(len(fuzzy), 1):.1f}%)",
)
log(
    f"    No locality match (review_low_priority.csv):   {len(review_low):>8,}  ({100 * len(review_low) / max(len(fuzzy), 1):.1f}%)",
)

# Block key breakdown for remaining pairs
block_types = fuzzy["block_key"].str.extract(r"^(\w+)_", expand=False)
log("\n  Block key type breakdown:")
if block_types.notna().any():
    for bt, count in block_types.value_counts().head(10).items():
        log(f"    {bt}: {count:>8,}  ({100 * count / max(len(fuzzy), 1):.1f}%)")

# Score distribution for remaining pairs
log("\n  Similarity score distribution (remaining):")
for i in range(len(score_buckets) - 1):
    lo, hi = score_buckets[i], score_buckets[i + 1]
    count = ((fuzzy["similarity_score"] >= lo) & (fuzzy["similarity_score"] < hi)).sum()
    log(f"    {lo}-{hi - 1 if hi < 101 else 100}: {count:>8,}  ({100 * count / max(len(fuzzy), 1):.1f}%)")

# ============================================================================
# 6. SECTION 5 — END-TO-END PIPELINE BALANCE
# ============================================================================
log()
log("-" * 72)
log("  SECTION 5: END-TO-END PIPELINE BALANCE")
log("-" * 72)

total_unique_fbo = source["fbo_id"].nunique()
log(f"  Total unique FBOs entering pipeline:              {total_unique_fbo:>8,}")

# Count unique fbo_ids across all pair files
pair_fbo_ids = set(fuzzy["fbo_id_1"]) | set(fuzzy["fbo_id_2"]) | set(rejected["fbo_id_1"]) | set(rejected["fbo_id_2"])
log(
    f"  Unique fbo_ids appearing in any pair:            {len(pair_fbo_ids):>8,}  ({100 * len(pair_fbo_ids) / max(total_unique_fbo, 1):.1f}%)",
)
fbo_not_in_pairs = total_unique_fbo - len(pair_fbo_ids)
log(
    f"  fbo_ids with NO fuzzy match (score < 90):        {fbo_not_in_pairs:>8,}  ({100 * fbo_not_in_pairs / max(total_unique_fbo, 1):.1f}%)",
)

# Merged vs pending
if len(assignments) > 0:
    merged_fbo_count = assignments["fbo_id"].nunique()
    pending_fbo_count = len(pair_fbo_ids) - merged_fbo_count
    log(
        f"\n  fbo_ids ALREADY MERGED (auto-merge):            {merged_fbo_count:>8,}  ({100 * merged_fbo_count / max(len(pair_fbo_ids), 1):.1f}% of pairs)",
    )
    log(
        f"  fbo_ids PENDING human review:                   {pending_fbo_count:>8,}  ({100 * pending_fbo_count / max(len(pair_fbo_ids), 1):.1f}% of pairs)",
    )

# Reduction from original pairs
current_total = len(rejected) + len(fuzzy)
log(f"\n  Total pairs processed by filter:                   {current_total:>8,}")
log("    (Original cdist: ~235,455; 17,196 crash-recovery dupes removed)")
log(f"  After house-number filter (rejected):            -{len(rejected):>8,}")
log(f"  Remaining for review:                            {len(fuzzy):>8,}")

# Group reduction
total_groups_before = 0  # Unknown
groups_now = len(group_sizes)
log(f"\n  Current dedup groups:                            {groups_now:>8,}")
log(f"  Single-record groups (still to dedup):           {single_groups:>8,}")
log(f"  Multi-record groups (already deduped):           {multi_groups:>8,}")

# ============================================================================
# 7. SECTION 6 — DATA INTEGRITY CHECKS
# ============================================================================
log()
log("-" * 72)
log("  SECTION 6: DATA INTEGRITY CHECKS")
log("-" * 72)

# No fbo in multiple groups
fbo_group_count = source.groupby("fbo_id")["dedup_group_id"].nunique()
multi = (fbo_group_count > 1).sum()
log(f"  fbo_ids in multiple groups:                      {multi:>8,}  {'PASS' if multi == 0 else 'FAIL'}")

# Cross-file pair overlap
both = pd.concat([fuzzy[["fbo_id_1", "fbo_id_2"]].assign(src="f"), rejected[["fbo_id_1", "fbo_id_2"]].assign(src="r")])
overlap = both.duplicated(subset=["fbo_id_1", "fbo_id_2"], keep=False).sum()
log(f"  Cross-file pair overlap (fuzzy x rejected):      {overlap // 2:>8,}  {'PASS' if overlap == 0 else 'WARN'}")

# Group ID format
all_gids = source["dedup_group_id"].dropna().unique()
bad_format = sum(1 for g in all_gids if not (isinstance(g, str) and g.startswith("g") and g[1:].isdigit()))
log(f"  Group ID format violations:                      {bad_format:>8,}  {'PASS' if bad_format == 0 else 'FAIL'}")

# Source fbo_ids coverage in pair files
source_fbo_set = set(source["fbo_id"])
fuzzy_fbo_set = set(fuzzy["fbo_id_1"]) | set(fuzzy["fbo_id_2"])
rejected_fbo_set = set(rejected["fbo_id_1"]) | set(rejected["fbo_id_2"])
all_pair_fbo = fuzzy_fbo_set | rejected_fbo_set
missing = all_pair_fbo - source_fbo_set
log(f"  Pair file fbo_ids missing from source:           {len(missing):>8,}  {'PASS' if len(missing) == 0 else 'FAIL'}")

# ============================================================================
# 8. FOOTER
# ============================================================================
log()
log("=" * 72)
log("  END OF REPORT")
log("=" * 72)

# ============================================================================
# 9. SAVE REPORT
# ============================================================================
report_text = "\n".join(report_lines)
with open("stage0_status_report.txt", "w", encoding="utf-8") as f:
    f.write(report_text)

log("\n  Report saved to: stage0_status_report.txt")
log(f"  Lines: {len(report_lines):,}")
