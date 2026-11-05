"""A/B/C/D experiment: which factor(s) fix provision_check generation failures?

Conditions (identical chunks, model, evidence set — only the mitigation differs):
  A  baseline          no mitigation enabled
  B  provision_id      RAG_ANSWER_CONTRACT + RAG_ANSWER_CONTRACT_REPAIR
  C  citation_gate     RAG_CITATION_BEFORE_GENERATION
  D  two_pass         ENABLE_STRUCTURED_REASONER
  E  all_three        B + C + D combined

Reports binary_correct, answer_correctness, citation_recall, provision_correct,
and hallucination_detected per condition.

Usage:
    python -m evaluation.ab_provision_check --limit 0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Semaphore
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)
os.environ["RAG_USE_STUB_LLM"] = "false"

from evaluation.answer_scoring import score_answer
from evaluation.bench_p0_1_packing import RAW_DIR, _chunks_for, _load_jsonl, _load_payload_index
from evaluation.benchmark import load_questions
from evaluation.llm_ssl_client import SSLBypassLLMClient
from evaluation.gold_text_match import matches_gold_enhanced
from evaluation.resolution import FamilyMap, matches_gold

OUT_FILE = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "provision_check_ab.json"
TABULATION = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5" / "full_review_tabulation.json"
ARM = "C_hybrid"
MAX_WORKERS = 3

# The 21 provision_check generation-failure QIDs
TARGET_QIDS = [
    "Q007", "Q010", "Q014", "Q015", "Q016", "Q018", "Q020",
    "Q031", "Q032", "Q049", "Q051", "Q058", "Q073", "Q076",
    "Q078", "Q114", "Q117", "Q120", "Q125", "Q127", "Q129",
]

CONDITIONS = {
    "A_baseline": {},
    "B_provision_id": {"answer_contract": True, "answer_contract_repair": True},
    "C_citation_gate": {"citation_before_generation": True},
    "D_two_pass": {"structured_reasoner": True},
    "E_all_three": {
        "answer_contract": True,
        "answer_contract_repair": True,
        "citation_before_generation": True,
        "structured_reasoner": True,
    },
}


def _set_flags(flags: dict[str, bool]) -> None:
    """Apply feature flags to cfg."""
    from app.shared.config import cfg
    cfg.answer_contract = flags.get("answer_contract", False)
    cfg.answer_contract_repair = flags.get("answer_contract_repair", False)
    cfg.citation_before_generation = flags.get("citation_before_generation", False)
    cfg.structured_reasoner = flags.get("structured_reasoner", False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    payload_index = _load_payload_index()
    questions = {q.question_id: q for q in load_questions()}
    family_map = FamilyMap()
    arms = {
        r["question_id"]: [str(c) for c in (r.get("chunk_ids") or [])]
        for r in _load_jsonl(RAW_DIR / f"{ARM}.jsonl")
    }

    tasks = []
    for qid in TARGET_QIDS:
        q, ids = questions.get(qid), arms.get(qid)
        if not q or not ids:
            continue
        chunks = _chunks_for(ids, payload_index)
        if len(chunks) < 2:
            continue
        tasks.append((qid, q, chunks))

    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        print("no tasks", file=sys.stderr)
        return 1

    print(f"Provision-check A/B on {len(tasks)} questions x {len(CONDITIONS)} conditions", flush=True)

    results: dict[str, list[dict[str, Any]]] = {c: [] for c in CONDITIONS}

    for cond_name, flags in CONDITIONS.items():
        _set_flags(flags)
        print(f"\n=== Condition {cond_name} ===", flush=True)

        from app.rag.generation.grounded_service import GroundedGenerationService

        sem = Semaphore(MAX_WORKERS)

        def one(task):
            qid, q, chunks = task
            qt = q.question_types[0] if q.question_types else ""
            built = None
            from app.rag.generation.context_builder import ContextBuilder
            built = ContextBuilder(query_type=qt).build(q.question, chunks, qt)
            with sem:
                svc = GroundedGenerationService()
                svc.llm_client = SSLBypassLLMClient(model=os.environ["RAG_LLM_MODEL"])
                sys_p, user_p = svc._render_prompt(q.question, built, query_type=qt)
                llm = svc._call_llm(sys_p, user_p)
                answer = getattr(llm, "text", "") or ""
                tracked = svc._extract_citations(llm, chunks, built)
                san = svc.sanitizer.sanitize(answer, tracked, chunks)

                # Check provision correctness
                cited_ids = [c.chunk_id for c in tracked]
                gold_covered = set()
                for cid in cited_ids:
                    payload = payload_index.get(str(cid))
                    if payload is None:
                        continue
                    for u in q.recall_units():
                        from evaluation.resolution import matches_gold
                        if matches_gold(payload, u, family_map, text_fallback=True):
                            gold_covered.add(u.provision_id)
                provision_correct = any(u.provision_id in gold_covered for u in q.relevant_units())

                m = score_answer(answer, q.acceptable_conclusion or "", q.insufficient_evidence)
                return {
                    "qid": qid,
                    "answer": answer,
                    "binary_correct": int(m.get("binary_correct", 0)),
                    "answer_correctness": m.get("answer_correctness", 0.0),
                    "citation_recall": m.get("citation_recall", 0.0),
                    "provision_correct": int(provision_correct),
                    "hallucination_detected": int(san.hallucination_detected),
                    "groundedness_score": san.groundedness_score,
                    "abstained": int(san.abstained),
                }

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(one, t): t for t in tasks}
            for f in as_completed(futs):
                results[cond_name].append(f.result())

    # Report
    print("\n" + "=" * 80)
    print(f"Provision-check A/B  n={len(tasks)}  model={os.environ.get('RAG_LLM_MODEL')}")
    print("=" * 80)

    metrics = ["binary_correct", "answer_correctness", "citation_recall",
               "provision_correct", "hallucination_detected", "groundedness_score", "abstained"]

    header = f"{'metric':<25}" + "".join(f"{c:>18}" for c in CONDITIONS)
    print(header)
    print("-" * len(header))

    for metric in metrics:
        vals = {c: sum(r[metric] for r in results[c]) / len(results[c]) for c in CONDITIONS}
        row = f"{metric:<25}" + "".join(f"{vals[c]:>18.4f}" for c in CONDITIONS)
        print(row)

    # Per-condition provision_correct count
    print("\nProvision correct counts:")
    for c in CONDITIONS:
        pc = sum(r["provision_correct"] for r in results[c])
        print(f"  {c}: {pc}/{len(results[c])}")

    # Paired comparison: baseline vs each condition
    print("\nPaired binary (vs baseline):")
    for c in CONDITIONS:
        if c == "A_baseline":
            continue
        improved = regressed = 0
        for r_base in results["A_baseline"]:
            r_cond = next(r for r in results[c] if r["qid"] == r_base["qid"])
            if r_cond["binary_correct"] > r_base["binary_correct"]:
                improved += 1
            elif r_cond["binary_correct"] < r_base["binary_correct"]:
                regressed += 1
        print(f"  {c}: improved={improved}, regressed={regressed}, net={improved-regressed}")

    out = {
        "n": len(tasks),
        "conditions": {c: results[c] for c in CONDITIONS},
        "summary": {
            c: {m: sum(r[m] for r in results[c]) / len(results[c]) for m in metrics}
            for c in CONDITIONS
        },
    }
    Path(OUT_FILE).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwritten: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
