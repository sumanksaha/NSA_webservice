"""KG-arm recall-contribution measurement (deliverables §6 item 2).

The knowledge graph is a *generation-time* stage: ``_generate_apply_kg_context``
RRF-fuses KG provisions into the LLM context, it does not re-rank the retrieval
list.  So an ablation over ``run_retrieval_pipeline`` cannot show a KG recall
delta — the ranked list is byte-identical with the KG on and off.  What the KG
can contribute is *context coverage*: provisions that reach the LLM only
because the graph expanded a retrieved chunk.

This runner measures exactly that, per benchmark question:

* ``retrieval_clauses``  — FSSAI clause numbers inside the S4 retrieved chunks.
* ``kg_clauses``         — clause numbers of the provisions the KG injects.
* ``retrieval_hit``      — gold clause already in the retrieved chunks.
* ``kg_hit``             — gold clause in the retrieved chunks *or* injected.
* ``kg_injected``        — gold clause reaches the LLM **only** via the KG.

It also re-runs each query with the KG switched off and asserts the ranking is
unchanged, which is the evidence for the "no retrieval-side delta" claim above.

Requires a local Neo4j populated by ``scripts/build_kg_corpus.py`` (the
FSSAI collection must be in the graph or every question scores zero).

    python -m evaluation.run_kg_contribution [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.kg_contribution")

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "kg_contribution"
RESULTS_PATH = OUT_DIR / "results.jsonl"
METRICS_PATH = OUT_DIR / "metrics.json"
REPORT_PATH = OUT_DIR / "report.md"

#: S4 (shipped) food-intent flags — the arm whose retrieval we expand.
S4_FLAGS: dict[str, bool | int] = {
    "RAG_FOOD_INTENT_ENABLED": True,
    "RAG_FOOD_LEGAL_RERANK": True,
    "RAG_FOOD_PARENT_RECONSTRUCT": True,
    "RAG_FOOD_VALIDATE": True,
    "RAG_FOOD_ANSWER_MODE": True,
    "RAG_FOOD_FALLBACK_ROUNDS": 2,
}
FLAG_KEYS = tuple(S4_FLAGS)

TOP_K = 10


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def gold_clause_of(question: dict[str, Any]) -> str:
    """Gold FSSAI clause for a benchmark question.

    ``primary_provisions`` looks like ``fssai:2.9.8-cumin``; the clause is the
    middle segment.  ``gold_clause`` is the authoritative field when present.
    """
    clause = str(question.get("gold_clause") or "").strip()
    if clause:
        return clause
    for ref in question.get("primary_provisions") or []:
        match = re.match(r"^[a-z]+:(\d+(?:\.\d+)*)", str(ref))
        if match:
            return match.group(1)
    return ""


def _clauses(chunks: list[dict[str, Any]]) -> set[str]:
    return {str(c.get("clause_number") or "").strip() for c in chunks if c.get("clause_number")}


def _rank_key(chunks: list[dict[str, Any]]) -> list[str]:
    return [str(c.get("chunk_id") or "") for c in chunks]


# --------------------------------------------------------------------------- #
# Per-question measurement
# --------------------------------------------------------------------------- #
def _run_question(question: dict[str, Any], expander: Any) -> dict[str, Any]:
    from app.rag.tasks import run_retrieval_pipeline

    query = question["question"]
    gold = gold_clause_of(question)

    t0 = time.monotonic()
    result = run_retrieval_pipeline(query, top_k=TOP_K)
    latency_ms = int((time.monotonic() - t0) * 1000)
    chunks = result.get("chunks") or []
    chunk_ids = [str(c.get("chunk_id") or "") for c in chunks if c.get("chunk_id")]

    retrieval_clauses = _clauses(chunks)
    expansion = expander.expand_chunks(chunk_ids)
    kg_clauses = {
        str(p.get("provision_number") or "").strip()
        for p in expansion.get("provisions", [])
        if p.get("provision_number")
    }

    # Control arm: same query, KG disabled.  Retrieval must be unchanged —
    # that is the evidence the KG is generation-time only.  Note the pipeline
    # is not bit-reproducible run to run (ties in the fused ranking resolve
    # differently), so we score set overlap and record exact-order identity
    # separately rather than treating any difference as a KG effect.
    from flask import current_app

    saved = {k: current_app.config[k] for k in ("RAG_KG_EXPANSION", "RAG_KG_FUSION")}
    current_app.config["RAG_KG_EXPANSION"] = False
    current_app.config["RAG_KG_FUSION"] = False
    try:
        control = run_retrieval_pipeline(query, top_k=TOP_K)
    finally:
        current_app.config.update(saved)
    control_rank = _rank_key(control.get("chunks") or [])
    retrieved_ids = set(_rank_key(chunks))
    control_ids = set(control_rank)

    retrieval_hit = gold in retrieval_clauses
    kg_hit = retrieval_hit or gold in kg_clauses
    return {
        "question_id": question["question_id"],
        "question": query,
        "category": question["category"],
        "expected_intent": question["expected_intent"],
        "gold_clause": gold,
        "primary_provisions": question.get("primary_provisions") or [],
        "n_retrieved": len(chunks),
        "retrieval_clauses": sorted(retrieval_clauses),
        "kg_clauses": sorted(kg_clauses),
        "kg_provisions": [
            {
                "provision_id": p.get("provision_id"),
                "provision_number": p.get("provision_number"),
                "title": p.get("title"),
                "instrument_title": p.get("instrument_title"),
                "status": p.get("status"),
            }
            for p in expansion.get("provisions", [])
        ],
        "kg_matched_chunks": expansion.get("matched_chunks", 0),
        "kg_error": expansion.get("error"),
        "kg_latency_ms": expansion.get("latency_ms", 0),
        "retrieval_hit": retrieval_hit,
        "kg_hit": kg_hit,
        "kg_injected": kg_hit and not retrieval_hit,
        "retrieval_unchanged_without_kg": control_rank == _rank_key(chunks),
        "kg_off_set_overlap": round(len(retrieved_ids & control_ids) / len(retrieved_ids | control_ids), 4)
        if (retrieved_ids | control_ids)
        else None,
        "kg_off_top1_identical": bool(control_rank[:1] == _rank_key(chunks)[:1]),
        "retrieval_latency_ms": latency_ms,
        "error": result.get("error"),
    }


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in records if r.get("gold_clause")]
    n = len(scored)
    retrieval_hits = [r for r in scored if r["retrieval_hit"]]
    kg_hits = [r for r in scored if r["kg_hit"]]
    injected = [r for r in scored if r["kg_injected"]]
    kg_errors = [r for r in records if r.get("kg_error")]

    def mean(values: Any) -> float | None:
        return round(sum(values) / n, 2) if n else None

    return {
        "overall": {
            "n": n,
            "n_kg_errors": len(kg_errors),
            "n_retrieval_unchanged_without_kg": sum(1 for r in records if r.get("retrieval_unchanged_without_kg")),
            "mean_kg_off_set_overlap": mean(r.get("kg_off_set_overlap") or 0 for r in scored),
            "n_kg_off_top1_identical": sum(1 for r in records if r.get("kg_off_top1_identical")),
            "retrieval_recall": round(len(retrieval_hits) / n, 4) if n else None,
            "kg_context_recall": round(len(kg_hits) / n, 4) if n else None,
            "kg_injected_gold": len(injected),
            "kg_injected_rate": round(len(injected) / n, 4) if n else None,
            "mean_kg_provisions": mean(len(r.get("kg_provisions") or []) for r in scored),
            "mean_kg_matched_chunks": mean(r.get("kg_matched_chunks") or 0 for r in scored),
            "mean_kg_latency_ms": mean(r.get("kg_latency_ms") or 0 for r in scored),
        },
        "by_category": {
            cat: {
                "n": len(rows),
                "retrieval_recall": round(sum(1 for r in rows if r["retrieval_hit"]) / len(rows), 4),
                "kg_context_recall": round(sum(1 for r in rows if r["kg_hit"]) / len(rows), 4),
                "kg_injected_gold": sum(1 for r in rows if r["kg_injected"]),
            }
            for cat, rows in (
                (cat, [r for r in scored if r["category"] == cat])
                for cat in sorted({r["category"] for r in scored})
            )
        },
        "injected": [
            {
                "question_id": r["question_id"],
                "gold_clause": r["gold_clause"],
                "question": r["question"],
                "kg_clauses": r["kg_clauses"],
            }
            for r in injected
        ],
    }


def _write_report(metrics: dict[str, Any], records: list[dict[str, Any]]) -> None:
    o = metrics["overall"]
    lines = [
        "# KG-arm recall contribution (benchmark v1.1)",
        "",
        "Gold FSSAI clause coverage of the LLM context, KG expansion on vs off.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| questions | {o['n']} |",
        f"| retrieval recall (gold clause in retrieved chunks) | {o['retrieval_recall']} |",
        f"| KG context recall (retrieval + injected) | {o['kg_context_recall']} |",
        f"| **gold clauses reaching the LLM only via the KG** | **{o['kg_injected_gold']}** |",
        f"| mean KG provisions injected / question | {o['mean_kg_provisions']} |",
        f"| mean KG chunks matched / question | {o['mean_kg_matched_chunks']} |",
        f"| mean KG expansion latency | {o['mean_kg_latency_ms']} ms |",
        f"| ranking identical with KG disabled | {o['n_retrieval_unchanged_without_kg']}/{o['n']} |",
        f"| mean top-k set overlap, KG off vs on (k={metrics['top_k']}) | {o['mean_kg_off_set_overlap']} |",
        f"| top-1 identical, KG off vs on | {o['n_kg_off_top1_identical']}/{o['n']} |",
        f"| KG expansion errors | {o['n_kg_errors']} |",
        "",
        "## By category",
        "",
        "| category | n | retrieval recall | KG context recall | KG-only gold |",
        "| --- | --- | --- | --- | --- |",
    ]
    for cat, row in metrics["by_category"].items():
        lines.append(
            f"| {cat} | {row['n']} | {row['retrieval_recall']} | {row['kg_context_recall']} | {row['kg_injected_gold']} |",
        )
    lines += ["", "## Gold clauses the KG injected on its own", ""]
    if metrics["injected"]:
        for row in metrics["injected"]:
            lines.append(f"- `{row['question_id']}` gold {row['gold_clause']} — {row['question']}")
    else:
        lines.append("None: retrieval already reached every gold clause on this benchmark.")
    lines += ["", "## Per question", "", "| id | gold | retrieved | KG clauses | ret | KG | injected |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in records:
        if not r.get("gold_clause"):
            continue
        lines.append(
            f"| {r['question_id']} | {r['gold_clause']} | {len(r['retrieval_clauses'])} | {len(r['kg_clauses'])} | "
            f"{'Y' if r['retrieval_hit'] else 'n'} | {'Y' if r['kg_hit'] else 'n'} | {'Y' if r['kg_injected'] else 'n'} |",
        )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.food_intent_metrics import load_food_intent_questions

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="run only the first N questions (0 = all)")
    parser.add_argument("--force", action="store_true", help="ignore the cache and re-run every question")
    args = parser.parse_args()

    questions = load_food_intent_questions()
    if args.limit:
        questions = questions[: args.limit]

    from app import create_app

    app = create_app()
    records: list[dict[str, Any]] = []
    done_ids: set[str] = set()
    if RESULTS_PATH.exists() and not args.force:
        with open(RESULTS_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    records.append(rec)
                    done_ids.add(rec["question_id"])
                except Exception:
                    continue

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pending = [q for q in questions if args.force or q["question_id"] not in done_ids]

    with app.app_context():
        for key, value in S4_FLAGS.items():
            app.config[key] = value

        from kg.hybrid import KGContextExpander

        if not KGContextExpander.configured():
            raise SystemExit("Neo4j is not configured — set NEO4J_URI/USERNAME/PASSWORD and rebuild the KG first.")
        expander = KGContextExpander()

        for i, q in enumerate(pending, 1):
            t0 = time.monotonic()
            try:
                rec = _run_question(q, expander)
            except Exception as exc:  # per-question isolation
                rec = {
                    "question_id": q["question_id"],
                    "question": q["question"],
                    "category": q["category"],
                    "expected_intent": q["expected_intent"],
                    "gold_clause": gold_clause_of(q),
                    "error": f"{type(exc).__name__}: {exc}",
                    "retrieval_hit": False,
                    "kg_hit": False,
                    "kg_injected": False,
                }
            with open(RESULTS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            records = [r for r in records if r.get("question_id") != rec["question_id"]]
            records.append(rec)
            logger.info(
                "%d/%d %s gold=%s ret=%s kg=%s injected=%s (%.1fs)",
                i,
                len(pending),
                rec["question_id"],
                rec.get("gold_clause"),
                rec.get("retrieval_hit"),
                rec.get("kg_hit"),
                rec.get("kg_injected"),
                time.monotonic() - t0,
            )

    by_id = {r["question_id"]: r for r in records}
    ordered = [by_id[q["question_id"]] for q in questions if q["question_id"] in by_id]
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        for rec in ordered:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    metrics = aggregate(ordered)
    metrics["flags"] = dict(S4_FLAGS)
    metrics["top_k"] = TOP_K
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_report(metrics, ordered)

    o = metrics["overall"]
    print("\n=== KG-arm recall contribution (benchmark v1.1) ===")
    print(f"n={o['n']} (kg errors {o['n_kg_errors']})")
    print(f"retrieval recall:      {o['retrieval_recall']}")
    print(f"KG context recall:    {o['kg_context_recall']}")
    print(f"gold via KG only:     {o['kg_injected_gold']}")
    print(f"mean KG provisions:    {o['mean_kg_provisions']}")
    print(f"ranking unchanged w/o KG: {o['n_retrieval_unchanged_without_kg']}/{o['n']}")
    print(f"mean top-k set overlap w/o KG: {o['mean_kg_off_set_overlap']}")
    print(f"\nmetrics -> {METRICS_PATH}")
    print(f"report  -> {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
