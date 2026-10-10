"""Temporary scratch: dump overlay schema + abstention banner keys."""

import json

with open("evaluation/evaluator_v2_overlay.json", encoding="utf-8") as f:
    ov = json.load(f)

for k, v in ov.items():
    print(
        f"{k}: {'dict len ' + str(len(v)) if isinstance(v, dict) else 'list len ' + str(len(v)) if isinstance(v, list) else repr(v)[:120]}"
    )

print("\nall qids:", sorted(ov["widened_conclusions"].keys()))

import json
from collections import Counter

TAB = "evaluation/out/ceiling_v5/full_review_tabulation.json"
ARMS = ("C-O3", "D2", "D3", "E1")

with open(TAB, encoding="utf-8") as f:
    data = json.load(f)

recs = data["records"]
em = [r for r in recs if r["verdict"] == "evaluator_miss"]

resolved, remaining = [], []
for r in em:
    mach = r.get("machine") or {}
    best = max(float((mach.get(a) or {}).get("v2_soft") or 0.0) for a in ARMS)
    any_v2 = any((mach.get(a) or {}).get("v2") for a in ARMS)
    row = {
        "qid": r["qid"],
        "category": r.get("category"),
        "best_v2_soft": round(best, 4),
        "any_v2": any_v2,
        "notes": r.get("notes"),
    }
    (resolved if any_v2 else remaining).append(row)

print("evaluator_miss total:", len(em))
print("  any-arm v2 correct :", len(resolved))
print("  remaining (no arm) :", len(remaining))
print("remaining categories:", Counter(r["category"] for r in remaining))
print("\n--- remaining, sorted by soft desc ---")
for r in sorted(remaining, key=lambda x: -x["best_v2_soft"]):
    print(f"{r['qid']} cat={r['category']} soft={r['best_v2_soft']:.4f}")
    print(f"    {r['notes'][:300]}")
