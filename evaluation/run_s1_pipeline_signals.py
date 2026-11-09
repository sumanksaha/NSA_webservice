"""S1 blocker run — production pipeline signals per qid (plan §6 BLOCKER).

The per-query-type threshold calibration (S1) needs the production
pipeline's continuous per-qid signals: ``groundedness`` and the
abstention outcome on real retrieval.  These live in no existing artifact
(``answers_baseline.json`` carries only evaluator-v2 oracle-arm scores),
so this runner generates them live, per question of the frozen benchmark.

Environment seams used (all documented flags, nothing hand-wired):

* ``RAG_QDRANT_BM25=true`` — set from ``.env`` (default is false); this box
  runs the Qdrant-side BM25 arm (verified live) because it cannot reach
  huggingface for the local dense/cross-encoder weights.
* Harness pins ``app.rag.model_runtime.get_sentence_transformer`` /
  ``get_cross_encoder`` to ``None`` so every consumer takes its documented
  degrade path (dense arm returns an error result, EnsembleReranker falls
  back to sec_act features) instead of hanging on unreachable downloads.
* ``LLM_ENABLED=true`` + ``MAX_LLM_CALLS`` from ``.env`` enforce the call
  budget; the runner stops cleanly mid-run when exhausted and resumes on
  the next invocation (append-only jsonl keyed by ``question_id``).

Output (append-only jsonl, resumable):
``evaluation/out/ceiling_v5/s1_pipeline_signals.jsonl``

Per-line fields: ``question_id`` / ``split`` / ``query`` /
``query_types`` / ``expected_abstain`` /
``groundedness_score`` (production pipeline's continuous signal) /
``abstained`` (production ``is_abstention`` on the final answer) /
``hallucination_detected`` / ``n_chunk`` / ``retrieval_error`` /
``answer_head`` (first 2000 chars) / ``token_usage`` / ``latencies`` /
``llm_calls_used``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OUT_DIR = PROJECT_ROOT / "evaluation" / "out" / "ceiling_v5"
OUT_PATH = OUT_DIR / "s1_pipeline_signals.jsonl"


def _pin_models_offline() -> bool:
    """Restore or pin the local torch models, offline-only.

    Tries to load the default sentence-transformer / cross-encoder purely
    from the local HF cache (``HF_HUB_OFFLINE=1`` — no network attempt).
    ``sentence-transformers`` is installed in this venv; the only blocker
    was HF download TLS.  When the weights are cached (fetched out-of-band
    through the system Python, whose TLS path validates the proxy chain)
    the dense arm and CE reranker run for real.  Only if the cache is
    empty are both loaders pinned to ``None`` so consumers take their
    documented degrade paths (dense arm errors, ``EnsembleReranker``
    falls back to sec_act features).

    Also forces ``RAG_QDRANT_BM25=true``: the configured local fastembed
    sparse path needs an ONNX model download that this box cannot perform;
    the Qdrant-side BM25 arm (cluster computes the query vector) is verified
    live against the provisioned collection.  Set before ``create_app`` so
    ``seed_config_from_env`` seeds the override into app config.

    Returns True when at least the embedding model loaded offline.
    """
    import os

    os.environ["RAG_QDRANT_BM25"] = "true"
    os.environ["HF_HUB_OFFLINE"] = "1"  # never attempt HF network from this run

    loaded = False
    try:
        from app.rag.model_runtime import get_cross_encoder, get_sentence_transformer
        from app.shared.config import cfg

        enc = get_sentence_transformer(cfg.embedding_model)
        loaded = enc is not None
        ce = get_cross_encoder(cfg.reranker_model)
        if enc is None:
            import app.rag.model_runtime as model_runtime

            model_runtime.get_sentence_transformer = lambda name: None
        if ce is None:
            import app.rag.model_runtime as model_runtime

            model_runtime.get_cross_encoder = lambda name: None
    except Exception as exc:  # noqa: BLE001 — the pin is the last-resort path
        import app.rag.model_runtime as model_runtime

        model_runtime.get_sentence_transformer = lambda name: None
        model_runtime.get_cross_encoder = lambda name: None
        print(f"model pin fallback: {type(exc).__name__}: {exc}", file=sys.stderr)
    return loaded


def _cached_qids() -> set[str]:
    """Question ids already collected (partial trailing lines are skipped)."""
    done: set[str] = set()
    if not OUT_PATH.exists():
        return done
    with open(OUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["question_id"])
            except Exception:  # unparsable/partial line — resumable, ignore
                continue
    return done


def _record(q: Any, result: dict[str, Any], calls_used: int, wall_s: float) -> dict[str, Any]:
    """Fold one pipeline result into the on-disk signal record."""
    from app.rag.generation.abstention import ABSTENT_MARKERS_RE, is_abstention

    answer = str(result.get("answer") or "")
    chunks = result.get("retrieved_chunks") or []
    text = answer.strip()
    markers = sorted({m.group(0) for m in ABSTENT_MARKERS_RE.finditer(text)}) if text else []
    return {
        "question_id": q.question_id,
        "split": q.split,
        "query": q.question,
        "query_types": q.question_types,
        "expected_abstain": q.insufficient_evidence,
        "collections": q.collections,
        "groundedness_score": result.get("groundedness_score"),
        "hallucination_detected": result.get("hallucination_detected"),
        "abstained": is_abstention(answer),
        "n_chunks": len(chunks),
        # Provision stamps of the retrieved pool — lets the recall checker
        # score against gold_source_chunks without re-running retrieval.
        "chunk_stamps": [
            {
                "sec": c.get("section_number"),
                "clause": c.get("clause_number"),
                "doc_type": c.get("document_type"),
                "act": str(c.get("act_name") or "")[:40],
            }
            for c in chunks
        ],
        "has_answer": bool(answer.strip()) and not result.get("error"),
        "retrieval_error": result.get("error"),
        "answer_head": answer[:2000],
        # S1 negation-marker diagnosis — which ABSTENT_MARKERS_RE alternative
        # fired in the answer text, to pinpoint the prompt template trigger.
        "abstention_markers": markers,
        "llm_model": result.get("llm_model"),
        "token_usage": result.get("token_usage"),
        "llm_calls_used": calls_used,
        "total_latency_ms": result.get("total_latency_ms"),
        "wall_s": round(wall_s, 1),
    }


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    import argparse

    ap = argparse.ArgumentParser(description="Collect production RAG signals per benchmark question")
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated question ids to force re-run, ignoring the resume cache "
        "(rows are appended; the analysis loaders keep the last row per question)",
    )
    args = ap.parse_args()

    from app.shared import llm_client as llm_budget
    from app.shared.llm_client import llm_enabled

    if not llm_enabled():
        print("LLM_ENABLED is off — refusing to run", file=sys.stderr)
        return 2

    _pin_models_offline()

    from app import create_app

    app = create_app()

    from evaluation.benchmark import load_questions

    with app.app_context():
        questions = load_questions()
        forced = {q.strip() for q in args.only.split(",") if q.strip()}
        if forced:
            unknown = forced - {q.question_id for q in questions}
            if unknown:
                print(f"unknown question ids: {sorted(unknown)}", file=sys.stderr)
                return 2
            pending = [q for q in questions if q.question_id in forced]
            print(f"force re-run of {len(pending)} question(s); rows are appended", flush=True)
            done: set[str] = set()
        else:
            done = _cached_qids()
            pending = [q for q in questions if q.question_id not in done]
        max_calls = llm_budget.max_llm_calls()
        print(
            f"questions={len(questions)} cached={len(done)} "
            f"pending={len(pending)} llm_calls_used={llm_budget.llm_call_count()} "
            f"budget={max_calls}",
            flush=True,
        )
        if not pending:
            return 0

        from app.rag.tasks import run_generation_pipeline

        started = time.monotonic()
        with open(OUT_PATH, "a", encoding="utf-8") as out:
            for i, q in enumerate(pending, 1):
                calls_before = llm_budget.llm_call_count()
                t0 = time.monotonic()
                try:
                    result = run_generation_pipeline(
                        q.question,
                        top_k=10,
                        collection_name=q.collections or None,
                        pipeline="s1_signals",
                    )
                except Exception as exc:  # keep the run moving, record it
                    result = {
                        "error": f"{type(exc).__name__}: {exc}",
                        "answer": "",
                        "groundedness_score": None,
                        "retrieved_chunks": [],
                    }
                calls_used = llm_budget.llm_call_count() - calls_before
                record = _record(q, result, calls_used, time.monotonic() - t0)
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                if i % 5 == 0 or i == len(pending):
                    total_used = llm_budget.llm_call_count()
                    print(
                        f"{i}/{len(pending)} last_qid={q.question_id} "
                        f"calls={total_used}/{max_calls} "
                        f"elapsed={time.monotonic() - started:.0f}s",
                        flush=True,
                    )
                if llm_budget.llm_call_count() >= max_calls:
                    print(
                        "LLM budget exhausted mid-run; rerun to resume from the last flush",
                        file=sys.stderr,
                    )
                    return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
