"""
TASK B.4 — AUTO-MERGE HIGH-CONFIDENCE PAIRS (house number match)
via union-find connected components.

Input:  fuzzy_candidates.csv  (36,478 rows, with high_confidence flag)
Output: dedup_group_assignments.csv  (mapping fbo_id -> new dedup_group_id)
        merged_summary_report.txt    (group counts, large clusters)
"""

from collections import defaultdict

import pandas as pd

# ============================================================================
# 1. LOAD DATA
# ============================================================================
print("=" * 70)
print("TASK B.4: UNION-FIND MERGE OF HIGH-CONFIDENCE PAIRS")
print("=" * 70)

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")
hc = df_fuzzy[df_fuzzy["high_confidence"] == True].copy()
print(f"\nLoaded {len(hc):,} high-confidence pairs from fuzzy_candidates.csv")

# ============================================================================
# 2. UNION-FIND (DISJOINT SET UNION)
# ============================================================================
print("\nBuilding union-find connected components...")

parent = {}
rank = {}


def find(x):
    """Find with path compression."""
    if parent[x] != x:
        parent[x] = find(parent[x])
    return parent[x]


def union(x, y):
    """Union by rank."""
    rx, ry = find(x), find(y)
    if rx == ry:
        return
    if rank[rx] < rank[ry]:
        parent[rx] = ry
    elif rank[rx] > rank[ry]:
        parent[ry] = rx
    else:
        parent[ry] = rx
        rank[rx] += 1


# Collect all unique fbo_ids
all_ids = set()
pairs_processed = 0
for _, row in hc.iterrows():
    id1, id2 = row["fbo_id_1"], row["fbo_id_2"]
    if id1 not in parent:
        parent[id1] = id1
        rank[id1] = 0
    if id2 not in parent:
        parent[id2] = id2
        rank[id2] = 0
    all_ids.add(id1)
    all_ids.add(id2)
    union(id1, id2)
    pairs_processed += 1

print(f"  Unique fbo_ids in high-confidence pairs: {len(all_ids):,}")
print(f"  Pairs processed: {pairs_processed:,}")

# ============================================================================
# 3. BUILD COMPONENTS
# ============================================================================
print("\nBuilding connected components...")

components = defaultdict(list)
for fbo_id in all_ids:
    root = find(fbo_id)
    components[root].append(fbo_id)

# Sort components by size (descending)
sorted_components = sorted(components.values(), key=len, reverse=True)

print(f"  Total connected components (dedup groups): {len(sorted_components):,}")
print(f"  Single-fbo groups (no merge partner): {sum(1 for c in sorted_components if len(c) == 1):,}")
print(f"  Multi-fbo groups (actual merges): {sum(1 for c in sorted_components if len(c) > 1):,}")

# ============================================================================
# 4. IDENTIFY LARGE CLUSTERS (>5 members)
# ============================================================================
print("\n--- Large Cluster Analysis ---")
large_clusters = [c for c in sorted_components if len(c) > 5]
small_clusters = [c for c in sorted_components if 2 <= len(c) <= 5]
singletons = [c for c in sorted_components if len(c) == 1]

print(f"  Clusters with >5 members (LARGE): {len(large_clusters):,}")
print(f"  Clusters with 2-5 members:        {len(small_clusters):,}")
print(f"  Singletons (no merge):            {len(singletons):,}")
print()

if large_clusters:
    print(f"  {'Size':<6} {'Members':<20} {'Group ID':<12}")
    print(f"  {'-' * 40}")
    for i, cluster in enumerate(large_clusters[:10]):  # Top 10
        gid = f"g_fuzzy_{i + 1}"
        print(f"  {len(cluster):<6} {str(cluster[0])[:18]:<20} {gid:<12}")
    if len(large_clusters) > 10:
        print(f"  ... and {len(large_clusters) - 10} more large clusters")
else:
    print("  No large clusters (>5 members) found. Good.")

# Show distribution summary
print("\n  Cluster size distribution:")
size_counts = defaultdict(int)
for c in sorted_components:
    size_counts[len(c)] += 1
for size in sorted(size_counts.keys()):
    print(f"    Size {size}: {size_counts[size]:,} clusters")

# ============================================================================
# 5. ASSIGN NEW DEDUP GROUP IDs
# ============================================================================
print("\nAssigning dedup_group_id values...")

# Load existing source data to get the max existing group number
source = pd.read_csv("extracted_with_exact_groups.csv")
existing_groups = source["dedup_group_id"].dropna().unique()

# Parse existing group numbers: format is "g1", "g2", etc.
existing_nums = []
for g in existing_groups:
    try:
        num = int(g[1:]) if g.startswith("g") else 0
        existing_nums.append(num)
    except (ValueError, IndexError):
        pass

