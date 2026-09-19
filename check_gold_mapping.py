import json
import sys

sys.path.insert(0, r"C:\github\NSA_webservice")

with open(r"C:\github\NSA_webservice/evaluation/out/cache/payload_index.jsonl", encoding="utf-8") as f:
    for i, line in enumerate(f):
        rec = json.loads(line.strip())
        rid = rec["id"]
        p = rec["payload"]
        cid = p.get("chunk_id", "")
        print(f"Index ID: {rid[:80]}")
        print(f"  payload.chunk_id: {cid[:80]}")
        print(f"  section_number: {p.get('section_number')}")
        print(f"  act_name: {p.get('act_name', '')[:60]}")
        txt = p.get("chunk_text", "") or p.get("text", "")
        print(f"  chunk_text (first 80): {txt[:80]}")
        if i >= 4:
            break

# Now check how matches_gold resolves these
from evaluation.resolution import FamilyMap, matches_gold

family_map = FamilyMap()
print(f"\nFamilyMap loaded: {len(family_map._by_family) if hasattr(family_map, '_by_family') else 'unknown'} families")

# Check a specific gold unit
from evaluation.benchmark import load_questions

questions = load_questions()
q0 = questions[0]
gold_unit = q0.gold_units[0]
print(
    f"\nQ001 gold unit: provision_id={gold_unit.provision_id}, family={gold_unit.family}, section={gold_unit.section}, act={gold_unit.act}"
)

# Find a payload that matches this gold unit
with open(r"C:\github\NSA_webservice/evaluation/out/cache/payload_index.jsonl", encoding="utf-8") as f:
    for line in f:
        rec = json.loads(line.strip())
        p = rec["payload"]
        if matches_gold(p, gold_unit, family_map):
            print(f"MATCH found! payload id={rec['id'][:60]}, chunk_id={p.get('chunk_id')[:60]}")
            print(f"  section_number={p.get('section_number')}, act_name={p.get('act_name')[:50]}")
            break
