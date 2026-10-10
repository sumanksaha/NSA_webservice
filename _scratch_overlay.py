"""Temporary scratch: inspect evaluator_v2_overlay.json shape + audit counts."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

ov = json.loads((ROOT / "evaluation" / "evaluator_v2_overlay.json").read_text(encoding="utf-8"))
print("== overlay top-level keys:", list(ov.keys()))
for k, v in ov.items():
    print(f"  {k}: {type(v).__name__} len={len(v) if isinstance(v, (list, dict)) else v}")

first = next(iter((ov.get("questions") or ov).items())) if isinstance(ov, dict) else None
print("\n== sample entry:")
print(json.dumps(first, indent=1, ensure_ascii=False)[:1200])

# The tabulation: how many evaluator_miss remain unresolved under v2?
tab_path = ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
if tab_path.exists():
    tab = json.loads(tab_path.read_text(encoding="utf-8"))
    recs = tab.get("records") or []
    from collections import Counter

    print("\n== tabulation verdicts:", Counter(r.get("verdict") for r in recs))
    em = [r for r in recs if r.get("verdict") == "evaluator_miss"]
    print("evaluator_miss total:", len(em))
    ARMS = ("C-O3", "D2", "D3", "E1")
    unresolved = []
    for r in em:
        m = r.get("machine") or {}
        if not any((m.get(a) or {}).get("v2") for a in ARMS):
            unresolved.append(r.get("qid"))
    print("still unresolved under v2:", len(unresolved))
    print("qids:", unresolved)
    print("\n== record keys:", sorted(em[0].keys()) if em else None)
    if em:
        print("machine keys:", sorted((em[0].get("machine") or {}).keys()))
        print("sample record:", json.dumps(em[0], indent=1, ensure_ascii=False)[:1200])
