"""Spot-check the house number filter output for correctness."""

import warnings

import pandas as pd

warnings.filterwarnings("ignore")

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")
df_rej = pd.read_csv("rejected_number_mismatch.csv")

# This was a known bug in the previous version
q = df_fuzzy[df_fuzzy["fbo_id_1"] == 22820038000259]
for _, r in q.iterrows():
    a1 = str(r["raw_address_1"])[:65]
    a2 = str(r["raw_address_2"])[:65]

sample_rej = df_rej.head(10)
for _, r in sample_rej.iterrows():
    a1 = str(r["raw_address_1"])[:55]
    a2 = str(r["raw_address_2"])[:55]

has_trailing = df_rej["house_number_1"].astype(str).str.contains(r",$")
has_trailing2 = df_rej["house_number_2"].astype(str).str.contains(r",$")


hc = df_fuzzy[df_fuzzy["high_confidence"]].head(5)
for _, r in hc.iterrows():
    pass
