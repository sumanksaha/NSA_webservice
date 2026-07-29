"""
TASK C — TRIAGE THE AMBIGUOUS QUEUE (no house number on one/both sides)

Input:  fuzzy_candidates.csv  (high_confidence == False subset, 24,442 pairs)
Output: review_priority.csv      (locality_match True)
        review_low_priority.csv  (locality_match False)
"""

import re

import pandas as pd

# ============================================================================
# 1. LOAD THE AMBIGUOUS SUBSET
# ============================================================================
print("=" * 70)
print("TASK C: TRIAGE THE AMBIGUOUS QUEUE")
print("=" * 70)

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")
ambig = df_fuzzy[df_fuzzy["high_confidence"] == False].copy()
print(f"\nLoaded {len(ambig):,} ambiguous pairs (no house number on >=1 side)")

# ============================================================================
# 2. BUILD LOCALITY/STOP-LIST
# ============================================================================
# Tokens that are ALWAYS generic and should never count as a locality match
GENERIC_TOKENS = {
    # Geographic
    "KOLKATA",
    "KOL",
    "CALCUTTA",
    "WEST",
    "BENGAL",
    "WB",
    "W.B.",
    "MUNICIPAL",
    "CORPORATION",
    "MUNICIPALITY",
    "CITY",
    "TOWN",
    "BOROUGH",
    "WARD",
    "PIN",
    "PINCODE",
    "POSTAL",
    "ZIP",
    # Street suffixes
    "STREET",
    "ROAD",
    "LANE",
    "AVENUE",
    "DRIVE",
    "CIRCUS",
    "ROW",
    "SQUARE",
    "PLACE",
    "COURT",
    "CRESCENT",
    "GARDENS",
    "PARK",
    "BRIDGE",
    "FLYOVER",
    "OVERBRIDGE",
    # Building / unit indicators
    "GROUND",
    "FIRST",
    "SECOND",
    "THIRD",
    "FOURTH",
    "FIFTH",
    "FLOOR",
    "FLOORS",
    "BASEMENT",
    "TOP",
    "UPPER",
    "LOWER",
    "FLAT",
    "ROOM",
    "SHOP",
    "STALL",
    "UNIT",
    "OFFICE",
    "CHAMBER",
    "BLDG",
    "BUILDING",
    "BLOCK",
    "TOWER",
    "WING",
    "PHASE",
    "HOUSE",
    "HOLDING",
    "PLOT",
    "PREMISES",
    # Number / designation markers
    "NO",
    "NUMBER",
    "NUM",
    "#",
    # Directional / filler
    "NEAR",
    "OPP",
    "OPPOSITE",
    "BESIDE",
    "BEHIND",
    "BETWEEN",
    "EAST",
    "NORTH",
    "SOUTH",
    "EASTERN",
    "WESTERN",
    "NORTHERN",
    "SOUTHERN",
    # Age/era descriptors (too generic for locality matching)
    "NEW",
    "OLD",
    "GREATER",
    # Common abbreviations
    "ST",
    "RD",
    "LN",
    "APT",
    "APPT",
    "DEPT",
    "DEPT.",
    "C/O",
    "CARE",
    "OF",
    "KMC",
    "KMDA",
    "KMC",
    # Very short or meaningless alone
    "THE",
    "AND",
    "&",
    "AT",
    "BY",
    "FOR",
    "TO",
    "IN",
    "ON",
    "A",
    "AN",
    "VIA",
}

# Tokens that are numeric-only or start with a digit (PIN codes, house numbers)
NUMERIC_PATTERN = re.compile(r"^\d")


def extract_distinctive_tokens(address: str) -> set:
    """Extract distinctive locality/landmark tokens from an address,
    excluding generic address tokens and pure numbers."""
    if pd.isna(address) or not str(address).strip():
        return set()

    addr = str(address).upper()

    # Split by whitespace and common punctuation
    tokens = re.split(r"[\s,./\-()]+", addr)

    distinctive = set()
    for token in tokens:
        token = token.strip(" '\"")
        if not token or len(token) <= 2:  # skip very short tokens
            continue
        if NUMERIC_PATTERN.match(token):  # skip anything starting with digit
            continue
        if token in GENERIC_TOKENS:  # skip generic tokens
            continue

        distinctive.add(token)

    return distinctive


# ============================================================================
# 3. COMPUTE LOCALITY MATCH
# ============================================================================
print("\nComputing locality_match for each pair...")


def has_locality_match(row) -> bool:
    tokens_1 = extract_distinctive_tokens(row["raw_address_1"])
    tokens_2 = extract_distinctive_tokens(row["raw_address_2"])

    if not tokens_1 or not tokens_2:
        return False

    # Check for any shared distinctive token
    shared = tokens_1 & tokens_2
    return len(shared) > 0


ambig["locality_match"] = ambig.apply(has_locality_match, axis=1)

