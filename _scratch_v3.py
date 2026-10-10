"""Scratch: inspect evaluator-v3 triage / candidates state."""

import json
from pathlib import Path

OUT = Path("evaluation/out/ceiling_v5")

for name in ("evaluator_v3_triage.json", "evaluator_v3_candidates.json", "evaluator_v3_component_rule.json"):
    p = OUT / name
    if not p.exists():
        print(f"--- {name}: MISSING")
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"--- {name}: keys={list(d.keys())}")
    if "summary" in d:
        print("   summary:", json.dumps(d["summary"]))
    for k, v in d.items():
        if isinstance(v, list):
            print(f"   {k}: list n={len(v)}")
        elif isinstance(v, dict):
            print(f"   {k}: dict keys={list(v.keys())[:8]}")

t = json.loads((OUT / "evaluator_v3_triage.json").read_text(encoding="utf-8"))
for bucket, items in (t.get("buckets") or {}).items():
    print(f"\n[{bucket}] n={len(items)}")
    for it in items:
        print("   ", it["qid"], "| cat=", it.get("category"), "| soft=", it.get("v2_soft_by_arm"))
