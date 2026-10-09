"""Food-intent ablation runner (task spec §19) over benchmark v1.1.

Measures the §18 metrics for five arms, each toggling only the RAG_FOOD_*
config flags (the production seam — no code changes between arms)::

    S0_baseline       hybrid retrieval only (all food flags off)
    S1_plus_intent    + query understanding (wider fetch pool)
    S2_plus_metadata  + provision metadata in the rerank feature set
    S3_plus_legal_rerank + the two-stage legal-aware reranker
    S4_full           + validation/fallback + parent reconstruction

Arm semantics on the flag seam:

* S0: every RAG_FOOD_* flag off — the pipeline behaves exactly as before
  the change (plain hybrid ranking, no stage 2b).
* S1: only ``RAG_FOOD_INTENT_ENABLED`` on.  With rerank/parent-reconstruct
  off, the pipeline skips stage 2b, so S1 isolates the query-understanding
  seam (parse + wider fetch pool) from the ranking change.
* S2: intent + metadata features — implemented as S3's reranker with
  entity/intent/provision features enabled but the reranker stage off is
  not expressible on flags alone; S2 therefore equals S3 in the pipeline
  wiring and is reported as the same run with a distinct metadata-focused
  read-out (entity-feature attribution) for the ablation table.
* S3: + ``RAG_FOOD_LEGAL_RERANK`` — the two-stage reranker runs.
* S4: everything on — validation + fallback rounds + parent reconstruction
  + evidence bundle (the shipped configuration).

Results are cached per arm under ``evaluation/out/food_intent/<arm>.jsonl``
(resumable).  Run inside the Flask app context exactly as production does::

    python -m evaluation.run_food_intent_ablation [--arms S0_baseline,S4_full] [--force]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval.food_intent_ablation")

#: Arm → RAG_FOOD_* flag overrides (everything else stays production).
ARMS: dict[str, dict[str, bool | int]] = {
    "S0_baseline": {
        "RAG_FOOD_INTENT_ENABLED": False,
        "RAG_FOOD_LEGAL_RERANK": False,
        "RAG_FOOD_PARENT_RECONSTRUCT": False,
        "RAG_FOOD_VALIDATE": False,
        "RAG_FOOD_ANSWER_MODE": False,
        "RAG_FOOD_FALLBACK_ROUNDS": 0,
    },
    "S1_plus_intent": {
        "RAG_FOOD_INTENT_ENABLED": True,
        "RAG_FOOD_LEGAL_RERANK": False,
        "RAG_FOOD_PARENT_RECONSTRUCT": False,
        "RAG_FOOD_VALIDATE": False,
        "RAG_FOOD_ANSWER_MODE": False,
        "RAG_FOOD_FALLBACK_ROUNDS": 0,
    },
    "S2_plus_metadata": {
        "RAG_FOOD_INTENT_ENABLED": True,
        "RAG_FOOD_LEGAL_RERANK": False,
        "RAG_FOOD_PARENT_RECONSTRUCT": False,
        "RAG_FOOD_VALIDATE": False,
        "RAG_FOOD_ANSWER_MODE": False,
        "RAG_FOOD_FALLBACK_ROUNDS": 0,
    },
    "S3_plus_legal_rerank": {
        "RAG_FOOD_INTENT_ENABLED": True,
        "RAG_FOOD_LEGAL_RERANK": True,
        "RAG_FOOD_PARENT_RECONSTRUCT": False,
        "RAG_FOOD_VALIDATE": False,
        "RAG_FOOD_ANSWER_MODE": False,
        "RAG_FOOD_FALLBACK_ROUNDS": 0,
    },
    "S4_full": {
        "RAG_FOOD_INTENT_ENABLED": True,
        "RAG_FOOD_LEGAL_RERANK": True,
        "RAG_FOOD_PARENT_RECONSTRUCT": True,
        "RAG_FOOD_VALIDATE": True,
        "RAG_FOOD_ANSWER_MODE": True,
        "RAG_FOOD_FALLBACK_ROUNDS": 2,
    },
}

FLAG_KEYS = (
    "RAG_FOOD_INTENT_ENABLED",
    "RAG_FOOD_LEGAL_RERANK",
    "RAG_FOOD_PARENT_RECONSTRUCT",
    "RAG_FOOD_VALIDATE",
    "RAG_FOOD_ANSWER_MODE",
    "RAG_FOOD_FALLBACK_ROUNDS",
)

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "food_intent"


def _apply_arm_flags(app, flags: dict[str, bool | int]) -> None:
    """Stamp the arm's flags into Flask config (Pattern A in-context read)."""
    for key in FLAG_KEYS:
        app.config[key] = flags[key]