match_count = ambig["locality_match"].sum()
no_match_count = (~ambig["locality_match"]).sum()
print(f"  Locality match FOUND:  {match_count:>8,} ({100 * match_count / len(ambig):.1f}%)")
print(f"  No locality match:     {no_match_count:>8,} ({100 * no_match_count / len(ambig):.1f}%)")

# ============================================================================
# 4. SORT BY block_key, THEN locality_match (True first)
# ============================================================================
print("\nSorting by block_key (PIN), then locality_match (True first within each block)...")

# Sort: block_key alpha, then locality_match descending (True=1 before False=0)
ambig_sorted = ambig.sort_values(by=["block_key", "locality_match"], ascending=[True, False]).reset_index(drop=True)

# Verify sort
print(f"  Sorted {len(ambig_sorted):,} rows")

# ============================================================================
# 5. SPLIT INTO TWO FILES
# ============================================================================
print("\nSplitting into priority files...")

priority = ambig_sorted[ambig_sorted["locality_match"] == True].copy()
low_priority = ambig_sorted[ambig_sorted["locality_match"] == False].copy()

# Columns to output (all from fuzzy_candidates.csv)
output_cols = [
    "fbo_id_1",
    "fbo_id_2",
    "raw_address_1",
    "raw_address_2",
    "similarity_score",
    "block_key",
    "house_number_1",
    "house_number_2",
    "high_confidence",
    "locality_match",
]

priority[output_cols].to_csv("review_priority.csv", index=False)
low_priority[output_cols].to_csv("review_low_priority.csv", index=False)

print(f"  -> review_priority.csv:     {len(priority):>8,} rows (locality_match True)")
print(f"  -> review_low_priority.csv: {len(low_priority):>8,} rows (no locality match)")
print(f"  -> Total:                   {len(priority) + len(low_priority):>8,} rows")

# ============================================================================
# 6. SPOT-CHECK: first 20 rows grouped by block_key
# ============================================================================
print("\n" + "=" * 70)
print("SPOT-CHECK: First 20 rows (grouped by block_key)")
print("=" * 70)

printed = 0
current_block = None
for _, row in ambig_sorted.iterrows():
    if printed >= 20:
        break

    if row["block_key"] != current_block:
        current_block = row["block_key"]
        print(f"\n  --- Block: {current_block} ---")

    a1 = str(row["raw_address_1"])[:55]
    a2 = str(row["raw_address_2"])[:55]
    lm = "Y" if row["locality_match"] else "N"
    print(f"  [{lm}] {row['similarity_score']} | {a1}...")
    print(f"       {a2}...")
    printed += 1

# Show total remaining after the spot-check
remaining = len(ambig_sorted) - printed
if remaining > 0:
    print(f"\n  ... and {remaining:,} more rows")

# ============================================================================
# 7. FINAL REPORT
# ============================================================================
print("\n" + "=" * 70)
print("TASK C COMPLETE - RESULTS SUMMARY")
print("=" * 70)

print(f"\n  Total ambiguous pairs processed:       {len(ambig):>8,}")
print(f"    -> review_priority.csv:              {len(priority):>8,}")
print(f"    -> review_low_priority.csv:          {len(low_priority):>8,}")
print()
print(f"  Locality match rate: {100 * match_count / len(ambig):.1f}%")
print()

# Show sample distinctive tokens from matched pairs for transparency
if match_count > 0:
    sample_matches = priority.head(10)
    print("  Sample distinctive tokens that triggered locality_match:")
    seen_tokens = set()
    for _, row in sample_matches.iterrows():
        t1 = extract_distinctive_tokens(row["raw_address_1"])
        t2 = extract_distinctive_tokens(row["raw_address_2"])
        shared = (t1 & t2) - seen_tokens
        if shared:
            print(f"    {', '.join(sorted(shared))}")
            seen_tokens |= shared
print(f"    (showing first {len(seen_tokens)} unique tokens found)")

print("\n  Output files:")
print(f"    - review_priority.csv     ({len(priority):,} rows) - higher-confidence, REVIEW FIRST")
print(f"    - review_low_priority.csv ({len(low_priority):,} rows) - lower-confidence, reviewed later")

print("\n" + "=" * 70)
print("STAGE 0 DEDUP PIPELINE STATUS")
print("=" * 70)
print("""
  Step 1: Exact-match dedup        -> extracted_with_exact_groups.csv  [DONE]
  Step 2: Fuzzy cdist               -> fuzzy_candidates.csv (orig)     [DONE]
  Step 3: House-number filter       -> rejected_number_mismatch.csv    [DONE]
  Step 4: High-confidence auto-merge -> 4,164 groups created           [DONE]
  Step 5: Ambiguous queue triage    -> review_priority/low_priority    [DONE]
""")
print("=" * 70)
print("READY FOR HUMAN REVIEW")
print("=" * 70)
