"""
Validation script for dedup_group_id consistency across all pipeline output files.

Checks:
1. No fbo_id in multiple groups (extracted_with_exact_groups.csv)
2. All merge assignments reflected in source data
3. Group ID format consistency
4. All fbo_ids in fuzzy/rejected files exist in source
5. No "missed merge" — pairs in fuzzy_candidates that should already be merged
6. Orphan fbo_ids that were merged but still appear in review files
"""
import pandas as pd
from collections import defaultdict

PASS = 0
FAIL = 0

def check(condition: bool, msg: str):
    global PASS, FAIL
    if condition:
        print(f"  [PASS] {msg}")
        PASS += 1
    else:
        print(f"  [FAIL] {msg}")
        FAIL += 1

print("=" * 70)
print("DEDUP GROUP ID CONSISTENCY VALIDATION")
print("=" * 70)

# ============================================================================
# 1. LOAD ALL DATA FILES
# ============================================================================
print("\n--- Loading data files ---")

source = pd.read_csv("extracted_with_exact_groups.csv")
print(f"  extracted_with_exact_groups.csv:     {len(source):,} records")

try:
    assignments = pd.read_csv("dedup_group_assignments.csv")
    print(f"  dedup_group_assignments.csv:         {len(assignments):,} records")
except FileNotFoundError:
    assignments = pd.DataFrame()
    print(f"  dedup_group_assignments.csv:         NOT FOUND (no merge performed)")

fuzzy = pd.read_csv("fuzzy_candidates.csv")
print(f"  fuzzy_candidates.csv:                 {len(fuzzy):,} records")

rejected = pd.read_csv("rejected_number_mismatch.csv")
print(f"  rejected_number_mismatch.csv:         {len(rejected):,} records")

# ============================================================================
# 2. CONSISTENCY CHECK 1: NO FBO_ID IN MULTIPLE GROUPS
# ============================================================================
print("\n--- Check 1: No fbo_id in multiple groups ---")

fbo_group_map = source.groupby("fbo_id")["dedup_group_id"].apply(set).reset_index()
multi_group = fbo_group_map[fbo_group_map["dedup_group_id"].apply(len) > 1]
check(len(multi_group) == 0,
      f"No fbo_id appears in multiple groups ({len(multi_group)} violations)")

if len(multi_group) > 0:
    print(f"  Violations (first 5):")
    for _, row in multi_group.head(5).iterrows():
        print(f"    fbo_id={row['fbo_id']} -> groups: {row['dedup_group_id']}")

# ============================================================================
# 3. CONSISTENCY CHECK 2: MERGE ASSIGNMENTS REFLECTED IN SOURCE
# ============================================================================
print("\n--- Check 2: Merge assignments reflected in source data ---")

if len(assignments) > 0:
    # Build lookup: fbo_id -> expected group from assignments
    assign_map = dict(zip(assignments["fbo_id"], assignments["dedup_group_id"]))
    
    # Check each assigned fbo_id has the correct group in source
    mismatch_count = 0
    for fbo_id, expected_gid in assign_map.items():
        actual_gids = source.loc[source["fbo_id"] == fbo_id, "dedup_group_id"].unique()
        if len(actual_gids) == 0:
            mismatch_count += 1  # fbo_id not found in source (shouldn't happen)
        elif expected_gid not in actual_gids:
            mismatch_count += 1
    
    check(mismatch_count == 0,
          f"All {len(assign_map)} assignment fbo_ids have correct group ID in source "
          f"({mismatch_count} mismatches)")
else:
    print("  [SKIP] No assignments file — no merge performed")

# ============================================================================
# 4. CONSISTENCY CHECK 3: GROUP ID FORMAT CONSISTENCY
# ============================================================================
print("\n--- Check 3: Group ID format consistency ---")

all_gids = source["dedup_group_id"].dropna().unique()
bad_format = [g for g in all_gids if not (isinstance(g, str) and g.startswith("g") and g[1:].isdigit())]
check(len(bad_format) == 0,
      f"All group IDs follow 'gXXXX' format ({len(bad_format)} violations)")

if bad_format:
    print(f"  Bad formats: {bad_format[:10]}")

# ============================================================================
# 5. CONSISTENCY CHECK 4: ALL FBO_IDS EXIST IN SOURCE
# ============================================================================
print("\n--- Check 4: All fbo_ids in review/reject files exist in source ---")

source_fbo_ids = set(source["fbo_id"])

all_fuzzy_fbo = set(fuzzy["fbo_id_1"]) | set(fuzzy["fbo_id_2"])
all_rejected_fbo = set(rejected["fbo_id_1"]) | set(rejected["fbo_id_2"])
all_pair_fbo = all_fuzzy_fbo | all_rejected_fbo

missing_from_source = all_pair_fbo - source_fbo_ids
check(len(missing_from_source) == 0,
      f"All fbo_ids in pair files exist in source "
      f"({len(missing_from_source)} missing)")

if missing_from_source:
    print(f"  Missing fbo_ids (first 5): {list(missing_from_source)[:5]}")

