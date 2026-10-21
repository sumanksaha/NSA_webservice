# Observability: Sentry + Prometheus — research and implementation guide

> **Status:** research / proposal (2026-10-17). Nothing in this document is
> implemented yet — README Level 7 "Observability" lists Prometheus + Grafana,
> Sentry, and distributed tracing as ⬜ not started (`README.md:827-833`).
>
> **Question answered:** where do Sentry and Prometheus actually pay off in
> *this* codebase, and what is the smallest, safest way to wire them in?
>
> **Audience:** whoever picks up the Level 7 observability work. Read §1–§2 for
> the decision, §3–§4 for the tool-specific design, §5 for the phased plan.

---

## 0. Scope

Two tools, one doc. They answer different questions and must not be conflated:

| Tool | Question it answers | Model |
| --- | --- | --- |
| **Sentry** | *"What broke, exactly once, with a stack trace, and is it new?"* | Push (SDK → Sentry) |
| **Prometheus** | *"How is the system behaving over time, in aggregate?"* | Pull (scraper → `/metrics`) |

Sentry is **per-event** (a single exception, grouped). Prometheus is
**per-series** (a number that changes over time). A legal-records app with
hash-chained audits needs both, plus the discipline that neither is the audit
trail — the audit trail stays in the DB (`app/services/audit.py`).

---

## 1. Where the project stands today (verified in-repo)

Everything below was confirmed by reading the files, not assumed.

| Area | Current state | Reference |
| --- | --- | --- |
| Server | FastAPI owns `/api/v2/*`; Flask mounted at `/` via `a2wsgi.WSGIMiddleware` | `asgi.py:88-310` |
| Worker model | `uvicorn asgi:app --host 0.0.0.0 --port 10000` — **single process, no `--workers`** | `render.yaml:43`, `Dockerfile:115` |
| Logging | structlog; JSON in prod, console in dev; stdlib loggers bridged through `ProcessorFormatter` | `app/utils/logging.py:44-101` |
| Config seam | one declaration table, `cfg.<attr>` accessor, `.env.example` parity enforced by a meta-test | `app/shared/config.py:67-1069`, `tests/test_shared_config.py:195` |
| Health probes | `GET /health` (DB + memory + WeasyPrint + QStash), `/health/cloudinary`, `/health/backups` (dead-man's switch), `GET /api/rag/health` | `app/health/routes.py:19,84,139`, `app/rag/routes.py:113` |
| Existing telemetry | `rag_query_log` already stores per-query `retrieval_latency_ms`, `total_latency_ms`, `groundedness_score`, `hallucination_detected`, `prompt_tokens`, `completion_tokens`, `llm_model`, `error` | `app/models/rag.py:32-72` |
| Telemetry writers | `RetrievalLogger` (retrieval phase) + `GenerationLogger` (generation phase), both **best-effort** — failures are logged and swallowed | `app/rag/generation/logger.py:85-153` |
| Background work | QStash webhooks (no Celery); recurring jobs declared in one `JOBS` tuple | `app/services/scheduled_jobs.py:43-74` |
| Error-handling style | deliberate best-effort `except Exception: pass/warning` across optional-infra paths (Ruff `S110`/`S112` globally ignored) | `pyproject.toml:205-210` |
| CSP violations | already collected, only `logger.warning`-ed | `app/health/routes.py:157-192` |
| Tests | conftest forces stub LLM + no network by default | `tests/conftest.py:133-184` |

**Two facts drive the whole design:**

1. **The app is a single uvicorn process today.** Prometheus multi-process mode
   (§4.5) is a *future* concern, not a current requirement. Do not pay its
   complexity up front; do keep the door open.
2. **The codebase swallows exceptions by design.** "Best-effort — a missing or
   unreachable Neo4j degrades to no expansion" (`RAG_KG_FUSION`), "best-effort —
   failures are logged and swallowed" (`GenerationLogger`). Those are correct
   *behaviours*, but today they are invisible. **This is Sentry's single biggest
   win in this repo** (§3.1).

---

## 2. Decision summary

| Decision | Choice | Why |
| --- | --- | --- |
| Sentry SDK | `sentry-sdk[flask]==2.72.0`, `FlaskIntegration` | One dependency; official Flask support; the logging integration captures already-swallowed warnings. **Pin 2.72.0** — docs.sentry.io documents a 3.x that is only available as alphas |
| Sentry init site | new `app/observability/sentry.py`, called from `create_app()` **and** `asgi.py` | Flask factory is where every entrypoint converges; ASGI adds FastAPI-error capture |
| Sentry gating | skip `init()` entirely when the DSN is blank | no DSN ⇒ no transport ⇒ **zero network in tests**. Never use `dsn=None` (the SDK falls back to `$SENTRY_DSN`) |
| Sentry release | `f"nsa-webservice@{RENDER_GIT_COMMIT}"` passed explicitly | the SDK does **not** auto-detect Render's commit var, and Render injects no Sentry env |
| Sentry PII | `send_default_pii=False`, `max_request_body_size="never"`, `before_send` scrubber | legal/FSSAI records: names, licence numbers, addresses, query text |
| Prometheus client | official `prometheus-client` (not `prometheus-flask-exporter`) | `/metrics` must sit on the **ASGI edge**; the exporter only sees Flask and would miss `/api/v2/rag/*` (the *latency-heavy* routes) |
| `/metrics` placement | `@app.get("/metrics")` on the FastAPI app in `asgi.py`, defined **before** the Flask catch-all mount | outside Flask's login/RBAC gate; one scrape surface for Flask + FastAPI; no trailing-slash redirect |
| Metric source split | **write-time** Counters/Histograms for events; **refresh-time** Gauges for SQL rollups | Prometheus is pull-based; never run a heavy aggregate inside a scrape (§4.4) |
| Scraper | Grafana Cloud "Metrics Endpoint" integration, 60 s interval, bearer token | free tier; no persistent Prometheus to run; **auth is mandatory** |
| Auth on `/metrics` | bearer token (`METRICS_TOKEN`), separate from `API_V2_KEY` | Grafana Cloud rejects unauthenticated public URLs |

Explicitly **out of scope** for the first landing: distributed tracing, OTel,
alert routing. They are noted as the natural next step (§7).

---

## 3. Sentry

### 3.1 Why it matters *here*: the swallowed-exception surface

This repo deliberately degrades instead of crashing — and says so in comments:

- *"Best-effort exception swallowing is a deliberate pattern across the
  RAG/backup layers (e.g. `except Exception: pass` on optional network calls
  that must not take the app down)"* (`pyproject.toml:205-210`, with Ruff `S110`
  and `S112` globally ignored).
