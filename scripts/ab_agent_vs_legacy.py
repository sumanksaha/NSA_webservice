"""A/B benchmark — legacy pipeline vs LangGraph agent (plan §8 rollout GATE).

Runs both pipelines over a subset of the frozen 150-question benchmark
(``benchmark/benchmark_v1.0.jsonl``) against the **live** Qdrant cluster
and inference endpoints, and reports:

* gold-source-chunk hit rate @10 (retrieval parity check — both pipelines
  share ``run_retrieval_pipeline``, so this must match),
* groundedness (legacy final vs agent final),
* retry distribution (agent), and
* wall-clock latency (legacy vs agent incl. retries).

Audit gap #7 (2026-08-23): the original harness pinned ``RAG_USE_STUB_LLM``
so its numbers could NOT justify flipping ``RAG_USE_AGENT_PIPELINE`` in
production.  This version **defaults to live-LLM mode**: it refuses to run
when ``GroundedLLMClient`` resolves to stub mode (no ``OPENROUTER_API_KEY``)
unless ``--allow-stub`` is passed for a mechanics-only dry run, and applies
an explicit **flip gate** — the process exits non-zero when the agent arm
regresses beyond tolerance on gold-hit@10 or groundedness, i.e. exit 0 is
the recorded evidence a production flag flip needs.

Usage:
    python scripts/ab_agent_vs_legacy.py [--limit N] [--domain DOMAIN] [--allow-stub]

Exit codes:
    0 — A/B ran (live or --allow-stub) and the agent met the quality gate;
    1 — gate failed, or stub mode was detected without --allow-stub.
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

#: Flip-gate tolerances (audit gap #7): the agent arm may trail the legacy
#: arm by at most this much on each metric before the gate fails.
GOLD_HIT_TOLERANCE = 0.05
GROUNDEDNESS_TOLERANCE = 0.05


def check_live_llm() -> tuple[bool, str]:
    """Verify the generation LLM will NOT run in stub mode.

    Returns ``(ok, message)``.  A stub-mode A/B exercises the retry-loop
    mechanics but its groundedness/hit numbers are canned — it cannot serve
    as evidence for a production flag flip (audit gap #7).
    """
    from app.rag.generation.llm_client import GroundedLLMClient

    client = GroundedLLMClient()
    if client.use_stub:
        return False, (
            "GroundedLLMClient is in STUB mode (no OPENROUTER_API_KEY / "
            "RAG_USE_STUB_LLM=true). A stub A/B cannot justify flipping "
            "RAG_USE_AGENT_PIPELINE in production — set OPENROUTER_API_KEY "
            "for a live A/B, or pass --allow-stub for a mechanics-only run."
        )
    return True, f"live LLM ({client.model})"


def summarize(arm_stats: dict) -> dict:
    """Aggregate one arm's accumulator dict into reportable means."""
    n = arm_stats["n"]
    grounded = arm_stats["grounded"]
    latency = arm_stats["latency"]

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "n": n,
        "mean_gold_hit": round(arm_stats["hit"] / n, 3) if n else 0.0,
        "mean_groundedness": round(mean(grounded), 3),
        "mean_latency_s": round(mean(latency), 2),
    }


def parity_verdict(
    legacy_summary: dict,
    agent_summary: dict,
    hit_tolerance: float = GOLD_HIT_TOLERANCE,
    grounded_tolerance: float = GROUNDEDNESS_TOLERANCE,
) -> tuple[bool, str]:
    """Flip-gate verdict comparing agent vs legacy aggregate metrics.

    Returns ``(passed, reason)``.  The gate fails when the agent arm trails
    the legacy arm by more than the tolerance on gold-hit@10 or groundedness.
    """
    l_hit = legacy_summary["mean_gold_hit"]
    a_hit = agent_summary["mean_gold_hit"]
    l_grounded = legacy_summary["mean_groundedness"]
    a_grounded = agent_summary["mean_groundedness"]

    if a_hit < l_hit - hit_tolerance:
        return False, (
            f"FAIL: gold-hit@10 regression — agent {a_hit:.3f} < legacy "
            f"{l_hit:.3f} − {hit_tolerance}. Keep RAG_USE_AGENT_PIPELINE=false."
        )
    if a_grounded < l_grounded - grounded_tolerance:
        return False, (
            f"FAIL: groundedness regression — agent {a_grounded:.3f} < legacy "
            f"{l_grounded:.3f} − {grounded_tolerance}. Keep RAG_USE_AGENT_PIPELINE=false."
        )
    return True, (
        f"PASS: agent within tolerance (gold-hit@10 {a_hit:.3f} vs {l_hit:.3f}; "
        f"groundedness {a_grounded:.3f} vs {l_grounded:.3f}) — safe to consider "
        "flipping RAG_USE_AGENT_PIPELINE."
    )


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


def main(out_dir: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="max questions (default 20)")
    parser.add_argument("--domain", default=None, help="filter by domain (e.g. FOOD_SAFETY)")
    parser.add_argument(
        "--allow-stub",
        action="store_true",
        help="run even in stub LLM mode (mechanics-only dry run — NOT valid flip-gate evidence)",
    )
    args = parser.parse_args()

    if args.allow_stub:
        # Must be set BEFORE create_app()/dotenv so the pipeline picks it up.
        # Skips the live-LLM gate below — this run is explicitly NOT
        # flip-gate evidence.
        os.environ["RAG_USE_STUB_LLM"] = "true"
        llm_msg = "stub LLM (--allow-stub mechanics-only dry run — NOT flip-gate evidence)"
    from app import create_app

    app = create_app()

    if not args.allow_stub:
        with app.app_context():
            llm_ok, llm_msg = check_live_llm()
        if not llm_ok:
            print(f"ABORT: {llm_msg}", file=sys.stderr)
            return 1
    print(f"A/B mode: {llm_msg}")

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

    legacy_summary = summarize(stats["legacy"])
    agent_summary = summarize(stats["agent"])
    gate_passed, gate_reason = parity_verdict(legacy_summary, agent_summary)

    report = {
        "mode": llm_msg,
        "allow_stub": args.allow_stub,
        "questions": len(questions),
        "legacy": legacy_summary,
        "agent": {
            **agent_summary,
            "mean_retries": round(sum(stats["agent"]["retries"]) / len(stats["agent"]["retries"]), 2)
            if stats["agent"]["retries"]
            else 0.0,
        },
        "flip_gate": {"passed": gate_passed, "reason": gate_reason},
    }

    out = out_dir if out_dir is not None else ROOT / "reports"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "ab_agent_vs_legacy.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    with (out / "ab_agent_vs_legacy_summary.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    return 0 if gate_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
