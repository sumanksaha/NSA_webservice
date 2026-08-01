"""Task A: Reconcile the 17,196 pair discrepancy between:
- Original cdist checkpoint: 235,455 pairs
- Current filter_house_number.py output: 218,259 pairs (36,478 + 181,781)
"""

import pandas as pd

df_fuzzy = pd.read_csv("fuzzy_candidates.csv")
df_rej = pd.read_csv("rejected_number_mismatch.csv")

current_total = len(df_fuzzy) + len(df_rej)
original_total = 235_455
gap = original_total - current_total


# 1. Check unusable_no_address.csv
try:
    unusable = pd.read_csv("unusable_no_address.csv")
    if len(unusable) > 0:
        ids_in_pairs = (
            set(df_fuzzy["fbo_id_1"]) | set(df_fuzzy["fbo_id_2"]) | set(df_rej["fbo_id_1"]) | set(df_rej["fbo_id_2"])
        )
        unusable_ids = set(unusable["fbo_id"]) if "fbo_id" in unusable.columns else set()
        overlap = ids_in_pairs & unusable_ids
    else:
        pass
except FileNotFoundError:
    pass

# 2. Check duplicate pair rows from overlapping blocks

# 3. Check silent drop in filter script

# 4. CRASH RECOVERY EXPLANATION