- `GenerationLogger` / `RetrievalLogger`: *"All DB writes are best-effort —
  failures are logged and swallowed so a logging failure never breaks the
  generation pipeline."* (`app/rag/generation/logger.py:40-42`).
- `/health/backups` reports a dead-man's switch but nothing alerts on it.
- `/csp-report` collects browser violations and does nothing but
  `logger.warning` (`app/health/routes.py:191`).

That is the right *behaviour* and the wrong *observability*. Sentry's default
`LoggingIntegration` promotes `ERROR`-level stdlib log records to events — i.e.
it can turn these already-existing warnings into grouped, stack-traced,
owned issues **without editing a single call site**. That is the single
highest-value Sentry win in this codebase.

### 3.2 Install and version (read this before copying the docs)

| Fact | Value |
| --- | --- |
| Stable on PyPI | **`sentry-sdk==2.72.0`** (2026-10-12) |
| 3.x on PyPI | `3.0.0a1 … 3.0.0a7` — **alphas only, no final** |
| docs.sentry.io shows | the **3.x** flow (`pip install "sentry-sdk"`, `data_collection`, stream spans) |

The docs site has moved ahead of the installable stable line. **Pin
`"sentry-sdk[flask]==2.72.0"`** in `pyproject.toml`. On 2.x the `flask` extra is
real and required-ish — `FlaskIntegration.setup_once()` imports `blinker`
(`raise DidNotEnable("blinker is not installed")`) and `markupsafe`. The Redis
and SQLAlchemy integrations are **auto-enabled** with no extra.

### 3.3 Where `init()` goes

Sentry hooks Flask **signals**, not the app object, so init needs no app
instance — but it must run *as early as possible*. Add
`app/observability/sentry.py` and import it from `create_app()`
(`app/__init__.py:102`) and, for the FastAPI layer, from `asgi.py:34`.

```python
# app/observability/sentry.py
import logging, os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

log = logging.getLogger(__name__)


def init_sentry() -> None:
    """Error tracking. Zero network when disabled; never blocks boot."""
    from app.shared.config import cfg

    dsn = (cfg.sentry_dsn or "").strip()
    if not dsn:                      # unset => SDK never initialised => no transport
        return
    try:
        sentry_sdk.init(
            dsn=dsn,                 # explicit: never rely on the env fallback (see below)
            environment=cfg.sentry_environment or ("production" if os.environ.get("RENDER") else "development"),
            release=f"nsa-webservice@{os.environ.get('RENDER_GIT_COMMIT', 'dev')}",
            send_default_pii=False,
            max_request_body_size="never",
            traces_sample_rate=cfg.sentry_traces_sample_rate,   # 0.0 by default
            integrations=[FlaskIntegration(http_methods_to_capture=("GET", "POST", "PUT", "PATCH", "DELETE"))],
            ignore_errors=["werkzeug.exceptions.NotFound", "werkzeug.exceptions.Unauthorized"],
            before_send=before_send,
        )
    except Exception:
        # A malformed DSN raises BadDsn out of Dsn(...) inside Transport.__init__,
        # unguarded. Never let a typo take the deploy down.
        log.warning("Sentry init failed; continuing without error tracking", exc_info=True)
```

### 3.4 The "off" switch has a trap

- With **no DSN there is no transport** (`make_transport()` ends in
  `if transport is None and options["dsn"]: ...`), so skipping `init()` is a
  true zero-network off switch — which is what `tests/conftest.py`'s no-network
  contract needs.
- **`dsn=None` is *not* an off switch.** `_get_options()` does
  `if rv["dsn"] is None: rv["dsn"] = os.environ.get("SENTRY_DSN")`, so an
  exported DSN in a dev shell would silently turn Sentry on. Either don't call
  `init()` at all (preferred, as above) or pass `dsn=""`.
- A **malformed DSN raises**, so `init()` is wrapped (§3.3). This matches
  `setup_logging`'s "must never block application boot" contract
  (`app/utils/logging.py:12-15`).

### 3.5 Capture strategy: let logging do the work