# ============================================================================
# 6. CONSISTENCY CHECK 5: NO "MISSED MERGE" IN FUZZY CANDIDATES
# ============================================================================
print("\n--- Check 5: No 'missed merge' in fuzzy_candidates.csv ---")
print("    (pairs where both sides are already in the same dedup group)")

fbo_to_group = dict(zip(source["fbo_id"], source["dedup_group_id"]))

same_group_count = 0
for _, row in fuzzy.iterrows():
    g1 = fbo_to_group.get(row["fbo_id_1"])
    g2 = fbo_to_group.get(row["fbo_id_2"])
    if g1 and g2 and g1 == g2:
        same_group_count += 1

if len(assignments) > 0:
    check(same_group_count == 0,
          f"No pairs in fuzzy_candidates are already in the same group "
          f"({same_group_count} pairs that could have been auto-merged)")
    if same_group_count > 0:
        print(f"  These {same_group_count} pairs passed the house-number filter")
        print(f"  but are in the same dedup group. Review priority.")
else:
    print(f"  [INFO] {same_group_count} pairs are in the same group (no HC merge done)")

# ============================================================================
# 7. CONSISTENCY CHECK 6: MERGED FBO_IDS NOT STILL IN FUZZY FILES
# ============================================================================
print("\n--- Check 6: No merged fbo_id still appears as 'needs review' ---")

if len(assignments) > 0:
    merged_fbo_ids = set(assignments["fbo_id"])
    fuzzy_fbo_ids = set(fuzzy["fbo_id_1"]) | set(fuzzy["fbo_id_2"])
    
    overlap = merged_fbo_ids & fuzzy_fbo_ids
    check(len(overlap) == 0,
          f"No merged fbo_ids still appear in fuzzy_candidates.csv "
          f"({len(overlap)} still present)")
    
    if overlap:
        print(f"  These fbo_ids were merged but still need review. Flagged.")
else:
    print("  [SKIP] No assignments file — no merge performed")

# ============================================================================
# 8. GROUP SIZE CONSISTENCY
# ============================================================================
print("\n--- Check 7: Group size distribution summary ---")

group_sizes = source["dedup_group_id"].value_counts()
size_dist = group_sizes.value_counts().sort_index()

print(f"  Total groups: {len(group_sizes):,}")
print(f"  Groups with 1 record:  {(group_sizes == 1).sum():>6,}")
print(f"  Groups with 2-5 records: {((group_sizes >= 2) & (group_sizes <= 5)).sum():>6,}")
print(f"  Groups with 6-10 records: {((group_sizes >= 6) & (group_sizes <= 10)).sum():>6,}")
print(f"  Groups with >10 records:  {(group_sizes > 10).sum():>6,}")

# Verify the largest groups are reasonable
largest_groups = group_sizes.head(5)
print(f"\n  5 largest groups:")
for gid, size in largest_groups.items():
    sample_fbos = source[source["dedup_group_id"] == gid]["fbo_id"].head(3).tolist()
    print(f"    {gid}: {size} members (e.g., {sample_fbos})")

# ============================================================================
# 9. COMPARE AGAINST BACKUP (if available)
# ============================================================================
print("\n--- Check 8: Compare against pre-merge backup ---")

try:
    backup = pd.read_csv("extracted_with_exact_groups_backup.csv")
    
    backup_groups = backup["dedup_group_id"].value_counts()
    backup_single = (backup_groups == 1).sum()
    backup_multi = (backup_groups > 1).sum()
    
    current_single = (group_sizes == 1).sum()
    current_multi = (group_sizes > 1).sum()
    
    print(f"  Pre-merge: {backup_single:,} single-record + {backup_multi:,} multi-record groups")
    print(f"  Post-merge: {current_single:,} single-record + {current_multi:,} multi-record groups")
    print(f"  Change: {backup_single - current_single:,} singletons consolidated into "
          f"{current_multi - backup_multi:,} new multi-record groups")
    
    # Count how many group IDs changed
    backup_map = dict(zip(backup["fbo_id"], backup["dedup_group_id"]))
    changed = 0
    for _, row in source.iterrows():
        old_gid = backup_map.get(row["fbo_id"])
        if old_gid and old_gid != row["dedup_group_id"]:
            changed += 1
    print(f"  FBOs with changed group IDs: {changed:,}")
    
    check(changed == len(assignments) if len(assignments) > 0 else changed == 0,
          f"Changed FBOs match assignment count ({changed} = {len(assignments)})")
    
except FileNotFoundError:
    print("  [SKIP] No backup file found")

# ============================================================================
# 10. FINAL SUMMARY
# ============================================================================
print("\n" + "=" * 70)
print("VALIDATION RESULTS")
print("=" * 70)
print(f"\n  {PASS} checks PASSED")
print(f"  {FAIL} checks FAILED")
print()

if FAIL == 0:
    print("  ALL CHECKS PASSED - data is consistent and correct.")
else:
    print(f"  {FAIL} check(s) failed. Review the [FAIL] messages above.")

print()
print("Legend:")
print("  extracted_with_exact_groups.csv   - master FBO -> group mapping")
print("  dedup_group_assignments.csv       - new assignments from HC merge")
print("  fuzzy_candidates.csv              - pairs needing human review")
print("  rejected_number_mismatch.csv      - pairs excluded by house # diff")
