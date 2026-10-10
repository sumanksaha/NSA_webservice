"""Temporary scratch: which evaluator_miss qids are NOT yet in the v2 overlay?"""

import json
from pathlib import Path

recs = json.loads(Path("evaluation/out/ceiling_v5/full_review_tabulation.json").read_text(encoding="utf-8"))["records"]
overlay = json.loads(Path("evaluation/evaluator_v2_overlay.json").read_text(encoding="utf-8"))
covered = set(overlay["widened_conclusions"])
print("v2 overlay entries:", len(covered))
print("overlay top-level keys:", sorted(overlay.keys()))

em = [r for r in recs if r["verdict"] == "evaluator_miss"]
remaining = [r["qid"] for r in em if r["qid"] not in covered]
print("evaluator_miss total:", len(em))
print("remaining after v2 overlay:", len(remaining))
print(" ".join(remaining))

# How many of the remaining does the v2 abstention fix already resolve (>0.5)?
resolved = [
    r["qid"]
    for r in em
    if r["qid"] not in covered
    and max(float(((r.get("machine") or {}).get(a) or {}).get("v2_soft") or 0.0) for a in ("C-O3", "D2", "D3", "E1"))
    > 0.5
]
print("remaining already >0.5 under v2:", len(resolved), resolved)

print()
print("sample record notes (for overlay drafting):")
for r in em:
    if r["qid"] in remaining[:6]:
        print(f"  {r['qid']} cat={r['category']} notes={(r.get('notes') or '')[:160]}")
