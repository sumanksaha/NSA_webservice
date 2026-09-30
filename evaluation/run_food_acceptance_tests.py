"""Acceptance tests 1-6 (task spec) - entity != information request upgrade.

Runs the shipped configuration (all RAG_FOOD_* flags on, i.e. arm S4_full)
live against Qdrant and checks each acceptance criterion:

  T1  definition ask          "What is cumin?"                  -> definition head top-1
  T2  standard ask            "What is the standard for cumin?" -> clause 2.9.8 standard row top-1 (not definition-only)
  T3  parameter-specific ask  "moisture limit for cumin"        -> clause 2.9.8 limit row top-1
  T4  disambiguation          benchmark v1.1 disambig = 1.00    -> definition never outranks rows
  T5  non-standard provision  "procedure for sampling"          -> sampling provision, not a standard
  T6  compliance ask          "12.0 percent moisture comply?"   -> standard + row evidence in top-5

T1-T3 gold sets are read from the frozen benchmark v1.1 records (FI001-FI003)
so the acceptance run and the ablation agree on what "correct" means.

Usage:
    python -m evaluation.run_food_acceptance_tests
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

BENCHMARK = PROJECT_ROOT / "benchmark" / "benchmark_food_intent_v1.1.jsonl"


def _load_gold() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(BENCHMARK, encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            out[rec["question_id"]] = rec
    return out


def _short(ids: list[str] | list[str]) -> set[str]:
    return {str(i)[:8] for i in ids}


def main() -> int:
    from app import create_app

    gold = _load_gold()
    app = create_app()
    results: list[tuple[str, str, bool, str]] = []
    with app.app_context():
        from app.rag.tasks import run_retrieval_pipeline

        def run(q: str) -> list[dict]:
            out = run_retrieval_pipeline(query=q, top_k=20)
            return out.get("chunks", [])

        def ids(chunks: list[dict]) -> list[str]:
            return [str(c.get("chunk_id", ""))[:8] for c in chunks]

        # T1 - definition ask: the clause-lead definition head itself.
        chunks = run(gold["FI001"]["question"])
        top1 = ids(chunks)[:1]
        t1_gold = _short(gold["FI001"]["gold_top1_chunks"])
        t1_ok = bool(top1) and top1[0] in t1_gold
        results.append(("T1", "definition ask -> definition head top-1", t1_ok, f"top1={top1}"))

        # T2 - standard ask: a clause 2.9.8 standard/limit row at top-1.
        chunks = run(gold["FI002"]["question"])
        top1 = ids(chunks)[:1]
        t2_gold = _short(gold["FI002"]["gold_top1_chunks"])
        t2_ok = bool(top1) and top1[0] in t2_gold
        results.append(("T2", "standard ask -> cumin standard row top-1", t2_ok, f"top1={top1}"))

        # T3 - parameter-specific ask: a clause 2.9.8 limit row at top-1.
        chunks = run(gold["FI003"]["question"])
        top1 = ids(chunks)[:1]
        t3_gold = _short(gold["FI003"]["gold_top1_chunks"])
        t3_ok = bool(top1) and top1[0] in t3_gold
        results.append(("T3", "parameter ask -> cumin limit row top-1", t3_ok, f"top1={top1}"))

        # T4 - definition-vs-standard disambiguation across benchmark v1.1.
        from evaluation.config import CACHE_DIR
        from evaluation.food_intent_metrics import evaluate_food_intent, load_food_intent_questions

        doc_map: dict[str, str] = {}
        idx = CACHE_DIR / "payload_index.jsonl"
        with open(idx, encoding="utf-8") as fh:
            for line in fh:
                rec = json.loads(line)
                d = str((rec.get("payload") or {}).get("document_id", "") or "")
                if d:
                    doc_map[rec["id"]] = d
        questions = load_food_intent_questions()
        ranked: dict[str, list[str]] = {}
        for q in questions:
            out = run_retrieval_pipeline(query=q["question"], top_k=20)
            ranked[q["question_id"]] = [c["chunk_id"] for c in out.get("chunks", []) if c.get("chunk_id")]
        report = evaluate_food_intent(ranked, questions, doc_for_chunk=doc_map)
        t4_ok = report["disambiguation_accuracy"] == 1.0
        results.append(
            (
                "T4",
                "benchmark disambiguation = 1.00 (definition never outranks rows)",
                t4_ok,
                f"disambig={report['disambiguation_accuracy']:.2f}",
            )
        )

        # T5 - sampling ask must retrieve a sampling provision (not a standard).
        chunks = run("What is the procedure for sampling?")
        texts = " || ".join(str(c.get("text", ""))[:200] for c in chunks[:5]).lower()
        t5_ok = bool(
            re.search(r"\bsampl(?:e|es|ing)\b", texts)
            and re.search(r"\bprocedure\b|\bmanner\b|\bseal", texts)
        )
        results.append(("T5", "sampling ask -> sampling provision in top-5", t5_ok, texts[:120]))

        # T6 - compliance ask: clause 2.9.8 evidence (heading or rows) in top-5.
        chunks = run(gold["FI013"]["question"])
        top5 = set(ids(chunks[:5]))
        t6_gold = _short(gold["FI013"]["gold_source_chunks"])
        t6_ok = bool(top5 & t6_gold)
        results.append(("T6", "compliance ask -> cumin clause evidence in top-5", t6_ok, f"top5={sorted(top5)}"))

    print("\n=== Acceptance tests (S4_full live) ===")
    all_ok = True
    for tid, desc, ok, detail in results:
        all_ok &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {tid}  {desc}  [{detail}]")
    print("ALL PASS" if all_ok else "FAILURES PRESENT")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
