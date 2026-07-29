#!/usr/bin/env python3
"""STEP 4.5: House-Number Disambiguation Filter

For every fuzzy candidate pair (score >= 90, from fuzzy_dedup_stage0.py):
1. Extract leading house/premises number from each raw_address
2. If both have house numbers AND they differ -> move to rejected_number_mismatch.csv
3. If either has NO extractable house number -> keep in fuzzy_candidates.csv as-is
4. If house numbers match -> keep in fuzzy_candidates.csv with high_confidence flag

Fixes over previous version:
- Removed aggressive ward exclusion that suppressed house number extraction
  on any address mentioning "WARD NO" (was causing false negatives)
- Strips trailing punctuation from extracted numbers so "44," == "44"
- Uses digit-first patterns to avoid alphanumeric over-capture
- Re-merges previously split files so corrections apply retroactively
"""

import os
import re
import warnings

import pandas as pd

warnings.filterwarnings("ignore")

# ============================================================================
# Load & merge all candidate pairs
# ============================================================================

fuzzy_file = "fuzzy_candidates.csv"
rejected_file = "rejected_number_mismatch.csv"

# Load current fuzzy candidates
base_cols = ["fbo_id_1", "fbo_id_2", "raw_address_1", "raw_address_2", "similarity_score", "block_key"]

df_fuzzy = pd.read_csv(fuzzy_file, usecols=base_cols)

# If a previously-rejected file exists, merge it back so we re-process everything
# (the previous run had extraction bugs we need to correct)
if os.path.exists(rejected_file):
    try:
        df_rejected = pd.read_csv(rejected_file, usecols=base_cols)
        df = pd.concat([df_fuzzy, df_rejected], ignore_index=True)
        # Drop duplicate pairs if any exist (shouldn't, but be safe)
        before = len(df)
        df = df.drop_duplicates(subset=["fbo_id_1", "fbo_id_2"])
        if len(df) < before:
            pass
    except Exception:
        df = df_fuzzy
else:
    df = df_fuzzy


# ============================================================================
# House number extraction
# ============================================================================


def extract_house_number(address: str) -> str | None:
    """Extract the leading house/premises number from a Kolkata address.

    Strategy (in priority order):
    1. First numeric token at the very start of the address string
       (most reliable indicator of a house number)
    2. Number following "No", "Plot", "Premises", "Holding No" keywords
       (fallback for addresses that begin with a non-numeric prefix)

    Returns the cleaned number string, or None if no house number found.

    Examples:
      "44, EZRA STREET ..."          -> "44"
      "17 ARMENIAN STREET WARD ..."  -> "17"     (ward reference NOT a blocker)
      "1050/1, SURVEY PARK ..."      -> "1050/1"
      "20/5B ARMENIAN STREET ..."    -> "20/5B"
      "T-16, OMDA RAJA LANE ..."     -> None     (no leading digit)
      "WARD NO -42, BOROUGH ..."     -> None     (ward-only, no real house number)

    """
    if pd.isna(address) or not str(address).strip():
        return None

    addr = str(address).strip()

    # ------------------------------------------------------------------
    # Priority 1: Leading numeric token at the start of the address
    # Must start with a digit (optionally after leading whitespace).
    # Matches: "44", "1050/1", "20/5B", "15A", "1E"
    # Does NOT match: "T-16", "WARD", "FLAT"
    # ------------------------------------------------------------------
    m = re.match(r"^\s*(\d[\dA-Za-z]*(?:/\d[\dA-Za-z]*)*)", addr)
    if m:
        candidate = m.group(1).rstrip(",-./ ")
        # Skip if it's a 6-digit PIN code (700001, 700015, etc.)
        if re.match(r"^\d{6}$", candidate):
            return None
        if candidate:
            return candidate

    # ------------------------------------------------------------------
    # Priority 2: Number after recognised keywords like "No", "Plot", etc.
    # Used when the address starts with something other than a digit.
    # Avoids matching ward numbers ("WARD NO -42") because the capture
    # group requires a leading digit (the hyphen prevents a match).
    # ------------------------------------------------------------------
    keyword_patterns = [
        r"(?:HOUSE\s+NO\.?|NO\.?)\s*(\d[\dA-Za-z]*(?:/\d[\dA-Za-z]*)*)",
        r"PLOT\s*(?:NO\.?)?\s*(\d[\dA-Za-z]*(?:/\d[\dA-Za-z]*)*)",
        r"PREMISES\s*(?:NO\.?)?\s*(\d[\dA-Za-z]*(?:/\d[\dA-Za-z]*)*)",
        r"HOLDING\s+NO\.?\s*(\d[\dA-Za-z]*(?:/\d[\dA-Za-z]*)*)",
        r"HOLDER\s+NO\.?\s*(\d[\dA-Za-z]*(?:/\d[\dA-Za-z]*)*)",
    ]

    for pattern in keyword_patterns:
        m = re.search(pattern, addr, re.IGNORECASE)
        if m:
            candidate = m.group(1).rstrip(",-./ ")
            if candidate:
                return candidate

    return None


