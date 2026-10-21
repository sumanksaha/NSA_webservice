"""Prometheus metrics — one module, one namespace, one registry contract.

Every metric object in the application is constructed **here** (spec FR-10,
FR-27). A metric built anywhere else is a review defect, because only this
module can enforce the label allowlists of spec §7 — and those allowlists are
what keep the series count bounded (INV-3, NFR-4/5). Grafana Cloud bills on
active series, so an unbounded label is not just untidy, it is a bill.

The rules this module enforces:

* **Allowlists, not trust.** Every label value passes through a helper below. A
  value that fails its allowlist becomes the literal ``"other"`` — never
  recorded raw.
* **Rules, not paths.** ``route`` labels are matched *route rules*
  (``/inspection/<int:inspection_id>``), never interpolated paths. Callers pass
  ``request.url_rule.rule`` (Flask) or ``scope["route"].path`` (Starlette);
  both are templates by construction.
* **Counters never decrease, and end in ``_total``.** Gauges may (INV-8).
* **Nothing heavy at scrape time.** ``render_metrics()`` serialises in-memory
  state only — no DB, no LLM, no network (INV-6). Prometheus pulls
  synchronously with a 10 s default timeout, so a heavy aggregate would show up
  as a scrape failure, which is exactly when you most want the metrics.

Import-safe by design: this module reads no config and touches no Flask context,
so it can be imported by a scraper-shaped test with nothing else running.
"""

from __future__ import annotations

import os
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
    multiprocess,
)

# --------------------------------------------------------------------------- #
# Label allowlists (spec §7, V-1…V-4). Values not listed become "other".
# --------------------------------------------------------------------------- #

OTHER = "other"
UNMATCHED = "unmatched"

#: In-flight sentinel: the ASGI surface labels its gauge before dispatch, when
#: the route genuinely is not resolved yet. Flask knows the rule up front and
#: never uses this. One bounded extra series (INV-3 survives).
PENDING = "pending"

