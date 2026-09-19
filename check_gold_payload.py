import sys, json

sys.path.insert(0, r"C:\github\NSA_webservice")
from evaluation.benchmark import load_questions
from evaluation.resolution import FamilyMap, matches_gold
from evaluation.config import CACHE_DIR

payload_index = {}
with open(CACHE_DIR / "payload_index.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rec = json.loads(line)
            payload_index[rec["id"]] = rec["payload"]

family_map = FamilyMap()
questions = {q.question_id: q for q in load_questions()}
q = questions["Q001"]

for unit in q.relevant_units():
    for pid, payload in payload_index.items():
        if matches_gold(payload, unit, family_map):
            print(f"Gold chunk: {pid}")
            ct = payload.get("chunk_text", "") or payload.get("text", "")
            print(f"  chunk_text length: {len(ct)}")
            print(f"  chunk_text (first 300): {ct[:300]}")
            print(f"  section_number: {payload.get('section_number')}")
            print(f"  document_title: {payload.get('document_title', '')[:80]}")
            print(f"  act_name: {payload.get('act_name', '')[:80]}")
            print(f"  document_type: {payload.get('document_type', '')}")
            print(f"  authority: {payload.get('authority', '')}")
            print(f"  chunk_index: {payload.get('chunk_index')}")
            break
