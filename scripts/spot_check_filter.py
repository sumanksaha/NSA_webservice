"""Spot-check the house number filter output for correctness."""

import warnings

import pandas as pd

warnings.filterwarnings("ignore")

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")
df_rej = pd.read_csv("rejected_number_mismatch.csv")

print("=== SPOT CHECK 1: Was the '83,' vs '83' false rejection fixed? ===")
# This was a known bug in the previous version
q = df_fuzzy[df_fuzzy["fbo_id_1"] == 22820038000259]
for _, r in q.iterrows():
    a1 = str(r["raw_address_1"])[:65]
    a2 = str(r["raw_address_2"])[:65]
    print(f"  FUZZY {r['similarity_score']} | hc={r['high_confidence']} | {a1}")
    print(f"    vs {a2}")
    print()

print("=== SPOT CHECK 2: Rejected pairs - do house numbers actually differ? ===")
sample_rej = df_rej.head(10)
for _, r in sample_rej.iterrows():
    a1 = str(r["raw_address_1"])[:55]
    a2 = str(r["raw_address_2"])[:55]
    print(f"  REJECT #{r['house_number_1']} vs #{r['house_number_2']} ({r['similarity_score']})")
    print(f"    {a1}...")
    print(f"    {a2}...")
    print()

print("=== SPOT CHECK 3: Trailing comma in rejected house numbers? ===")
has_trailing = df_rej["house_number_1"].astype(str).str.contains(r",$")
print(f"  Rejected with trailing comma in hn_1: {has_trailing.sum()}")
has_trailing2 = df_rej["house_number_2"].astype(str).str.contains(r",$")
print(f"  Rejected with trailing comma in hn_2: {has_trailing2.sum()}")

print()
print("=== SPOT CHECK 4: Summary counts ===")
print(f"  High-confidence (hn match):  {df_fuzzy['high_confidence'].sum():,}")
print(f"  No house number (keep):      {(~df_fuzzy['high_confidence']).sum():,}")
print(f"  Total for human review:      {len(df_fuzzy):,}")
print(f"  Total rejected:              {len(df_rej):,}")
print(f"  Combined total:              {len(df_fuzzy) + len(df_rej):,}")

print()
print("=== SPOT CHECK 5: High-confidence pairs sample ===")
hc = df_fuzzy[df_fuzzy["high_confidence"] == True].head(5)
for _, r in hc.iterrows():
    print(f"  MATCH #{r['house_number_1'] if 'house_number_1' in r else '?'} | {r['similarity_score']}")

print("\n--- Done ---")
