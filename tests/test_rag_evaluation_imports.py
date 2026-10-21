"""Import-boundary regressions for the RAG decomposition evaluator."""

from __future__ import annotations

import ast
from pathlib import Path

from app.rag.evaluation import QueryBenchmark as PackageQueryBenchmark
from app.rag.evaluation.benchmark import QueryBenchmark as BenchmarkQueryBenchmark
from app.rag.evaluation.benchmark_entry import QueryBenchmark as EntryQueryBenchmark
from app.rag.evaluation.decomposition_metrics import QueryBenchmark as MetricsQueryBenchmark

_METRICS_PATH = Path(__file__).resolve().parents[1] / "app" / "rag" / "evaluation" / "decomposition_metrics.py"


def test_query_benchmark_has_one_neutral_owner():
    assert BenchmarkQueryBenchmark is EntryQueryBenchmark
    assert MetricsQueryBenchmark is EntryQueryBenchmark
    assert PackageQueryBenchmark is EntryQueryBenchmark
    assert EntryQueryBenchmark.__module__ == "app.rag.evaluation.benchmark_entry"


def test_decomposition_metrics_does_not_import_the_evaluator():
    tree = ast.parse(_METRICS_PATH.read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert "app.rag.evaluation.benchmark" not in imported
    assert "benchmark" not in imported
    assert "app.rag.evaluation.benchmark_entry" in imported