Keep the default `LoggingIntegration`. It forwards `ERROR` (and above) log
records as events, plus breadcrumbs for `INFO`+. Because
`app/utils/logging.py` already bridges *every* stdlib logger through structlog's
`ProcessorFormatter`, the swallowed warnings and errors listed in §3.1 become
tracked issues with no code changes at the call sites.

Two details that matter:

- **uvicorn may reset logging config** (Sentry's own docs say so). Ensure the
  handler setup is authoritative — `setup_logging()` already replaces
  `root.handlers` (`app/utils/logging.py:87-90`), which is the equivalent of
  `force=True`.
- Prefer this over sprinkling `sentry_sdk.capture_exception()` into the
  best-effort `except` blocks: it preserves the deliberate-degradation design
  and keeps Sentry a pure consumer of existing signals.

### 3.6 PII: this is a legal/FSSAI records app

Checklist for the first landing:

- `send_default_pii=False` (the SDK default is `None`, which is not the same
  thing — set it explicitly).
- `max_request_body_size="never"` so audit/legal payloads are never captured.
- A `before_send` that drops `request.data` / cookies and hashes or filters
  anything identifying: `fso_name`, licence numbers, addresses, query text.
- `EventScrubber(recursive=True)` if the built-in denylist is not enough —
  it is **not recursive by default**.
- Never put raw `RecordAudit` bodies or chain hashes into `set_context` /
  `set_tag`. Docs: *"scrub any data before it is sent, to ensure that sensitive
  data never leaves the local environment."* Sentry is an error sink; it is not
  a second copy of the audit trail.

### 3.7 Correlating Sentry issues with structlog lines

There is **no official structlog integration**. The clean route is the logging
bridge: because `app/utils/logging.py` routes stdlib records through
`structlog.stdlib.ProcessorFormatter`, fields passed in `extra` are promoted to
searchable top-level Sentry attributes. Add the app's request id (and
`audit.actor_role`) in the existing `before_request` hook next to
`set_audit_user` (`app/__init__.py:378-386`):

```python
@app.before_request
def _bind_observability_context():
    sentry_sdk.set_tag("request_id", g.get("request_id", ""))
    sentry_sdk.set_context("audit", {"actor_role": role_of(current_user)})
```

Tags are indexed/searchable; context is not. Prefer `id`/`username` over email,
and hash anything that must stay linkable but not readable.

### 3.8 The a2wsgi double-transaction caveat (ASGI reality check)

`FlaskIntegration` patches the WSGI entry point — `Flask.__call__ = sentry_patched_wsgi_app`
— and a2wsgi invokes exactly that, so **Flask-side capture works under
`uvicorn asgi:app`**. But `FastApiIntegration`/`StarletteIntegration` are
**auto-enabled** when FastAPI/Starlette are importable, so a single request can
produce **two server transactions**: the outer ASGI one and the inner WSGI one.

Consequences for `asgi.py`:

- Error capture is duplicated-but-harmless (Sentry groups on the exception).
- Transaction volume/cost is doubled. De-duplicate with a `traces_sampler`
  that drops the bridged WSGI `http.server` transaction, or scope sampling to
  one layer. Verify with `debug=True` or a stub `transport` before enabling
  tracing.
- On the ASGI side, 4xx are **already excluded** — `failed_request_status_codes`
  defaults to `{500..599}`, so `/api/v2/*` 401/404 responses won't create
  issues. Flask-side 404/401 come through as `werkzeug.exceptions.*` and are
  handled by `ignore_errors` + `before_send` (§3.3, §3.10).

### 3.9 Release, environment, and cron monitoring

- **`RENDER_GIT_COMMIT` is *not* auto-detected.** The SDK's
  `get_default_release()` checks `SENTRY_RELEASE`, local git, `HEROKU_BUILD_COMMIT`,
  `SOURCE_VERSION`, `CIRCLE_SHA1`, … — Render is absent. Pass
  `release=f"nsa-webservice@{os.environ['RENDER_GIT_COMMIT']}"` explicitly
  (Render documents the variable as the deploy commit SHA, available at runtime).
- **Render has no Sentry env-var integration.** Its docs list Datadog,
  QuotaGuard and Formspree under third-party integrations; Sentry appears only
  as an OTel/log-stream *destination*. So set `SENTRY_DSN` yourself as a Render
  secret — nothing is injected for you.
- **Cron monitoring** is a good fit for the QStash jobs in
  `app/services/scheduled_jobs.py`: wrap the scheduled handlers with
  `sentry_sdk.monitor(...)` so a backup/ingestion job that stops firing, or
  fails, raises a *missed* check-in — the same dead-man's-switch idea as
  `/health/backups`, but pushed instead of polled.

### 3.10 Noise control (this app generates 4xx on purpose)

- `ignore_errors=["werkzeug.exceptions.NotFound", "werkzeug.exceptions.Unauthorized"]`
  — entries match bare names or `module.Type`, or accept exception classes.
- `before_send` returning `None` for `werkzeug.exceptions.*` / CSRF failures and
  hostile-bot traffic.
- **There is no `error_sample_rate` option.** Errors are controlled by
  `sample_rate` (static) or `error_sampler` (dynamic, which overrides
  `sample_rate`). `traces_sample_rate`/`traces_sampler` govern **transactions
  only** — they do not sample errors. Do not confuse them.