# Apply extraction to both addresses in every row
df["house_number_1"] = df["raw_address_1"].apply(extract_house_number)
df["house_number_2"] = df["raw_address_2"].apply(extract_house_number)

# ============================================================================
# Classification logic
# ============================================================================


def classify_pair(row) -> str:
    """Returns one of:
    "reject"  -> house numbers differ -> move to rejected_number_mismatch.csv
    "match"   -> house numbers match  -> keep, flag as high-confidence
    "keep"    -> one or both have no house number -> keep as-is
    """
    hn1 = row["house_number_1"]
    hn2 = row["house_number_2"]

    # If either is missing -> can't disambiguate by house number -> keep
    if pd.isna(hn1) or pd.isna(hn2):
        return "keep"

    # Exact string comparison (user requirement: "not fuzzy")
    if hn1 != hn2:
        return "reject"

    # Exact match -> high-confidence duplicate signal
    return "match"


df["classification"] = df.apply(classify_pair, axis=1)

# ============================================================================
# Split into output files
# ============================================================================

# --- Rejected pairs (house number mismatch) ---
mask_reject = df["classification"] == "reject"
df_rejected = df[mask_reject].copy()
rejected_cols = [*base_cols, "house_number_1", "house_number_2"]
df_rejected[rejected_cols].to_csv(rejected_file, index=False)

# --- Pairs for human review ---
mask_keep = df["classification"].isin(["keep", "match"])
df_review = df[mask_keep].copy()

# Add high_confidence column for pairs where house numbers matched exactly
df_review["high_confidence"] = df_review["classification"] == "match"

# Output columns: base + house numbers + confidence flag (so reviewers can verify)
review_cols = [*base_cols, "house_number_1", "house_number_2", "high_confidence"]
df_review[review_cols].to_csv(fuzzy_file, index=False)

# ============================================================================
# Report
# ============================================================================
total = len(df)
rejected = len(df_rejected)
review = len(df_review)
matched = int((df["classification"] == "match").sum())
kept_no_hn = int((df["classification"] == "keep").sum())


# Coverage stats
with_hn1 = int(df["house_number_1"].notna().sum())
with_hn2 = int(df["house_number_2"].notna().sum())
with_both = int((df["house_number_1"].notna() & df["house_number_2"].notna()).sum())
with_neither = int((df["house_number_1"].isna() & df["house_number_2"].isna()).sum())


# Show samples
if rejected > 0:
    for _, _row in df_rejected.head(5).iterrows():
        pass

if matched > 0:
    matched_sample = df[df["classification"] == "match"].head(5)
    for _, _row in matched_sample.iterrows():
        pass

hc_label = "(+high_confidence flag)" if matched > 0 else ""
