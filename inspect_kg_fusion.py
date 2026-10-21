"""Inspect what the KG actually fused for given QIDs (offline, 0 LLM calls).

For each QID: baseline chunk_ids from retrieval.jsonl, expansion provisions
from KGContextExpander, converted chunks, fused result, and the question's
gold units. Used to autopsy live pairs where KG fusion moved soft a lot
(Q014 -0.157, Q016 +0.064 flip) -- was the fused content gold, near-gold,
or noise?

Usage: python inspect_kg_fusion.py Q014 Q016
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# NOTE: this file lives at repo root (not evaluation/), so root is .parent,
# not .parent.parent like the evaluation/ harnesses.
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUT = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "reeval_89"


def main() -> int:
    qids = sys.argv[1:] or ["Q014", "Q016"]
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    from app import create_app
    app = create_app()
    with app.app_context():
        from evaluation.benchmark import load_questions
        from evaluation.bench_p0_1_packing import _chunks_for, _load_payload_index
        from kg.hybrid import KGContextExpander, provisions_to_retrieved_chunks, rrf_fuse_chunks

        questions = {q.question_id: q for q in load_questions()}
        payload_index = _load_payload_index()
        retr = {json.loads(l)["qid"]: json.loads(l)
                for l in (OUT / "retrieval.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()}
        expander = KGContextExpander()

        for qid in qids:
            q = questions[qid]
            row = retr[qid]
            ids = list((row.get("C_hybrid") or {}).get("chunk_ids") or [])[:20]
            chunks = _chunks_for(ids, payload_index)
            exp = expander.expand_chunks([c.chunk_id for c in chunks])
            provs = exp.get("provisions") or []
            kg_chunks = provisions_to_retrieved_chunks(provs, limit=5)
            fused = rrf_fuse_chunks([chunks, kg_chunks], rrf_k=60.0, top_k=20)
            n_kg_in = sum(1 for c in fused if (c.document_type or "") == "KG-Provision")
            print(f"=== {qid} ===")
            print(f"gold units: {[(u.family, u.section, u.provision_id) for u in q.recall_units()]}")
            print(f"expansion: matched={exp.get('matched_chunks')} provs={len(provs)} "
                  f"-> {len(kg_chunks)} kg_chunks, {n_kg_in} survive fusion in top-20")
            for kc in [c for c in fused if (c.document_type or "") == "KG-Provision"]:
                print(f"  KG-IN : sec={kc.section_number!r} doc={kc.document_title!r}")
                print(f"          {kc.text[:160]!r}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