- The SDK has no client-side rate limit beyond sampling; the server's 429 +
  `Retry-After` handling (and Sentry's spike protection) is the real lever.
  Leave `enable_backpressure_handling=True` (default) on small Render instances.
- Drop `/health` transactions via `before_send_transaction`.

### 3.11 What Sentry adds that structlog cannot

Grouping (N log lines → one issue), stack traces with source context and
locals, issue ownership/routing, regression detection and suspect commits (needs
`release`), release-health sessions, uptime + cron monitors, and — if tracing is
later enabled — SQLAlchemy/Redis spans that expose N+1 queries, which log text
cannot show.

## 4. Prometheus

### 4.1 Where the endpoint lives (the ASGI/Flask split decides it)

A request to `/inspection` and a request to `/api/v2/rag/generate` enter the
same uvicorn process but are served by **different apps** (`asgi.py:184-310`):
FastAPI handles `/api/v2/*` natively, Flask handles everything else under the
`mount("/")`. `prometheus-flask-exporter` only instruments Flask, so the two
LLM endpoints — the slowest routes in the system — would be **invisible**.

`/metrics` must therefore be served by FastAPI, defined **before** the Flask
catch-all mount:

```python
# asgi.py — after the /api/v2 routes, BEFORE app.mount("/", WSGIMiddleware(...))

from prometheus_client import CONTENT_TYPE_LATEST
from starlette.responses import Response

from app.observability.metrics import render_metrics   # registry logic lives here (§4.5)

@app.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus scrape endpoint. Bearer-token guarded (Grafana Cloud
    requires auth on public URLs) and outside Flask's login/RBAC gate."""
    token = os.environ.get("METRICS_TOKEN", "")
    if token and request.headers.get("authorization") != f"Bearer {token}":
        return Response(status_code=401)
    return Response(render_metrics(), media_type=CONTENT_TYPE_LATEST)
```

> **Why a hand-written route instead of `make_asgi_app()`?** On the current
> release (`prometheus-client` 0.26.0) `make_asgi_app()` + `app.add_route()`
> raises `TypeError`; the documented workaround is `app.mount("/metrics", ...)`,
> which serves at `/metrics/` and 307-redirects `/metrics`. Grafana Cloud's
> metrics integration states *"redirects are not supported"*. A plain `@app.get`
> handler avoids the redirect entirely, gives one obvious place for the token
> check, and works identically in single- and multi-process mode.

### 4.2 Naming and cardinality rules (non-negotiable)

From Prometheus' naming guidance:

- App prefix on every metric: **`nsa_`** (`process_` and `scrape_` are reserved).
- Base units, plural suffix: **`_seconds`**, **`_bytes`**, ratios 0–1. Counters
  end in **`_total`**.
- Never label with anything unbounded. In *this* app that means **never**:
  `query` (free-text legal question), `fso_name`, `case_id`, `inspection_id`,
  `license_no`, `content_hash`. The naming docs: *"every unique combination of
  key-value label pairs represents a new time series … Do not use labels to
  store dimensions with high cardinality."*
- Target **< 10 values per label**; anything past ~100 is a design bug.
- Label the HTTP route with the **rule/endpoint, not the interpolated path** —
  `/inspection/<int:inspection_id>`, never `/inspection/4821`.
- The one bounded dimension this domain *does* have is `query_type` — the
  pipeline already classifies it into a small enum
  (`section_lookup | case_law | provision_search | general_qa | amendment_query`,
  `app/models/rag.py:44-46`). Use that, never the query text.

### 4.3 Metric catalogue for this project

**Events — Counters/Histograms, recorded at write time:**

| Metric | Type | Labels (cardinality) |
| --- | --- | --- |
| `nsa_http_requests_total` | Counter | `surface`(flask\|fastapi), `method`, `route`(rule), `status` |
| `nsa_http_request_duration_seconds` | Histogram | `surface`, `method`, `route` |
| `nsa_http_requests_in_progress` | Gauge (`livesum`) | `route` |
| `nsa_rag_queries_total` | Counter | `query_type`, `pipeline`(legacy\|agent), `outcome`(ok\|error\|abstained\|degraded) |
| `nsa_rag_retrieval_duration_seconds` | Histogram | `stage`(dense\|sparse\|rerank\|kg\|sql) |
| `nsa_rag_pipeline_duration_seconds` | Histogram | `pipeline` |
| `nsa_llm_generation_duration_seconds` | Histogram | `model` |
| `nsa_llm_generations_total` | Counter | `model`, `mode`(live\|stub) |
| `nsa_llm_tokens_total` | Counter | `model`, `direction`(prompt\|completion) |
| `nsa_rag_hallucinations_total` | Counter | `query_type` |
| `nsa_rag_degraded_total` | Counter | `reason`(stub_llm\|circuit_open\|no_qdrant\|no_kg) |
| `nsa_qstash_tasks_total` | Counter | `task`, `outcome`(ok\|failed) |
| `nsa_qstash_task_duration_seconds` | Histogram | `task` |
| `nsa_csp_violations_total` | Counter | `directive`(bounded set from Talisman) |

**Snapshots — Gauges, refreshed on a schedule (never inside a scrape):**

| Metric | Type | Source |
| --- | --- | --- |
| `nsa_rag_groundedness_avg` | Gauge (`mostrecent`) | `avg(rag_query_log.groundedness_score)` over 24 h |
| `nsa_rag_hallucination_rate` | Gauge (`mostrecent`) | share of `hallucination_detected` over 24 h |
| `nsa_rag_queries_24h` | Gauge | `count(rag_query_log)` over 24 h |
| `nsa_backup_last_success_timestamp_seconds` | Gauge (`mostrecent`) | mirrors `/health/backups`'s `last_backup_status()` |
| `nsa_db_pool_checkedout` | Gauge | `db.engine.pool.checkedout()` |
| `nsa_build_info` | Info | `version`, `git_sha` (`RENDER_GIT_COMMIT`) |

The `/health/backups` endpoint already implements the freshness rule
(26 h threshold, `app/health/routes.py:139-154`); `nsa_backup_last_success_timestamp_seconds`
turns the same fact into an alertable series instead of a 503 a human has to poll.

### 4.4 Two instrumentation patterns, and which data goes where

Prometheus is **pull-based and synchronous**: *"exporter[s] should not perform
scrapes based on their own timers. … If a metric is particularly expensive to
retrieve … it is acceptable to cache it."* So:

**(A) Write-time instrumentation** — the cheap, faithful, aggregation-safe way
to cover *events*. Each metric call is a counter bump; it works across workers
and needs no background job. Wire into the places that already observe the data:

| Site | What to record |
| --- | --- |
| `asgi.py` (Starlette middleware, `/api/v2/*` only) | HTTP count/latency/in-progress for FastAPI routes |
| `app/__init__.py` `before_request`/`after_request` (Flask-served paths only) | same metrics, `surface="flask"` |
| `app/rag/retrieval/logger.py::RetrievalLogger` | `nsa_rag_retrieval_duration_seconds`, `nsa_rag_queries_total{outcome}`, `nsa_rag_degraded_total` |
| `app/rag/generation/logger.py::GenerationLogger` | `nsa_rag_pipeline_duration_seconds`, `nsa_llm_generation_duration_seconds`, `nsa_llm_generations_total`, `nsa_llm_tokens_total`, `nsa_rag_hallucinations_total` |
| `app/tasks_webhook/` handler | `nsa_qstash_tasks_total`, `nsa_qstash_task_duration_seconds` |
| `app/health/routes.py::csp_report` | `nsa_csp_violations_total` |

> **Put the metric call outside the `try` that swallows DB errors.** Both
> loggers are *"best-effort — failures are logged and swallowed"*
> (`generation/logger.py:40-42`). A metric that only fires when the DB write
> succeeds would under-count exactly when the pipeline is unhealthy.

**Do not double count.** The Flask hooks must skip `/api/v2` (FastAPI never
forwards it to Flask, so the two surfaces are disjoint by construction) and both
surfaces must skip `/metrics`, `/health`, `/api/v2/health`. Use `surface` as the
disambiguating label so a dashboard can split them.

**(B) Refresh-time gauges** — for SQL rollups that only exist as aggregates.
A scheduled job sets the gauge; the scrape reads memory. This is
**multiprocess-safe**, unlike a custom `Collector` (*"Custom collectors do not
work"* in multi-process mode) and avoids a DB query inside a 10 s scrape timeout.

The idiomatic home is the existing recurring-job registry — add one row to
`JOBS` in `app/services/scheduled_jobs.py:43` plus a handler in
`app/scripts/` (mirroring `cleanup_rag_logs.py`), and register the task key in
`app/utils/qstash_client.py::TASK_REGISTRY`:

```python
ScheduledJob(
    name="refresh_observability_metrics",
    flag_key="METRICS_ENABLED",
    cron_key=None,
    default_cron="*/5 * * * *",          # every 5 minutes
    description="Refresh snapshot gauges (RAG quality, backup freshness, DB pool).",
)
```

Set the gauge even when the query returns nothing (`Gauge.set(0.0)`), so a
vanished series is a visible drop, not a stale "last known good".

### 4.5 Multi-process / multi-worker (future-proofing, not today)

Today `render.yaml:43` runs **one** uvicorn process, so naive in-memory metrics
are correct. If `--workers N` is ever added, each worker keeps its own counters
and a scrape reports whichever worker answered — `rate()` then resets randomly.
The official mechanism:

- `PROMETHEUS_MULTIPROC_DIR` **must be set before `prometheus_client` is first
  imported** (the value-class is chosen at import: *"This needs to be chosen
  before the first metric is constructed"*). Set it in `render.yaml` `envVars`
  or the start command — **never** from Python.
- The scrape registry must be **fresh** and contain **only**
  `multiprocess.MultiProcessCollector` (app metrics stay on the default
  `REGISTRY` and write their own mmap files); otherwise metrics are exported
  twice.
- Wipe the directory on every boot and reap dead workers with
  `multiprocess.mark_process_dead(pid)` from a `child_exit` hook. **`uvicorn
  --workers` has no documented `child_exit` hook** — use
  `gunicorn -k uvicorn.workers.UvicornWorker --workers N` (gunicorn is already a
  dependency) if this is ever needed.
- Gauge aggregation mode must be chosen per gauge (`livesum` for in-progress,
  `mostrecent` for snapshots). Under `mostrecent`, `inc()`/`dec()` raise — use
  `set()`.
- **Also broken in multi-process mode:** custom collectors, `set_function`,
  Info/Enum metrics, `process_*`/`python_*` per-worker metrics, exemplars.
  This is the reason §4.4 chooses scheduled gauges over a custom collector.

That registry choice is the only thing `render_metrics()` has to encapsulate:

```python
# app/observability/metrics.py
import os
from prometheus_client import REGISTRY, CollectorRegistry, generate_latest, multiprocess


def render_metrics() -> bytes:
    """Single-process: the default registry. Multi-process: a fresh registry
    holding ONLY the MultiProcessCollector (app metrics write their own files)."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest(REGISTRY)
```

### 4.6 Scrape path: Grafana Cloud free tier + auth

- Use the **Grafana Cloud "Metrics Endpoint" integration** — it scrapes any
  public Prometheus/OpenMetrics URL with no agent to run. The docs are explicit:
  *"it is mandatory to provide authentication details, and Grafana Cloud won't
  accept a public URL that is not protected by authentication."*
- Therefore `METRICS_TOKEN` is a required secret when metrics are enabled, and
  `/metrics` returns 401 without it. Do **not** put `/metrics` behind Flask's
  login gate (a scraper cannot log in) and do **not** leave it public.
- Interval **60 s** (the integration default). Grafana Cloud bills on active
  series × data points per minute, so keep label cardinality low; consider
  `PROMETHEUS_DISABLE_CREATED_SERIES=true` to drop the extra `_created` series
  the Python client emits for every Counter/Histogram.
- Render's built-in metrics cover *host* signals (CPU, memory, HTTP volume and
  p50/p75/p90/p99 latency — the latter Pro-plan only) and **not** domain signals
  (groundedness, hallucination rate, LLM tokens/spend). The two are
  complementary; both can land in the same Grafana stack.

### 4.7 Alerts worth shipping on day one

These are the ones tied to real, already-documented failure modes in this repo:

| Alert | Expression (sketch) | Why it matters here |
| --- | --- | --- |
| Endpoint down | `up == 0` | also covers Render killing the service |
| 5xx rate | `sum(rate(nsa_http_requests_total{status=~"5.."}[5m])) / sum(rate(nsa_http_requests_total[5m])) > 0.02` | baseline error budget |
| **Stub LLM in production** | `increase(nsa_llm_generations_total{mode="stub"}[15m]) > 0` | `/api/rag/health` exists precisely because *"stub answers look plausible but are canned"* (`app/rag/routes.py:120-122`) — this alert is the automated form of that audit |
| RAG degrading | `increase(nsa_rag_degraded_total[15m]) > 0` | circuit-open / no-Qdrant / no-Neo4j fallbacks are silent by design |
| Groundedness drop | `nsa_rag_groundedness_avg < 0.6` | answers still return, quality silently falls |
| Hallucination spike | `nsa_rag_hallucination_rate > 0.2` | legal-answer correctness risk |
| Backup stale | `time() - nsa_backup_last_success_timestamp_seconds > 26*3600` | codifies `/health/backups`'s rule as an alert |
| LLM latency | `histogram_quantile(0.95, sum by (le) (rate(nsa_llm_generation_duration_seconds_bucket[15m]))) > 30` | user-visible stall; free-tier timeout risk |
| Memory near cap | `process_resident_memory_bytes > 4.8e8` | the 512 MB Render free tier is a documented constraint (`RAG_EMBED_REMOTE_FALLBACK` exists to avoid OOM) |

Use histograms (never summaries) for latency: the Python client's `Summary` does
**not** compute quantiles locally — *"If you need p50/p95/p99, use a Histogram
instead"* — and precomputed quantiles cannot be aggregated across workers.

### 4.8 Bucket layouts

```python
HIST_HTTP = Histogram(                       # default web buckets are fine
    "nsa_http_request_duration_seconds", "HTTP latency", ["surface", "method", "route"],
)
HIST_LLM = Histogram(
    "nsa_llm_generation_duration_seconds", "LLM generation latency", ["model"],
    buckets=[.1, .25, .5, 1, 2, 3, 5, 8, 13, 21, 34, 55],   # multi-second tail
)
HIST_PIPELINE = Histogram(
    "nsa_rag_pipeline_duration_seconds", "End-to-end RAG latency", ["pipeline"],
    buckets=[.25, .5, 1, 2, 4, 8, 15, 30, 60, 120],
)
```

The default buckets top out at 10 s — too low for an LLM answer over a
cross-encoder rerank plus Neo4j expansion. Each bucket is a live series, so keep
`model` to a small bounded set.

## 5. Implementation plan

Five small, independently shippable phases. Each is flag-gated and each leaves
the app byte-for-byte unchanged when the flag is off — the codebase's standing
convention (`FoodView.for_query`, `ENABLE_PWA`, …).

### Phase 0 — config seam (no behaviour)

Add the settings to the declaration table and `.env.example`; the meta-test
`tests/test_shared_config.py::test_env_example_keys_are_declared` forces both.

| Key | `cfg` attr | Type | Default | Convention |
| --- | --- | --- | --- | --- |
| `SENTRY_DSN` | `sentry_dsn` | str | `""` | — (blank ⇒ disabled) |
| `SENTRY_ENVIRONMENT` | `sentry_environment` | str | `""` | falls back to APP_ENV/RENDER |
| `SENTRY_TRACES_SAMPLE_RATE` | `sentry_traces_sample_rate` | float | `0.0` | — |
| `METRICS_ENABLED` | `metrics_enabled` | bool | `False` | opt_in |
| `METRICS_TOKEN` | `metrics_token` | str | `""` | secret |

PII is deliberately **not** a flag: `send_default_pii` is hard-wired `False`
(§3.6) because a setting that invited someone to enable it would be a footgun in
a legal-records app.

Files: `app/shared/config.py:67-1069` (rows), `.env.example`, `render.yaml`
(both services). `PROMETHEUS_MULTIPROC_DIR` is deliberately **not** a table row —
it is read by the client at import time and must never be resolved through
`cfg` (§4.5).

### Phase 1 — Sentry

- Add `"sentry-sdk[flask]==2.72.0"` to `pyproject.toml`
  `[project.dependencies]` (pinned; see §3.2).
- New `app/observability/sentry.py` with `init_sentry()`; call it from
  `create_app()` and `asgi.py`.
- Skip `init()` entirely when the DSN is blank (never pass `dsn=None`); wrap
  `init()` in `try/except` so a malformed DSN cannot block boot (§3.4).
- Keep the default logging integration so already-swallowed `ERROR`/`WARNING`
  records become issues with no call-site edits (§3.5).
- Add the PII guards in the same change — `send_default_pii=False`,
  `max_request_body_size="never"`, `before_send`, `EventScrubber(recursive=True)`
  (§3.6).
- De-duplicate the ASGI/WSGI transaction pair before ever enabling tracing
  (§3.8). Default `SENTRY_TRACES_SAMPLE_RATE=0.0` ships as errors-only.
- Optionally wrap the `app/services/scheduled_jobs.py` handlers with
  `sentry_sdk.monitor(...)` so a QStash job that stops firing raises a missed
  check-in (§3.9).
- Tests: blank DSN ⇒ `sentry_sdk.init` never called; non-blank DSN ⇒ called with
  the expected kwargs; malformed DSN ⇒ boot continues; `before_send` scrubs a
  hostile event fixture (assert no `request.data`, no raw licence number/name).

### Phase 2 — Prometheus HTTP + `/metrics`

- New `app/observability/metrics.py` — all metric definitions in **one** module
  (a single namespace, mirroring the single-config-seam discipline).
- `asgi.py`: `@app.get("/metrics")` before the Flask mount; Starlette middleware
  for `/api/v2/*` request metrics.
- `app/__init__.py`: Flask `before_request`/`after_request` hooks for the
  Flask-served surface.
- Tests: `/metrics` returns 401 without the token and 200 with it; route labels
  use the rule (assert `/inspection/<int:...>` not a concrete id);
  `/api/v2` is not double-counted.

### Phase 3 — domain metrics

- Metric calls in `RetrievalLogger` / `GenerationLogger` (outside the swallowed
  `try`), the QStash webhook handler, and `csp_report`.
- `refresh_observability_metrics` scheduled job + `TASK_REGISTRY` entry +
  handler under `app/scripts/`.
- Tests: metrics module is import-safe with `METRICS_ENABLED=false`; the refresh
  handler sets gauges from a seeded `rag_query_log`.

### Phase 4 — scraper, dashboards, alerts

- Grafana Cloud Metrics Endpoint integration pointing at
  `https://<service>/metrics` with the bearer token; 60 s interval.
- Two dashboards: **Platform** (HTTP, pool, memory, backups) and **RAG Quality**
  (queries by type, groundedness, hallucination rate, tokens, p95 latency).
- Alert rules from §4.7.

**Rollback:** unset the flag / delete the env var. Phase 2–3 add no schema, no
migration, and no change to any existing response.

---

## 6. Risks and anti-patterns

| Risk | Guard |
| --- | --- |
| Metric label cardinality explosion | label only with bounded enums/rules; never query text, ids, or names (§4.2) |
| Sentry leaking PII from a legal/FSSAI app | `send_default_pii=False` + explicit `before_send` scrubber; never send request bodies |
| Scrape-time DB load | snapshots come from scheduled gauges, never a query inside a scrape (§4.4) |
| Double-counting HTTP metrics | `surface` label; `/api/v2` only on the ASGI middleware, everything else only in Flask hooks |
| Sentry double server transactions (a2wsgi ASGI→WSGI) | de-duplicate via `traces_sampler`/`before_send_transaction` before enabling tracing |
| Tests hitting the network | both integrations are no-ops when unset; asserts live in `tests/` |
| `/metrics` publicly readable | bearer token mandatory when Grafana Cloud is the scraper |
| Multi-process silently wrong numbers | documented trigger (§4.5); re-check before ever adding `--workers` |
| Sentry noise (404s, auth failures, hostile bots) | `ignore_errors` + `before_send` for 401/404 and CSRF, plus per-issue rate limits |
| Treating metrics/Sentry as the audit trail | they are not — `RecordAudit` stays the hash-chained record of truth |

---

## 7. Open decisions

1. **Error-only vs tracing.** Start with errors + logs (cheap, high value).
   Enable `SENTRY_TRACES_SAMPLE_RATE` only once someone will read the traces.
2. **Grafana Cloud vs Render-native.** Render covers host metrics but not
   domain metrics; Grafana Cloud covers both app-level and (via Render metrics
   streaming, Pro+) platform metrics. Default: Grafana Cloud free tier.
3. **OTel later?** If LLM-latency *tracing* becomes the goal, OpenTelemetry is
   the better long-term bet (one API for traces + metrics + logs, and both
   Render and Grafana Cloud ingest OTLP). Metrics-only does not justify it yet.
4. **Where do alerts go?** No on-call channel is defined in the repo; Grafana
   Alerting can fan out to email/Slack without new infrastructure.
5. **Should the refresh job be a QStash schedule or an in-process thread?**
   QStash matches the existing pattern (§4.4) but requires credentials; an
   in-process daemon thread needs no infrastructure and is fine while there is
   one worker.

## 8. Sources (primary only)

### Sentry

- Flask integration — https://docs.sentry.io/platforms/python/integrations/flask/
- ASGI integration — https://docs.sentry.io/platforms/python/integrations/asgi/
- WSGI integration — https://docs.sentry.io/platforms/python/integrations/wsgi/
- FastAPI integration — https://docs.sentry.io/platforms/python/integrations/fastapi/
- Integrations index (auto-enabled table) — https://docs.sentry.io/platforms/python/integrations/
- Logging integration — https://docs.sentry.io/platforms/python/integrations/logging/
- Options reference (`dsn`, `release`, `environment`, `sample_rate`, `error_sampler`, `ignore_errors`, `send_default_pii`, `data_collection`, `event_scrubber`, `traces_sample_rate`, `traces_sampler`, `max_request_body_size`, `enable_backpressure_handling`) — https://docs.sentry.io/platforms/python/configuration/options/
- Sampling — https://docs.sentry.io/platforms/python/sampling/
- Filtering (`before_send`, hints, `before_send_transaction`) — https://docs.sentry.io/platforms/python/configuration/filtering/
- Scrubbing sensitive data — https://docs.sentry.io/platforms/python/data-management/sensitive-data/
- Users / Tags / Context / Scopes — https://docs.sentry.io/platforms/python/enriching-events/identify-user/, …/tags/, …/context/, …/scopes/
- Releases — https://docs.sentry.io/platforms/python/configuration/releases/
- Sessions / release health — https://docs.sentry.io/platforms/python/configuration/sessions/
- Quotas, rate limits, spike protection — https://docs.sentry.io/pricing/quotas/manage-event-stream-guide/
- Cron monitoring — https://docs.sentry.io/product/monitors-and-alerts/monitors/crons/
- Uptime monitoring — https://docs.sentry.io/product/monitors-and-alerts/monitors/uptime-monitoring/
- Source: `sentry_sdk/utils.py` (`Dsn`/`BadDsn`, `get_default_release`) — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/utils.py
- Source: `sentry_sdk/client.py` (`_get_options` DSN fallback, `make_transport`) — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/client.py
- Source: `sentry_sdk/integrations/flask.py` (patches `Flask.__call__`) — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/integrations/flask.py
- Source: `sentry_sdk/integrations/wsgi.py` (`SentryWsgiMiddleware` starts the transaction) — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/integrations/wsgi.py
- PyPI metadata (version + `flask` extra) — https://pypi.org/pypi/sentry-sdk/json
- Render default env vars (`RENDER_GIT_COMMIT`, `PORT`) — https://render.com/docs/environment-variables
- Render log streams (Sentry **not** listed as a provider) — https://render.com/docs/log-streams
- Render metrics streams (OTel) — https://render.com/docs/metrics-streams

### Prometheus

- `prometheus/client_python` README + releases — https://github.com/prometheus/client_python
- Multiprocess mode — https://prometheus.github.io/client_python/multiprocess/
- Exporting → Flask / ASGI / FastAPI+Gunicorn — https://prometheus.github.io/client_python/exporting/http/flask/, …/http/asgi/, …/http/fastapi-gunicorn/
- Custom collectors — https://prometheus.github.io/client_python/collector/custom/
- Registry (`CollectorRegistry`) — https://prometheus.github.io/client_python/registry/
- Instrumenting → Counter / Gauge / Histogram / Summary / Info — https://prometheus.github.io/client_python/instrumenting/
- Source: `prometheus_client/values.py` (multiproc chosen at import), `metrics.py` (`_created`, `DEFAULT_BUCKETS`) — https://github.com/prometheus/client_python
- Metric types — https://prometheus.io/docs/concepts/metric_types/
- Metric and label naming — https://prometheus.io/docs/practices/naming/
- Histograms and summaries — https://prometheus.io/docs/practices/histograms/
- Instrumentation practices (cardinality, counter vs gauge) — https://prometheus.io/docs/practices/instrumentation/
- Writing exporters (synchronous scrape, custom-collector rules) — https://prometheus.io/docs/instrumenting/writing_exporters/
- Configuration (`scrape_interval`, `scrape_timeout`, `basic_auth`, `authorization`) — https://prometheus.io/docs/prometheus/latest/configuration/configuration/
- Grafana Cloud metrics endpoint integration (mandatory auth, 60 s default) — https://grafana.com/docs/grafana-cloud/monitor-infrastructure/integrations/integration-reference/integration-metrics-endpoint/
- Grafana Cloud metrics pricing (active series × DPM) — https://grafana.com/docs/grafana-cloud/platform/pricing-and-usage/metrics/
- Render service metrics (host-only; HTTP latency is Pro+) — https://render.com/docs/service-metrics
- OpenTelemetry Python exporters (OTLP vs Prometheus) — https://opentelemetry.io/docs/languages/python/exporters/


