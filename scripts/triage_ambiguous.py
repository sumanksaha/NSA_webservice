"""TASK C — TRIAGE THE AMBIGUOUS QUEUE (no house number on one/both sides)

Input:  fuzzy_candidates.csv  (high_confidence == False subset, 24,442 pairs)
Output: review_priority.csv      (locality_match True)
        review_low_priority.csv  (locality_match False)
"""

import re

import pandas as pd

# ============================================================================
# 1. LOAD THE AMBIGUOUS SUBSET
# ============================================================================

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")
ambig = df_fuzzy[not df_fuzzy["high_confidence"]].copy()

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
    excluding generic address tokens and pure numbers.
    """
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

# ============================================================================
# 4. SORT BY block_key, THEN locality_match (True first)
# ============================================================================

# Sort: block_key alpha, then locality_match descending (True=1 before False=0)
ambig_sorted = ambig.sort_values(by=["block_key", "locality_match"], ascending=[True, False]).reset_index(drop=True)

# Verify sort

# ============================================================================
# 5. SPLIT INTO TWO FILES
# ============================================================================

priority = ambig_sorted[ambig_sorted["locality_match"]].copy()
low_priority = ambig_sorted[not ambig_sorted["locality_match"]].copy()

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


# ============================================================================
# 6. SPOT-CHECK: first 20 rows grouped by block_key
# ============================================================================

printed = 0
current_block = None
for _, row in ambig_sorted.iterrows():
    if printed >= 20:
        break

    if row["block_key"] != current_block:
        current_block = row["block_key"]

    a1 = str(row["raw_address_1"])[:55]
    a2 = str(row["raw_address_2"])[:55]
    lm = "Y" if row["locality_match"] else "N"
    printed += 1

# Show total remaining after the spot-check
remaining = len(ambig_sorted) - printed
if remaining > 0:
    pass

# ============================================================================
# 7. FINAL REPORT
# ============================================================================


# Show sample distinctive tokens from matched pairs for transparency
if match_count > 0:
    sample_matches = priority.head(10)
    seen_tokens = set()
    for _, row in sample_matches.iterrows():
        t1 = extract_distinctive_tokens(row["raw_address_1"])
        t2 = extract_distinctive_tokens(row["raw_address_2"])
        shared = (t1 & t2) - seen_tokens
        if shared:
            seen_tokens |= shared
