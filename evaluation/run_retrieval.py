"""Phase 1 — run retrieval arms A–F over the 150 benchmark questions.

Usage:
    python -m evaluation.run_retrieval [--arms A_dense,B_sparse] [--force]

Results are cached per arm under ``evaluation/out/raw/<arm>.jsonl``
(append-only, resumable).  All production components run inside a Flask
app context exactly as the deployed system configures them.
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("eval.retrieval")


def _cached_question_ids(arm: str) -> set[str]:
    from evaluation.config import RAW_DIR

    p = RAW_DIR / f"{arm}.jsonl"
    if not p.exists():
        return set()
    ids = set()
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                ids.add(json.loads(line)["question_id"])
            except Exception:
                continue
    return ids


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from evaluation.arms import build_arm_runner
    from evaluation.benchmark import load_questions
    from evaluation.config import ARMS, OUT_DIR, RAW_DIR, write_run_config
    from evaluation.resolution import build_payload_index

    parser = argparse.ArgumentParser()
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--shard", default="1/1", help="e.g. 2/4 -> questions 38..75")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_run_config()

    with app.app_context():
        questions = load_questions()
        collections = _all_collections(app)
        payload_index = build_payload_index(
            lambda coll: _store_factory(coll), collections, force=args.force
        )
        logger.info("payload index: %d points", len(payload_index))
        (OUT_DIR / "payload_index_meta.json").write_text(
            json.dumps({"n_points": len(payload_index), "collections": collections}),
            encoding="utf-8",
        )

        # shard slice
        idx_str, n_str = args.shard.split("/")
        shard_idx, n_shards = int(idx_str), int(n_str)
        questions = questions[shard_idx - 1::n_shards] if n_shards > 1 else questions
        logger.info("shard %s: %d questions", args.shard, len(questions))

        arms = [a.strip() for a in args.arms.split(",") if a.strip()]
        for arm in arms:
            runner = build_arm_runner(arm)
            cache_path = RAW_DIR / f"{arm}.jsonl"
            done = _cached_question_ids(arm)
            logger.info("arm %s: %d questions cached, running rest", arm, len(done))
            started = time.monotonic()
            with open(cache_path, "a", encoding="utf-8") as out:
                for i, q in enumerate(questions, 1):
                    if q.question_id in done and not args.force:
                        continue
                    t0 = time.monotonic()
                    try:
                        result = runner(q.raw)
                        result["arm"] = arm
                        result["question_id"] = q.question_id
                        result["error"] = result.get("error")
                    except Exception as exc:
                        result = {
                            "arm": arm,
                            "question_id": q.question_id,
                            "chunk_ids": [],
                            "kg_provisions": [],
                            "kg_source": None,
                            "latency_ms": int((time.monotonic() - t0) * 1000),
                            "error": f"{type(exc).__name__}: {exc}",
                            "retriever": "error",
                        }
                    out.write(json.dumps(result, ensure_ascii=False) + "\n")
                    out.flush()
                    if i % 10 == 0 or i == len(questions):
                        elapsed = time.monotonic() - started
                        logger.info(
                            "arm %s %d/%d (%.0fs, last %.0fms)",
                            arm, i, len(questions), elapsed,
                            result.get("latency_ms", 0),
                        )
            logger.info("arm %s complete", arm)
    logger.info("retrieval phase done")
    return 0


def _all_collections(app) -> list[str]:
    """The six production collections (benchmark-referenced + criminal)."""
    cfg = app.config
    cols = [
        cfg.get("RAG_QDRANT_COLLECTION", "fssai_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ENV", "env_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_COMMERCIAL", "commercial_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_ANIMAL", "animal_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_WB_STATE", "wb_state_legal_768"),
        cfg.get("RAG_QDRANT_COLLECTION_CRIMINAL", "criminal_legal_768"),
    ]
    return list(dict.fromkeys(cols))


def _store_factory(collection: str):
    from app.rag.qdrant_client import QdrantStore

    return QdrantStore(collection_name=collection)


if __name__ == "__main__":
    raise SystemExit(main())
