"""Scratch: structure of raw per-arm jsonl + KG fusion A/B."""

import json
from pathlib import Path

OUT = Path("evaluation/out/ceiling_v5")
print("raw files:", sorted(p.name for p in (OUT / "raw").glob("*")))

for name in ("D_kg.jsonl", "C_hybrid.jsonl"):
    p = OUT / "raw" / name
    if not p.exists():
        continue
    first = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    print(f"\n--- {name}: keys={sorted(first.keys())}")
    for k, v in first.items():
        print(f"    {k}: {repr(v)[:170]}")

kg = OUT / "kg_fusion_ab.json"
if kg.exists():
    d = json.loads(kg.read_text(encoding="utf-8"))
    print("\n--- kg_fusion_ab.json keys:", list(d.keys()))
    print(json.dumps(d, indent=1)[:2500])