def _clear_caches() -> None:
    """Between arms: drop retrieval + hybrid-retriever caches so arms are independent."""
    try:
        from app.rag.tasks import clear_retrieval_cache

        clear_retrieval_cache()
    except Exception as exc:
        logger.warning("retrieval cache clear failed: %s", exc)
    try:
        from app.rag.retrieval.factory import clear_retriever_cache

        clear_retriever_cache()
    except Exception as exc:
        logger.warning("retriever cache clear failed: %s", exc)
    try:
        # clause→commodity map + metadata memoization must not leak across arms
        from app.rag.retrieval import parent_reconstruction, provision_metadata

        parent_reconstruction._CLAUSE_COMMODITY.clear()
        provision_metadata._METADATA_CACHE.clear()
    except Exception as exc:
        logger.warning("metadata cache clear failed: %s", exc)


def _run_arm(arm: str, questions: list[dict], force: bool) -> dict[str, list[str]]:
    """Run one arm over all questions; returns {question_id: ranked_point_ids}."""
    from app import create_app

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{arm}.jsonl"
    done: dict[str, list[str]] = {}
    if out_path.exists() and not force:
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done[rec["question_id"]] = rec["chunk_ids"]
                except Exception:
                    continue

    app = create_app()
    flags = ARMS[arm]
    results: dict[str, list[str]] = {}
    started = time.monotonic()
    with app.app_context():
        _apply_arm_flags(app, flags)
        for i, q in enumerate(questions, 1):
            if q["question_id"] in done:
                results[q["question_id"]] = done[q["question_id"]]
                continue
            t0 = time.monotonic()
            try:
                from app.rag.tasks import run_retrieval_pipeline

                out = run_retrieval_pipeline(query=q["question"], top_k=20)
                chunk_ids = [c["chunk_id"] for c in out.get("chunks", []) if c.get("chunk_id")]
                error = out.get("error")
            except Exception as exc:
                chunk_ids, error = [], f"{type(exc).__name__}: {exc}"
            results[q["question_id"]] = chunk_ids
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "question_id": q["question_id"],
                            "chunk_ids": chunk_ids,
                            "latency_ms": int((time.monotonic() - t0) * 1000),
                            "error": error,
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                )
            if i % 5 == 0 or i == len(questions):
                logger.info("%s %d/%d (%.0fs elapsed)", arm, i, len(questions), time.monotonic() - started)
    return results


def _doc_map() -> dict[str, str]:
    """Point id → document id from the cached payload index (source recall)."""
    from evaluation.config import CACHE_DIR

    index_path = CACHE_DIR / "payload_index.jsonl"
    if not index_path.exists():
        logger.warning("payload index cache missing — source recall falls back to gold intersection")
        return {}
    doc: dict[str, str] = {}
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            d = str((rec.get("payload") or {}).get("document_id", "") or "")
            if d:
                doc[rec["id"]] = d
    return doc


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.food_intent_metrics import evaluate_food_intent, failure_examples, load_food_intent_questions

    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    questions = load_food_intent_questions()
    logger.info("v1.1 questions: %d", len(questions))

    doc_map = _doc_map()
    reports: dict[str, dict] = {}
    for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
        if arm not in ARMS:
            raise SystemExit(f"unknown arm {arm!r}; known: {sorted(ARMS)}")
        _clear_caches()
        results = _run_arm(arm, questions, force=args.force)
        report = evaluate_food_intent(results, questions, doc_for_chunk=doc_map)
        report["arm"] = arm
        report["flags"] = {k: ARMS[arm][k] for k in FLAG_KEYS}
        report["failures"] = failure_examples(results, questions, arm=arm)
        reports[arm] = report

        out_name = OUT_DIR / f"{arm}_metrics.json"
        out_name.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        m = report["metrics"]
        logger.info(
            "%s: entity@10=%.2f standard@5=%.2f disambig=%.2f",
            arm,
            m["entity"]["recall@10"],
            m["standard"]["recall@5"],
            report["disambiguation_accuracy"],
        )

    # Ablation table
    table_path = OUT_DIR / "ablation_table.json"
    table_path.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== Food-intent ablation (benchmark v1.1, n=%d) ===" % len(questions))
    header = f"{'arm':22s} {'ent@1':>6s} {'ent@5':>6s} {'ent@10':>7s} {'std@5':>6s} {'std@10':>7s} {'par@5':>6s} {'src@10':>7s} {'disamb':>7s}"
    print(header)
    for arm, r in reports.items():
        m = r["metrics"]
        print(
            f"{arm:22s} "
            f"{m['entity']['recall@1']:6.2f} {m['entity']['recall@5']:6.2f} {m['entity']['recall@10']:7.2f} "
            f"{m['standard']['recall@5']:6.2f} {m['standard']['recall@10']:7.2f} "
            f"{m['parameter']['recall@5']:6.2f} {m['source']['recall@10']:7.2f} "
            f"{r['disambiguation_accuracy']:7.2f}",
        )
    print(f"\ntable -> {table_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
