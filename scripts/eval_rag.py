"""RAG evaluation CLI — gold decomposition benchmark + eval batch reports.

Usage:
    python -m scripts.eval_rag --benchmark
        Score the deterministic planner against the gold dataset
        (``app/rag/evaluation/gold_dataset``).  Fully offline.

    python -m scripts.eval_rag --benchmark --min-recall 0.9
        Same, but exit 1 when task recall falls below the gate (CI-ready).

    python -m scripts.eval_rag --queries "What is Section 12?" "..."
        Run the live agent pipeline over the given queries and score each
        response with the RAGAS-style reference metrics.  Requires the
        retrieval stack (DB / Qdrant); fails gracefully without it.

    python -m scripts.eval_rag --benchmark --json
        Machine-readable output.

Exit codes: 0 success / gate passed, 1 gate failed, 2 usage or runtime error.
"""

from __future__ import annotations

import argparse
import json
import sys

PER_ENTRY = "  {:<3} {:<6} {:>7} {:>9} {:>6} {:>5}  {}"


def _print_benchmark_report(report: dict, per_entry_rows: list[dict] | None = None) -> None:
    """Human-readable benchmark report."""
    print("Gold decomposition benchmark")
    print("=" * 72)
    if per_entry_rows:
        print(PER_ENTRY.format("id", "class", "recall", "precision", "f1", "deps", "query"))
        print("-" * 72)
        for row in per_entry_rows:
            print(
                PER_ENTRY.format(
                    row["id"],
                    row["query_class"][:6],
                    f'{row["recall"]:.2f}',
                    f'{row["precision"]:.2f}',
                    f'{row["f1"]:.2f}',
                    f'{row["dep_accuracy"]:.2f}',
                    row["query"][:52],
                )
            )
        print("-" * 72)
    for key in (
        "task_recall",
        "task_precision",
        "decomposition_f1",
        "dependency_accuracy",
        "exact_match_rate",
        "over_decomposition_rate",
        "under_decomposition_rate",
    ):
        value = report.get(key)
        print(f"  {key:<26} {value if value is not None else 'n/a'}")
    per_class = report.get("per_query_class") or {}
    if per_class:
        print("  per query class:")
        for cls, stats in per_class.items():
            print(
                f"    {cls:<20} n={stats.get('count', 0)} "
                f"recall={stats.get('avg_recall')} f1={stats.get('avg_f1')}"
            )


def run_benchmark(min_recall: float | None = None, as_json: bool = False) -> int:
    """Score the planner against the gold dataset.  Returns the exit code."""
    from app.rag.evaluation.benchmark import DecompositionBenchmark
    from app.rag.evaluation.gold_dataset import GOLD_DECOMPOSITION
    from app.rag.planning.query_planner import QueryPlanner

    planner = QueryPlanner()
    bench = DecompositionBenchmark.from_gold_dataset()

    per_entry_rows: list[dict] = []
    for i, entry in enumerate(GOLD_DECOMPOSITION, start=1):
        decomposition = planner.plan(entry["query"])
        record = bench.record_prediction(entry["query"], decomposition)
        if record is None:  # pragma: no cover - from_gold_dataset guarantees a match
            continue
        gold = {}
        pred = {}
        for kind in entry["gold_task_kinds"]:
            gold[kind] = gold.get(kind, 0) + 1
        for kind in record.predicted_task_kinds:
            pred[kind] = pred.get(kind, 0) + 1
        matched = sum(min(gold.get(k, 0), pred.get(k, 0)) for k in set(gold) | set(pred))
        n_gold, n_pred = sum(gold.values()), sum(pred.values())
        gold_edges = {
            (d, kind)
            for kind, deps in (entry.get("gold_dependencies") or {}).items()
            for d in deps
        }
        pred_edges = set(record.predicted_dependencies or set())
        union = gold_edges | pred_edges
        per_entry_rows.append(
            {
                "id": f"q{i}",
                "query": entry["query"],
                "query_class": entry.get("query_class", "general"),
                "recall": round(matched / n_gold, 2) if n_gold else 0.0,
                "precision": round(matched / n_pred, 2) if n_pred else 0.0,
                "f1": round(2 * matched / (n_gold + n_pred), 2) if n_gold + n_pred else 0.0,
                "dep_accuracy": round(len(gold_edges & pred_edges) / len(union), 2)
                if union
                else 1.0,
            }
        )

    report = bench.evaluate()

    if as_json:
        print(json.dumps({"report": report, "per_entry": per_entry_rows}, indent=2))
    else:
        _print_benchmark_report(report, per_entry_rows)

    if min_recall is not None:
        recall = report.get("task_recall")
        if recall is None or recall < min_recall:
            print(
                f"\nGATE FAILED: task_recall {recall} < min-recall {min_recall}",
                file=sys.stderr,
            )
            return 1
        print(f"\nGate passed: task_recall {recall} >= {min_recall}")
    return 0


def run_query_batch(queries: list[str], as_json: bool = False) -> int:
    """Run the live agent pipeline over queries and score the responses."""
    from app.rag.agent.graph import run_agent
    from app.rag.agent.state import initial_state
    from app.rag.evaluation.runner import EvalRunner

    def pipeline(query: str) -> dict:
        result = run_agent(initial_state(query))
        return {
            "answer": result.get("answer", ""),
            "retrieved_chunks": result.get("chunks") or [],
            "cited_chunk_ids": [
                c.get("chunk_id")
                for c in (result.get("chunks") or [])
                if isinstance(c, dict) and c.get("chunk_id")
            ],
        }

    runner = EvalRunner(pipeline_fn=pipeline)
    entries = [{"query": q, "expected_citations": []} for q in queries]
    report = runner.evaluate_batch(entries, persist=False)

    if as_json:
        print(json.dumps(report, indent=2, default=str))
        return 0

    summary = report.get("summary", {})
    print("Live eval batch")
    print("=" * 72)
    print(f"  queries evaluated : {summary.get('total')}")
    print(f"  errors            : {summary.get('errors')}")
    for name in (
        "faithfulness",
        "answer_relevance",
        "context_precision",
        "context_recall",
        "citation_recall",
        "groundedness",
    ):
        print(f"  {name + '_avg':<26} {summary.get(name + '_avg')}")
    print(f"  {'mrr_avg':<26} {summary.get('mrr_avg')}")
    print(f"  {'latency_avg_ms':<26} {summary.get('latency_avg_ms')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval_rag", description="RAG evaluation: gold benchmark + live batch"
    )
    parser.add_argument("--benchmark", action="store_true", help="gold decomposition benchmark")
    parser.add_argument(
        "--min-recall", type=float, default=None, help="fail (exit 1) when task recall < value"
    )
    parser.add_argument(
        "--queries", nargs="+", default=None, help="run the live pipeline over these queries"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if not args.benchmark and not args.queries:
        parser.print_help()
        return 2
    try:
        if args.benchmark:
            return run_benchmark(min_recall=args.min_recall, as_json=args.json)
        return run_query_batch(args.queries, as_json=args.json)
    except ImportError as exc:
        print(f"Live evaluation requires the retrieval stack: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # surface a clean CLI error, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