max_existing = max(existing_nums) if existing_nums else 0
print(f"  Max existing dedup_group_id: g{max_existing}")

# Assign new group IDs starting from max+1
new_group_id_counter = max_existing + 1
# singletons already have group IDs from exact-match phase, not assigned here

# Build mapping: fbo_id -> new dedup_group_id (for multi-fbo clusters only)
# Singletons keep their existing group assignment
assignments = []
for cluster in sorted_components:
    if len(cluster) >= 2:
        gid = f"g{new_group_id_counter}"
        new_group_id_counter += 1
        for fbo_id in cluster:
            assignments.append({"fbo_id": fbo_id, "dedup_group_id": gid})
            # For multi-fbo clusters, all members get the same new group ID
    # Singletons are NOT assigned here - they keep their existing group from exact-match dedup

df_assignments = pd.DataFrame(assignments)
print(f"  New dedup_group_id assignments: {len(df_assignments):,}")
print(f"  Range of new group IDs: g{max_existing + 1} - g{new_group_id_counter - 1}")

# ============================================================================
# 6. SAVE OUTPUT
# ============================================================================
# Save assignments
df_assignments.to_csv("dedup_group_assignments.csv", index=False)
print(f"\n[OK] Saved dedup_group_assignments.csv ({len(df_assignments):,} rows)")

# Update source data with new group IDs for merged fbo_ids
# This ensures the main data table reflects the new multi-fbo groups
source_updated = source.copy()
assignment_map = dict(zip(df_assignments["fbo_id"], df_assignments["dedup_group_id"]))
update_mask = source_updated["fbo_id"].isin(assignment_map.keys())
source_updated.loc[update_mask, "dedup_group_id"] = source_updated.loc[update_mask, "fbo_id"].map(assignment_map)
updated_count = update_mask.sum()
source_updated.to_csv("extracted_with_exact_groups.csv", index=False)
print(f"[OK] Updated extracted_with_exact_groups.csv: {updated_count:,} fbo_ids got new group IDs")

# Save summary report
with open("merged_summary_report.txt", "w") as f:
    f.write("=" * 70 + "\n")
    f.write("TASK B.4: MERGED HIGH-CONFIDENCE PAIRS - SUMMARY REPORT\n")
    f.write("=" * 70 + "\n\n")
    f.write(f"High-confidence pairs processed: {pairs_processed:,}\n")
    f.write(f"Unique fbo_ids involved: {len(all_ids):,}\n")
    f.write(f"Connected components (dedup groups): {len(sorted_components):,}\n")
    f.write(f"  - Multi-fbo clusters (2+ members): {sum(1 for c in sorted_components if len(c) >= 2):,}\n")
    f.write(f"  - Singletons: {sum(1 for c in sorted_components if len(c) == 1):,}\n\n")

    f.write("Cluster size distribution:\n")
    for size in sorted(size_counts.keys()):
        f.write(f"  Size {size}: {size_counts[size]:,} clusters\n")

    f.write(f"\nLarge clusters (>5 members): {len(large_clusters):,}\n")
    if large_clusters:
        f.write("\nTop 20 largest clusters:\n")
        f.write(f"  {'Rank':<6} {'Size':<8} {'FBO IDs':<30}\n")
        f.write(f"  {'-' * 44}\n")
        for i, cluster in enumerate(large_clusters[:20]):
            f.write(
                f"  {i + 1:<6} {len(cluster):<8} {', '.join(str(x) for x in cluster[:5])}{'...' if len(cluster) > 5 else ''}\n"
            )

    f.write(f"\nNew group ID range: g{max_existing + 1} - g{new_group_id_counter - 1}\n")
    f.write(f"Total new groups created: {new_group_id_counter - max_existing - 1}\n")

print("[OK] Saved merged_summary_report.txt")

# ============================================================================
# 7. FINAL REPORT (console)
# ============================================================================
print("\n" + "=" * 70)
print("FINAL RESULTS")
print("=" * 70)

print(f"\n  Pairs merged:                         {pairs_processed:>8,}")
print(f"  FBOs involved:                        {len(all_ids):>8,}")
print(f"  New dedup groups created:             {new_group_id_counter - max_existing - 1:>8,}")
print(f"  Largest cluster size:                 {len(sorted_components[0]) if sorted_components else 0:>8,}")
print(f"  Clusters >5 members:                  {len(large_clusters):>8,}")
print(f"  Total pairs remaining for Task C:     {len(df_fuzzy[df_fuzzy['high_confidence'] == False]):>8,}")
ambiguous_count = (~df_fuzzy["high_confidence"]).sum()
print(f"    (ambiguous queue - no house number): {ambiguous_count:,}")

print("\n" + "=" * 70)
print("TASK B COMPLETE - ready for Task C")
print("=" * 70)