SURFACES = frozenset({"flask", "fastapi"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
STATUS_MIN = 100
STATUS_MAX = 599

#: Longest route rule worth recording; anything longer is not a rule.
MAX_ROUTE_LENGTH = 120

#: Paths that are never counted, on either surface. ``/api/v2/health`` is a
#: liveness probe; ``/metrics`` must not count itself (FR-33); ``/health`` is
#: excluded for the same reason (a monitor pinging every 10 s would dominate the
#: HTTP rate and flatten every other signal).
SKIPPED_PATHS = frozenset({"/metrics", "/health", "/api/v2/health"})

#: The prefix FastAPI owns natively in ``asgi.py``. Requests under it never
#: reach Flask, which is what makes the two surfaces disjoint (INV-3).
FASTAPI_PREFIX = "/api/v2"


# --------------------------------------------------------------------------- #
# Metric catalogue — events (spec §5.2)
# --------------------------------------------------------------------------- #

HTTP_REQUESTS = Counter(
    "nsa_http_requests_total",
    "HTTP requests handled, by surface and matched route rule.",
    ("surface", "method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "nsa_http_request_duration_seconds",
    "HTTP request latency, by surface and matched route rule.",
    ("surface", "method", "route"),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "nsa_http_requests_in_progress",
    "HTTP requests currently being handled.",
    ("surface", "route"),
    multiprocess_mode="livesum",
)

RAG_QUERIES = Counter(
    "nsa_rag_queries_total",
    "RAG queries completed, by query type, pipeline and outcome.",
    ("query_type", "pipeline", "outcome"),
)
RAG_RETRIEVAL_DURATION = Histogram(
    "nsa_rag_retrieval_duration_seconds",
    "Retrieval stage latency.",
    ("stage",),
)
RAG_PIPELINE_DURATION = Histogram(
    "nsa_rag_pipeline_duration_seconds",
    "End-to-end RAG latency. Buckets reach 120 s: an LLM answer over a "
    "cross-encoder rerank plus a Neo4j expansion does not fit the 10 s default.",
    ("pipeline",),
    buckets=[0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120],
)
LLM_GENERATION_DURATION = Histogram(
    "nsa_llm_generation_duration_seconds",
    "LLM generation latency, by model.",
    ("model",),
    buckets=[0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55],
)
LLM_GENERATIONS = Counter(
    "nsa_llm_generations_total",
    "LLM generations, by model and whether the live or stub path served them.",
    ("model", "mode"),
)
LLM_TOKENS = Counter(
    "nsa_llm_tokens_total",
    "LLM tokens, by model and direction.",
    ("model", "direction"),
)
RAG_HALLUCINATIONS = Counter(
    "nsa_rag_hallucinations_total",
    "Answers flagged as hallucinated.",
    ("query_type",),
)
RAG_DEGRADED = Counter(
    "nsa_rag_degraded_total",
    "RAG requests served by a fallback path, by reason.",
    ("reason",),
)
QSTASH_TASKS = Counter(
    "nsa_qstash_tasks_total",
    "QStash task deliveries, by task and outcome.",
    ("task", "outcome"),
)
QSTASH_TASK_DURATION = Histogram(
    "nsa_qstash_task_duration_seconds",
    "QStash task duration, by task.",
    ("task",),
)
CSP_VIOLATIONS = Counter(
    "nsa_csp_violations_total",
    "Content-Security-Policy violations reported by browsers.",
    ("directive",),
)

# --------------------------------------------------------------------------- #
# Metric catalogue — snapshots (spec §5.3). Set by the refresh job (OBS4);
# never inc()/dec() — `mostrecent` gauges raise on inc (INV-7).
# --------------------------------------------------------------------------- #

RAG_GROUNDEDNESS_AVG = Gauge(
    "nsa_rag_groundedness_avg",
    "Mean groundedness score over the last 24 h.",
    multiprocess_mode="mostrecent",
)
RAG_HALLUCINATION_RATE = Gauge(
    "nsa_rag_hallucination_rate",
    "Share of answers flagged hallucinated over the last 24 h.",
    multiprocess_mode="mostrecent",
)
RAG_QUERIES_24H = Gauge(
    "nsa_rag_queries_24h",
    "RAG queries logged in the last 24 h.",
)
BACKUP_LAST_SUCCESS_TIMESTAMP = Gauge(
    "nsa_backup_last_success_timestamp_seconds",
    "Unix timestamp of the last successful backup (same source as /health/backups).",
    multiprocess_mode="mostrecent",
)
DB_POOL_CHECKEDOUT = Gauge(
    "nsa_db_pool_checkedout",
    "SQLAlchemy pool connections currently checked out.",
)
# prometheus_client appends ``_info`` to an ``Info`` name, so this renders as the
# ``nsa_build_info`` family the spec names (MET-S6) — passing "nsa_build_info"
# here would export ``nsa_build_info_info``.
BUILD_INFO = Info(
    "nsa_build",
    "Build identity: version and deployed git SHA.",
)


# --------------------------------------------------------------------------- #
# Label helpers (spec §7)
# --------------------------------------------------------------------------- #


def allowlisted(value: Any, allowed: frozenset[str]) -> str:
    """Return *value* when it is in *allowed*, else ``"other"``."""
    text = str(value)
    return text if text in allowed else OTHER


def surface_label(value: Any) -> str:
    """V-1: ``flask`` | ``fastapi``."""
    return allowlisted(value, SURFACES)


def method_label(value: Any) -> str:
    """V-2: an uppercase HTTP method."""
    return allowlisted(str(value).upper(), METHODS)


def route_label(rule: Any) -> str:
    """V-3: the matched route **rule**, never an interpolated path.

    ``None``/blank means routing failed — recorded as ``"unmatched"`` so that a
    burst of 404s is visible without inventing a series per URL. The length and
    leading-slash checks are the last line of defence for a caller that hands us
    a path by mistake; the real guarantee is that both call sites pass a rule.
    """
    if rule is None:
        return UNMATCHED
    text = str(rule)
    if text == PENDING:
        return PENDING
    if not text or len(text) > MAX_ROUTE_LENGTH or not text.startswith("/"):
        return UNMATCHED
    return text


def status_label(value: Any) -> str:
    """V-4: a valid HTTP status integer."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return OTHER
    return str(code) if STATUS_MIN <= code <= STATUS_MAX else OTHER


def is_countable_path(path: str) -> bool:
    """True for paths the FastAPI (ASGI) surface owns and must count (FR-35)."""
    return path.startswith(FASTAPI_PREFIX) and path not in SKIPPED_PATHS


def is_flask_countable_path(path: str) -> bool:
    """True for paths that reach Flask and are worth counting (FR-34, FR-36)."""
    return path not in SKIPPED_PATHS and not path.startswith(FASTAPI_PREFIX)


# --------------------------------------------------------------------------- #
# Recording helpers
# --------------------------------------------------------------------------- #


def http_request_started(surface: str, route: Any) -> None:
    """Increment the in-flight gauge. Always paired with .._finished."""
    HTTP_REQUESTS_IN_PROGRESS.labels(surface_label(surface), route_label(route)).inc()


def http_request_finished(surface: str, route: Any) -> None:
    """Decrement the in-flight gauge, with the same labels used to increment."""
    HTTP_REQUESTS_IN_PROGRESS.labels(surface_label(surface), route_label(route)).dec()


def observe_http_request(*, surface: str, method: str, route: Any, status: Any, duration: float) -> None:
    """Record one completed HTTP request (FR-34, FR-35, FR-37)."""
    surface_value = surface_label(surface)
    method_value = method_label(method)
    route_value = route_label(route)
    HTTP_REQUESTS.labels(
        surface=surface_value,
        method=method_value,
        route=route_value,
        status=status_label(status),
    ).inc()
    HTTP_REQUEST_DURATION.labels(
        surface=surface_value,
        method=method_value,
        route=route_value,
    ).observe(duration)


# --------------------------------------------------------------------------- #
# Exposition (spec §5.4)
# --------------------------------------------------------------------------- #


def render_metrics() -> bytes:
    """Serialise the current registry as Prometheus text.

    Single-process: the default ``REGISTRY``. Multi-process: a **fresh**
    ``CollectorRegistry`` holding *only* the ``MultiProcessCollector`` — the
    application metrics write their own per-process files, and reusing the
    default registry would double-export every family.

    The multi-process branch is the only thing today's deferred decision (D11)
    costs: it is here so that turning on multiple workers later is a deploy
    change, not a code change.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest(REGISTRY)
