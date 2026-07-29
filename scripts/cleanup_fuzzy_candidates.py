"""Clean up fuzzy_candidates.csv by removing the 12,036 already-merged
high-confidence pairs, leaving only the 24,442 ambiguous pairs.

Also updates review_priority.csv and review_low_priority.csv to match.
"""

import pandas as pd

# 1. Load and backup
df = pd.read_csv("fuzzy_candidates.csv")

# Create backup
df.to_csv("fuzzy_candidates_backup.csv", index=False)

# 2. Filter out high-confidence pairs
hc_count = df["high_confidence"].sum()
ambig = df[not df["high_confidence"]].copy()

# 3. Save cleaned file
ambig.to_csv("fuzzy_candidates.csv", index=False)

# 4. Also update review_priority.csv and review_low_priority.csv
#    (re-generate them from the cleaned fuzzy_candidates to stay in sync)
import re

# Load the cleaned file for review generation
df_clean = pd.read_csv("fuzzy_candidates.csv")

# Recompute locality_match using the same logic as triage_ambiguous.py
GENERIC_TOKENS = {
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
    "NO",
    "NUMBER",
    "NUM",
    "#",
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
    "NEW",
    "OLD",
    "GREATER",
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

NUM_PATTERN = re.compile(r"^\d")


def extract_tokens(addr):
    if pd.isna(addr) or not str(addr).strip():
        return set()
    tokens = re.split(r"[\s,./\-()]+", str(addr).upper())
    result = set()
    for t in tokens:
        t = t.strip(" '\"")
        if len(t) <= 2 or NUM_PATTERN.match(t) or t in GENERIC_TOKENS:
            continue
        result.add(t)
    return result


df_clean["locality_match"] = df_clean.apply(
    lambda r: bool(extract_tokens(r["raw_address_1"]) & extract_tokens(r["raw_address_2"])),
    axis=1,
)

sorted_df = df_clean.sort_values(by=["block_key", "locality_match"], ascending=[True, False]).reset_index(drop=True)

priority = sorted_df[sorted_df["locality_match"]].copy()
low_priority = sorted_df[not sorted_df["locality_match"]].copy()

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
