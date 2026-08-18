"""A/B benchmark — legacy pipeline vs LangGraph agent (plan §8 rollout).

Runs both pipelines over a subset of the frozen 150-question benchmark
(``benchmark/benchmark_v1.0.jsonl``) against the **live** Qdrant cluster
and Modal inference endpoints, and reports:

* gold-source-chunk hit rate @10 (retrieval parity check — both pipelines
  share ``run_retrieval_pipeline``, so this must match),
* groundedness (legacy final vs agent final — with the stub LLM the
  *absolute* numbers are low, but the retry-loop mechanics are real),
* retry distribution (agent), and
* wall-clock latency (legacy vs agent incl. retries).

The LLM is pinned to stub mode (``RAG_USE_STUB_LLM=true``) so the run is
deterministic and needs no API key — a production-grade groundedness A/B
needs the real ``OPENROUTER_API_KEY`` and is part of the post-deploy flip
gate (task.md ENV-11).

Usage:
    python scripts/ab_agent_vs_legacy.py [--limit N] [--domain DOMAIN]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["RAG_USE_STUB_LLM"] = "true"


def load_questions(limit: int, domain: str | None) -> list[dict]:
    questions = []
    with Path(ROOT / "benchmark" / "benchmark_v1.0.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            q = json.loads(line)
            if domain and domain not in (q.get("domains") or []):
                continue
            questions.append(q)
            if limit and len(questions) >= limit:
                break
    return questions


def gold_hit_rate(question: dict, chunks: list[dict]) -> float:
    """Fraction of gold units covered by the retrieved chunks (family+section).

    Uses the eval framework's canonical resolution (``FamilyMap`` + gold
    registry) so e.g. ``fssai:s16(1)`` matches a chunk stamped
    ``act_name=Food Safety...`` + ``section_number=16`` — not a naive string
    compare against chunk IDs (the gold ids like ``fssai:s16(1)`` are
    provision references, not Qdrant point ids).
    """
    from evaluation.benchmark import _resolve_units, load_gold_registry
    from evaluation.resolution import FamilyMap, matches_gold

    gold_units = _resolve_units(question, load_gold_registry())
    if not gold_units:
        return 0.0
    family_map = FamilyMap()
    covered = 0
    for unit in gold_units:
        if any(matches_gold(c, unit, family_map) for c in chunks):
            covered += 1
    return covered / len(gold_units)


def run_legacy(question: str) -> tuple[dict, float]:
    from app.rag.tasks import run_generation_pipeline

    start = time.monotonic()
    result = run_generation_pipeline(query=question, top_k=10, pipeline="legacy")
    return result, time.monotonic() - start


def run_agent(question: str) -> tuple[dict, float]:
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state

    start = time.monotonic()
    result = run_agent(initial_state(question, top_k=10))
    return result, time.monotonic() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="max questions (default 20)")
    parser.add_argument("--domain", default=None, help="filter by domain (e.g. FOOD_SAFETY)")
    args = parser.parse_args()

    from app import create_app

    app = create_app()
    questions = load_questions(args.limit, args.domain)

    stats = {
        "legacy": {"hit": 0.0, "grounded": [], "latency": [], "n": 0},
        "agent": {"hit": 0.0, "grounded": [], "latency": [], "retries": [], "n": 0},
    }
    rows = []

    for i, q in enumerate(questions, 1):
        qid = q["question_id"]
        qtext = q["question"]

        with app.app_context():
            legacy_result, legacy_lat = run_legacy(qtext)
            agent_result, agent_lat = run_agent(qtext)

        l_chunks = legacy_result.get("retrieved_chunks") or []
        a_chunks = agent_result.get("retrieved_chunks") or []
        l_hit = gold_hit_rate(q, l_chunks)
        a_hit = gold_hit_rate(q, a_chunks)
        l_grounded = legacy_result.get("groundedness_score", 0.0)
        a_grounded = agent_result.get("groundedness_score", 0.0)
        a_retries = (agent_result.get("agent") or {}).get("retry_count", 0)

        stats["legacy"]["hit"] += l_hit
        stats["legacy"]["grounded"].append(l_grounded)
        stats["legacy"]["latency"].append(legacy_lat)
        stats["legacy"]["n"] += 1
        stats["agent"]["hit"] += a_hit
        stats["agent"]["grounded"].append(a_grounded)
        stats["agent"]["latency"].append(agent_lat)
        stats["agent"]["retries"].append(a_retries)
        stats["agent"]["n"] += 1

        rows.append({
            "question_id": qid,
            "question": qtext,
            "legacy": {
                "gold_hit": round(l_hit, 3),
                "groundedness": round(l_grounded, 3),
                "latency_s": round(legacy_lat, 2),
            },
            "agent": {
                "gold_hit": round(a_hit, 3),
                "groundedness": round(a_grounded, 3),
                "retries": a_retries,
                "latency_s": round(agent_lat, 2),
            },
        })

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    for name, s in (("legacy", stats["legacy"]), ("agent", stats["agent"])):
        if "retries" in s:
            pass

    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    out_file = out / "ab_agent_vs_legacy.jsonl"
    with out_file.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
