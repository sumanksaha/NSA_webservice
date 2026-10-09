import json
import time
from pathlib import Path

p = Path("evaluation/out/cache/payload_index.jsonl")
n = 0
with open(p, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            n += 1
print("payload entries:", n)

import sys

sys.path.insert(0, "evaluation")
from resolution import FamilyMap, matches_gold

from benchmark import GoldUnit

fm = FamilyMap()
u = GoldUnit(
    provision_id="fssai:s63",
    family="fssai",
    section="63",
    act="Food Safety and Standards Act, 2006",
    collection=None,
    document_id=None,
    gain=2.0,
    role="primary",
)
index = {}
start = time.time()
with open(p, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            rec = json.loads(line)
            index[rec["id"]] = rec["payload"]
print("loaded in", round(time.time() - start, 2), "s")
t0 = time.time()
hits = 0
for pid, payload in index.items():
    if matches_gold(payload, u, fm):
        hits += 1
print("hits:", hits, "in", round(time.time() - t0, 2), "s")
