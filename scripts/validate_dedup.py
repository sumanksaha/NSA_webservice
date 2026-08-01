"""Validation script for dedup_group_id consistency across all pipeline output files.

Checks:
1. No fbo_id in multiple groups (extracted_with_exact_groups.csv)
2. All merge assignments reflected in source data
3. Group ID format consistency
4. All fbo_ids in fuzzy/rejected files exist in source
5. No "missed merge" — pairs in fuzzy_candidates that should already be merged
6. Orphan fbo_ids that were merged but still appear in review files
"""

import pandas as pd

PASS = 0
FAIL = 0


def check(condition: bool, msg: str):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1


# ============================================================================
# 1. LOAD ALL DATA FILES
# ============================================================================

source = pd.read_csv("extracted_with_exact_groups.csv")

try:
    assignments = pd.read_csv("dedup_group_assignments.csv")
except FileNotFoundError:
    assignments = pd.DataFrame()

fuzzy = pd.read_csv("fuzzy_candidates.csv")

rejected = pd.read_csv("rejected_number_mismatch.csv")

# ============================================================================
# 2. CONSISTENCY CHECK 1: NO FBO_ID IN MULTIPLE GROUPS
# ============================================================================

fbo_group_map = source.groupby("fbo_id")["dedup_group_id"].apply(set).reset_index()
multi_group = fbo_group_map[fbo_group_map["dedup_group_id"].apply(len) > 1]
check(len(multi_group) == 0, f"No fbo_id appears in multiple groups ({len(multi_group)} violations)")

if len(multi_group) > 0:
    for _, row in multi_group.head(5).iterrows():
        pass

# ============================================================================
# 3. CONSISTENCY CHECK 2: MERGE ASSIGNMENTS REFLECTED IN SOURCE
# ============================================================================

if len(assignments) > 0:
    # Build lookup: fbo_id -> expected group from assignments
    assign_map = dict(zip(assignments["fbo_id"], assignments["dedup_group_id"], strict=False))

    # Check each assigned fbo_id has the correct group in source
    mismatch_count = 0
    for fbo_id, expected_gid in assign_map.items():
        actual_gids = source.loc[source["fbo_id"] == fbo_id, "dedup_group_id"].unique()
        if len(actual_gids) == 0:
            mismatch_count += 1  # fbo_id not found in source (shouldn't happen)
        elif expected_gid not in actual_gids:
            mismatch_count += 1

    check(
        mismatch_count == 0,
        f"All {len(assign_map)} assignment fbo_ids have correct group ID in source ({mismatch_count} mismatches)",
    )
else:
    pass

# ============================================================================
# 4. CONSISTENCY CHECK 3: GROUP ID FORMAT CONSISTENCY
# ============================================================================

all_gids = source["dedup_group_id"].dropna().unique()
bad_format = [g for g in all_gids if not (isinstance(g, str) and g.startswith("g") and g[1:].isdigit())]
check(len(bad_format) == 0, f"All group IDs follow 'gXXXX' format ({len(bad_format)} violations)")

if bad_format:
    pass

# ============================================================================
# 5. CONSISTENCY CHECK 4: ALL FBO_IDS EXIST IN SOURCE
# ============================================================================

source_fbo_ids = set(source["fbo_id"])

all_fuzzy_fbo = set(fuzzy["fbo_id_1"]) | set(fuzzy["fbo_id_2"])
all_rejected_fbo = set(rejected["fbo_id_1"]) | set(rejected["fbo_id_2"])
all_pair_fbo = all_fuzzy_fbo | all_rejected_fbo

missing_from_source = all_pair_fbo - source_fbo_ids
check(len(missing_from_source) == 0, f"All fbo_ids in pair files exist in source ({len(missing_from_source)} missing)")

if missing_from_source:
    pass

# ============================================================================
# 6. CONSISTENCY CHECK 5: NO "MISSED MERGE" IN FUZZY CANDIDATES
# ============================================================================

fbo_to_group = dict(zip(source["fbo_id"], source["dedup_group_id"], strict=False))

same_group_count = 0
for _, row in fuzzy.iterrows():
    g1 = fbo_to_group.get(row["fbo_id_1"])
    g2 = fbo_to_group.get(row["fbo_id_2"])
    if g1 and g2 and g1 == g2:
        same_group_count += 1

if len(assignments) > 0:
    check(
        same_group_count == 0,
        f"No pairs in fuzzy_candidates are already in the same group "
        f"({same_group_count} pairs that could have been auto-merged)",
    )
    if same_group_count > 0:
        pass
else:
    pass

# ============================================================================
# 7. CONSISTENCY CHECK 6: MERGED FBO_IDS NOT STILL IN FUZZY FILES
# ============================================================================

if len(assignments) > 0:
    merged_fbo_ids = set(assignments["fbo_id"])
    fuzzy_fbo_ids = set(fuzzy["fbo_id_1"]) | set(fuzzy["fbo_id_2"])

    overlap = merged_fbo_ids & fuzzy_fbo_ids
    check(len(overlap) == 0, f"No merged fbo_ids still appear in fuzzy_candidates.csv ({len(overlap)} still present)")

    if overlap:
        pass
else:
    pass

# ============================================================================
# 8. GROUP SIZE CONSISTENCY
# ============================================================================

group_sizes = source["dedup_group_id"].value_counts()
size_dist = group_sizes.value_counts().sort_index()


# Verify the largest groups are reasonable
largest_groups = group_sizes.head(5)
for gid, _size in largest_groups.items():
    sample_fbos = source[source["dedup_group_id"] == gid]["fbo_id"].head(3).tolist()

# ============================================================================
# 9. COMPARE AGAINST BACKUP (if available)
# ============================================================================

try:
    backup = pd.read_csv("extracted_with_exact_groups_backup.csv")

    backup_groups = backup["dedup_group_id"].value_counts()
    backup_single = (backup_groups == 1).sum()
    backup_multi = (backup_groups > 1).sum()

    current_single = (group_sizes == 1).sum()
    current_multi = (group_sizes > 1).sum()

    # Count how many group IDs changed
    backup_map = dict(zip(backup["fbo_id"], backup["dedup_group_id"], strict=False))
    changed = 0
    for _, row in source.iterrows():
        old_gid = backup_map.get(row["fbo_id"])
        if old_gid and old_gid != row["dedup_group_id"]:
            changed += 1

    check(
        changed == len(assignments) if len(assignments) > 0 else changed == 0,
        f"Changed FBOs match assignment count ({changed} = {len(assignments)})",
    )

except FileNotFoundError:
    pass

# ============================================================================
# 10. FINAL SUMMARY
# ============================================================================

if FAIL == 0:
    pass
else:
    pass
