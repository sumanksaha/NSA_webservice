"""RAG pipeline metrics export — Prometheus + OTel compatible.

Exports evaluation metrics and pipeline latency to the configured backend.
Uses prometheus-client if installed, otherwise no-ops (zero-cost when
monitoring isn't configured — see RAG_IMPROVEMENTS.md 3.3).

Usage in tasks.py:
    metrics.export(pipeline_latency_ms, retrieval_mrr, eval_metrics)
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# --- Lazy Prometheus / OTel adapter (ponytail: stdlib first, optional import) ---
try:
    from prometheus_client import Counter, Histogram, Gauge, generate_latest
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

# --- Metric objects (lazy init) ---
_request_count = None
_latency_hist = None
_score_gauges: dict[str, Any] = {}
_eval_histograms: dict[str, Any] = {}
_mrr_hist = None

_METRIC_PREFIX = "nsa_rag"


def _ensure_metrics() -> None:
    """Initialize Prometheus metrics if available (thread-safe lazy init)."""
    global _request_count, _latency_hist, _mrr_hist
    if not _HAS_PROMETHEUS:
        return
    if _request_count is not None:
        return
    _request_count = Counter(
        f"{_METRIC_PREFIX}_requests_total",
        "Total RAG pipeline requests",
        ["query_type"],
    )
    _latency_hist = Histogram(
        f"{_METRIC_PREFIX}_pipeline_latency_ms",
        "RAG pipeline latency in milliseconds",
        buckets=(50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000),
    )
    _mrr_hist = Histogram(
        f"{_METRIC_PREFIX}_retrieval_mrr",
        "Mean Reciprocal Rank of gold chunk in retrieval",
        buckets=(0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0),
    )
    _ensure_score_gauges()
    _ensure_eval_histograms()


def _ensure_score_gauges() -> None:
    """Create Gauge objects for each RAGAS metric name."""
    metric_names = ("faithfulness", "answer_relevance", "context_precision",
                    "context_recall", "citation_recall", "groundedness")
    for name in metric_names:
        if name not in _score_gauges:
            _score_gauges[name] = Gauge(
                f"{_METRIC_PREFIX}_score_{name}",
                f"RAGAS {name} score (0-1)",
            )


def _ensure_eval_histograms() -> None:
    """Create Histogram objects for each RAGAS metric name (for distribution)."""
    metric_names = ("faithfulness", "answer_relevance", "context_precision",
                    "context_recall", "citation_recall", "groundedness")
    for name in metric_names:
        if name not in _eval_histograms:
            _eval_histograms[name] = Histogram(
                f"{_METRIC_PREFIX}_eval_{name}",
                f"Distribution of RAGAS {name} scores",
                buckets=(0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0),
            )


def export(
    pipeline_latency_ms: float,
    retrieval_mrr: float | None = None,
    eval_metrics: dict[str, float] | None = None,
    query_type: str = "unknown",
    success: bool = True,
) -> None:
    """Export pipeline metrics to Prometheus (no-op if unavailable).
    
    Args:
        pipeline_latency_ms: End-to-end pipeline latency.
        retrieval_mrr: Mean Reciprocal Rank of gold chunk (optional).
        eval_metrics: Dict of RAGAS metric name -> score (optional).
        query_type: Type of query for labeling.
        success: Whether the pipeline succeeded.
    """
    _ensure_metrics()
    if not _HAS_PROMETHEUS:
        logger.debug("prometheus_client not installed — metrics dropped")
        return

    try:
        _request_count.labels(query_type=query_type).inc()

        if success:
            _latency_hist.observe(pipeline_latency_ms)

        if retrieval_mrr is not None:
            _mrr_hist.observe(retrieval_mrr)

        if eval_metrics:
            for name, score in eval_metrics.items():
                normalized = max(0.0, min(1.0, float(score)))
                if name in _score_gauges:
                    _score_gauges[name].set(normalized)
                if name in _eval_histograms:
                    _eval_histograms[name].observe(normalized)
    except Exception as exc:
        logger.warning("metrics export failed: %s", exc)


def prometheus_endpoint() -> tuple[str, str] | None:
    """Return (content_type, body) for Prometheus scrape if available."""
    if not _HAS_PROMETHEUS:
        return None
    _ensure_metrics()
    return ("text/plain; charset=utf-8", generate_latest().decode("utf-8"))
