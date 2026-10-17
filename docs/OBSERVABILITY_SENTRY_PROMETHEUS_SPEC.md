# Specification: Observability — Sentry Error Tracking and Prometheus Metrics

**Status:** specification only — no code, tests, or config changed.
**Date:** 2026-10-17.
**Implements:** [OBSERVABILITY_SENTRY_PROMETHEUS_RESEARCH.md](./OBSERVABILITY_SENTRY_PROMETHEUS_RESEARCH.md)
(primary-source research, tool-specific design, five-phase plan).
**Covers:** [`README.md:827-833`](../README.md) — Level 7 "Observability", the two ⬜ items only:
*Monitoring (Prometheus + Grafana)* and *Error tracking (Sentry)*. Distributed tracing stays ⬜
(§2.1).

**How to read this document.** Sections 3–6 are **normative**: `MUST`, `MUST NOT`, `SHOULD`, and
`MAY` carry their usual force. Section 7 onward is explanatory except where it carries an ID. Every
requirement has an ID (`FR-n`, `NFR-n`, `INV-n`, `V-n`, `SEC-n`, `AUD-n`, `MET-*`, `AL-n`, `AC-n`,
`D-n`) and appears in the traceability matrix (§13). Line citations are to the working tree as of
2026-10-17.

---

## 1. Problem statement

`README.md:827-833` lists Prometheus + Grafana and Sentry as ⬜. The other two Level 7 items —
structured logging (structlog) and `/health` — are ✅.

The system's failure modes are **deliberately silent**, and that is the whole problem:

- Ruff `S110` (try-except-pass) and `S112` (try-except-continue) are ignored **globally**
  (`pyproject.toml:209-210`), and the code documents why: *"Best-effort exception swallowing is a
  deliberate pattern across the RAG/backup layers."*
- `RetrievalLogger` / `GenerationLogger` — *"All DB writes are best-effort — failures are logged and
  swallowed so a logging failure never breaks the generation pipeline"*
  (`app/rag/generation/logger.py:40-42`).
- `/health/backups` implements a 26-hour freshness rule (`app/health/routes.py:139-154`) but nothing
  polls it; a failed backup is a 503 that no scheduled process reads.
- `/csp-report` collects browser violations and does nothing but `logger.warning`
  (`app/health/routes.py:191`).

At the same time the RAG pipeline already **writes** the numbers that would answer "is the system
healthy?": `rag_query_log` carries `retrieval_latency_ms`, `total_latency_ms`,
`groundedness_score`, `hallucination_detected`, `prompt_tokens`, `completion_tokens`, `llm_model`,
`query_type`, `pipeline`, and `error` (`app/models/rag.py:43-62`). Those columns are readable only by
hand.

The service is one uvicorn process serving **two apps**: FastAPI owns `/api/v2/*` natively and Flask
is mounted at `/` through `a2wsgi.WSGIMiddleware` (`asgi.py:37`, `asgi.py:310`, `render.yaml:43`).
`/api/v2/rag/generate` and `/api/v2/rag/query/agent` are the two slowest routes in the system and
are **invisible to any Flask-only instrumentation**.

This spec fixes (a) where each tool attaches in *this* codebase, and (b) what must hold so that
neither becomes a PII leak, a liability, or a second audit trail.

---

## 2. Scope and specified decisions

The research document left several choices open. This spec is written against the following
defaults. **Each is a single-point change if the decision flips**, marked inline.

| ID | Decision | Specified as | Flip cost |
| --- | --- | --- | --- |
| **D1** | Which Sentry SDK version? | **`sentry-sdk[flask]==2.72.0`** — the stable PyPI line. `3.0.0a1…a7` are **alphas only** while `docs.sentry.io` already documents the 3.x flow, so the docs site is *ahead* of what is installable. The `flask` extra is real on 2.x: `FlaskIntegration.setup_once()` imports `blinker` and `markupsafe`. | Version bump + re-run suite |
| **D2** | Where does `sentry_sdk.init()` run? | **Exactly one site: `create_app()`** (`app/__init__.py:102`). Both entrypoints converge there — `asgi.py:37` calls `create_app()`, and the WSGI module-level app is `create_app()` (`app/__init__.py:517`). A second call inside `asgi.py` **MUST NOT** be added. FastAPI/Starlette integrations are auto-enabled because FastAPI is importable. | One import |
| **D3** | The "off" switch | **Skip `sentry_sdk.init()` entirely when the DSN is blank.** `dsn=None` is **not** an off switch — `_get_options()` falls back to `os.environ["SENTRY_DSN"]`, so a DSN exported in a dev shell would silently switch Sentry on. | None |
| **D4** | Release identifier | **`f"nsa-webservice@{RENDER_GIT_COMMIT}"`**, passed explicitly, `dev` when unset. The SDK's `get_default_release()` checks `SENTRY_RELEASE`, local git, `HEROKU_BUILD_COMMIT`, `SOURCE_VERSION`, `CIRCLE_SHA1` — **Render is absent**. | One argument |
| **D5** | PII posture | **Hard-wired off. No config row.** `send_default_pii=False`, `max_request_body_size="never"`, and an explicit `before_send` scrubber. A flag that invited someone to enable PII capture would be a footgun in an FSSAI/legal-records app. | Would require a new `Setting` row — deliberately expensive |
| **D6** | Prometheus client | **Official `prometheus-client`**, not `prometheus-flask-exporter`. The exporter only sees Flask and would miss `/api/v2/rag/*` (§1). | Swap the dependency and the `/metrics` handler |
| **D7** | Where `/metrics` lives | **A FastAPI `@app.get("/metrics")` in `asgi.py`, defined before the Flask catch-all mount** (`asgi.py:310`). One scrape surface covering both apps; outside Flask's login/RBAC gate. | Route relocation only |
| **D8** | `/metrics` authentication | **Bearer `METRICS_TOKEN`, header-only, fail closed.** Grafana Cloud states it *"won't accept a public URL that is not protected by authentication"*; a blank token with metrics enabled is a misconfiguration, not permission to serve. | None |
| **D9** | Tracing | **Deferred. `SENTRY_TRACES_SAMPLE_RATE=0.0`, errors-only.** This ships the high-value half (grouped issues from already-swallowed exceptions) without paying the a2wsgi double-transaction cost. | Raise one env var *after* §6 INV-11 is satisfied |
| **D10** | Snapshot gauges | **A `ScheduledJob` row in `JOBS`** (`app/services/scheduled_jobs.py:43`), matching the existing pattern. An in-process thread is the fallback when QStash is unconfigured (§14, Q2). | One tuple entry |
| **D11** | Multi-process mode | **Deferred.** `render.yaml:43` runs one uvicorn process, so in-memory metrics are correct today. Only `render_metrics()` (§5.4) keeps the door open — at no cost. | Set `PROMETHEUS_MULTIPROC_DIR` + switch to gunicorn (§14, Q1) |

### 2.1 Explicitly out of scope

Distributed tracing and OpenTelemetry (§14, Q3); alert routing to an on-call channel (§14, Q4);
Sentry session replay, profiling, and uptime monitors; shipping logs to a third party; `uvicorn
--workers > 1` / `PROMETHEUS_MULTIPROC_DIR` / `gunicorn -k uvicorn.workers.UvicornWorker`;
migrating the Jinja UI off Flask; replacing structlog; any change to `rag_query_log`'s schema or to
the QStash delivery mechanism.

---

## 3. Functional requirements

### 3.1 Configuration and gating

| ID | Requirement |
| --- | --- |
| **FR-1** | `SENTRY_DSN` **MUST** be declared in the `_TABLE` declaration seam (`app/shared/config.py:67`) as `Setting("SENTRY_DSN", "sentry_dsn", str, "")`, and **MUST** have a matching `.env.example` entry in the same commit — `tests/test_shared_config.py::test_env_example_keys_are_declared` (`:195`) fails the build otherwise. A blank value means Sentry is never initialised (D3). |
| **FR-2** | `SENTRY_ENVIRONMENT` **MUST** be declared as `Setting("SENTRY_ENVIRONMENT", "sentry_environment", str, "")`. When blank, the environment **MUST** resolve to `"production"` if `RENDER` is set, else `"development"` (§5.1). |
| **FR-3** | `SENTRY_TRACES_SAMPLE_RATE` **MUST** be declared as `Setting("SENTRY_TRACES_SAMPLE_RATE", "sentry_traces_sample_rate", float, 0.0)`. `0.0` is the shipped default (D9). The name is deliberate: this option governs **transactions only** and does **not** sample errors. |
| **FR-4** | `METRICS_ENABLED` **MUST** be declared as `Setting("METRICS_ENABLED", "metrics_enabled", bool, False)` — opt-in (the raw string must be `"true"`), per the convention documented at `app/shared/config.py:19-21`. `False` ⇒ `/metrics` is not registered (D7, INV-5). |
| **FR-5** | `METRICS_TOKEN` **MUST** be declared as `Setting("METRICS_TOKEN", "metrics_token", str, "")`. It is a secret: dashboard-set, never committed, never logged (SEC-5). |
| **FR-6** | `render.yaml` **MUST** declare `SENTRY_DSN` and `METRICS_TOKEN` with `sync: false` on **both** web services (`render.yaml:23`, `:144`), mirroring the `SUPABASE_DB_URL` precedent (`render.yaml:52`). Render injects **no** Sentry env vars for you and has no Sentry integration — unlike Datadog, QuotaGuard, and Formspree. |
| **FR-7** | `PROMETHEUS_MULTIPROC_DIR` and `PROMETHEUS_DISABLE_CREATED_SERIES` **MUST NOT** be table rows. Both are read by `prometheus_client` **at import time** (the multiprocess value-class is chosen before the first metric is constructed), so resolving them through `cfg` would be silently wrong. They belong in `render.yaml` `envVars` if ever set. |

### 3.2 Dependency and module layout

| ID | Requirement |
| --- | --- |
| **FR-8** | `"sentry-sdk[flask]==2.72.0"` **MUST** be added to `[project.dependencies]` in `pyproject.toml:37`, pinned exactly (D1). `requirements.txt` needs no edit — it only contains `-e .` and names `pyproject.toml` as the single source of truth (`requirements.txt:4-6`). |
| **FR-9** | `"prometheus-client==0.26.0"` **MUST** be added to `[project.dependencies]`, pinned exactly to the release the design was verified against (D6). |
| **FR-10** | A new package `app/observability/` **MUST** contain exactly `__init__.py`, `sentry.py`, and `metrics.py`. **Every metric object in the application MUST be defined in `metrics.py`** — one module, one namespace, mirroring the single-config-seam discipline of `app/shared/config.py`. No metric object **MAY** be constructed anywhere else. |
| **FR-11** | Both modules **MUST** be import-safe and **MUST NOT** require a Flask or ASGI application context, config values, or a database. Their import **MUST NOT** be able to fail a boot: `create_app()` **MUST** wrap the Sentry import in the same defensive posture as `setup_logging` (*"any failure is caught by the caller and must never block application boot"*, `app/utils/logging.py:12-15`). |
| **FR-12** | No root-level `*.py` file **MAY** be added — `tests/test_repo_root_layout.py` freezes the root inventory; helpers live in `app/observability/`, one-off scripts in `scripts/`. `docs/INDEX.md` and `CONTEXT.md` **MUST** gain entries for the new package and its two public functions (FR-13, FR-25). |

### 3.3 Sentry

| ID | Requirement |
| --- | --- |
| **FR-13** | `app/observability/sentry.py` **MUST** expose `init_sentry() -> None` and nothing else public. It **MUST** return immediately, **without calling `sentry_sdk.init`**, when the DSN is blank (D3). It **MUST** never pass `dsn=None` — an exported `SENTRY_DSN` would then be picked up by the SDK's own fallback. |
| **FR-14** | `init_sentry()` **MUST** wrap `sentry_sdk.init()` in `try/except Exception` and log a warning with `exc_info=True` on failure. A malformed DSN raises `BadDsn` out of `Transport.__init__`, unguarded — a typo **MUST NOT** be able to take a deploy down. |
| **FR-15** | The init call **MUST** pass `release=f"nsa-webservice@{os.environ.get('RENDER_GIT_COMMIT', 'dev')}"` (D4) and `environment=cfg.sentry_environment or ("production" if os.environ.get("RENDER") else "development")` (FR-2). |
| **FR-16** | The init call **MUST** pass `send_default_pii=False` explicitly. The SDK default is `None`, which is **not** the same thing (D5, SEC-1). |
| **FR-17** | The init call **MUST** pass `max_request_body_size="never"` (SEC-2). |
| **FR-18** | The init call **MUST** pass `before_send=<scrubber>`, and the scrubber **MUST** implement the denylist in SEC-3. It **MUST** return `None` for the ignored cases in FR-20. |
| **FR-19** | The default `LoggingIntegration` **MUST** be kept. It forwards `ERROR`-and-above stdlib records as events and `INFO`+ as breadcrumbs; because `app/utils/logging.py` routes every stdlib logger through `structlog.stdlib.ProcessorFormatter`, the swallowed failures of §1 become grouped, stack-traced issues **with no edits at the call sites**. `sentry_sdk.capture_exception()` **MUST NOT** be sprinkled into existing best-effort `except` blocks — the deliberate-degradation design is preserved and Sentry stays a pure consumer of existing signals. |
| **FR-20** | The init call **MUST** pass `ignore_errors=["werkzeug.exceptions.NotFound", "werkzeug.exceptions.Unauthorized"]`, and `before_send` **MUST** additionally drop `werkzeug.exceptions.*` and CSRF failures. This app generates 4xx on purpose; ASGI-side 4xx are already excluded (`failed_request_status_codes` defaults to `{500…599}`), but Flask-side 404/401 surface as `werkzeug.exceptions.*`. |
| **FR-21** | There **MUST NOT** be a `error_sample_rate` option — it does not exist. If error volume ever needs capping, it is `sample_rate` (static) or `error_sampler` (dynamic, overrides `sample_rate`). `SENTRY_TRACES_SAMPLE_RATE` **MUST NOT** be presented as an error control. `enable_backpressure_handling` **MUST** stay at its default (`True`) on the small Render instance. |
| **FR-22** | `traces_sample_rate` **MUST** come from `cfg.sentry_traces_sample_rate` (FR-3, default `0.0`). Tracing **MUST NOT** be enabled until **INV-11** (transaction de-duplication) is satisfied, because `FlaskIntegration` patches `Flask.__call__` while `a2wsgi` invokes exactly that entry point — so a single request can produce two server transactions (outer ASGI, inner WSGI). |
| **FR-23** | The init call **MUST** pass `integrations=[FlaskIntegration(http_methods_to_capture=("GET", "POST", "PUT", "PATCH", "DELETE"))]`. `FlaskIntegration` hooks Flask **signals**, not the app object, so init needs no app instance — but it **MUST** run as early as possible in `create_app()` (D2). |
| **FR-24** | `create_app()` **MUST** bind the request correlation context next to the existing `set_audit_user` hook (`app/__init__.py:378-386`): `sentry_sdk.set_tag("request_id", …)` (indexed/searchable) and `set_context("audit", {"actor_role": …})` (not indexed). Prefer `id`/`username` over email; hash anything that must stay linkable but unreadable. |
| **FR-25** | The scheduled handlers in `app/services/scheduled_jobs.py` **SHOULD** be wrapped with `sentry_sdk.monitor(...)` so a QStash job that stops firing raises a *missed* check-in — the pushed form of the same dead-man's-switch idea as `/health/backups`. |
| **FR-26** | Raw `RecordAudit` bodies, chain hashes, or licence numbers **MUST NOT** be placed in `set_tag`/`set_context`/`set_extra`. Sentry is an error sink; it **MUST NOT** become a second copy of the audit trail (AUD-1). |

### 3.4 Prometheus surface and `/metrics`

| ID | Requirement |
| --- | --- |
| **FR-27** | `app/observability/metrics.py` **MUST** own all metric definitions (FR-10), `render_metrics()` (§5.4), and the label-allowlist helpers used by §7. |
| **FR-28** | `GET /metrics` **MUST** be defined on the FastAPI app in `asgi.py` **before** `app.mount("/", WSGIMiddleware(...))` (`asgi.py:310`) — after the `/api/v2/*` routes. Defining it after the mount is unreachable. |
| **FR-29** | The endpoint **MUST** be a hand-written `@app.get("/metrics")` handler. `make_asgi_app()` + `app.add_route()` raises `TypeError` on `prometheus-client` 0.26.0; the documented workaround — `app.mount("/metrics", …)` — serves at `/metrics/` and **307-redirects** `/metrics`, and Grafana Cloud's metrics integration states *"redirects are not supported"*. A plain handler also gives one obvious place for the token check and behaves identically in single- and multi-process mode. |
| **FR-30** | The handler **MUST** compare the `Authorization` header against `Bearer <METRICS_TOKEN>` using `hmac.compare_digest` and **MUST** return `401` otherwise (SEC-5). If `METRICS_ENABLED` is true and `METRICS_TOKEN` is blank, it **MUST** return `503` and log an error — **never** `200` (D8, SEC-6). |
| **FR-31** | The route **MUST** be registered **only** when `cfg.metrics_enabled` is true. When false, `/metrics` falls through to the existing Flask surface and its normal access rules, and no Prometheus exposition text is ever emitted (INV-5). |
| **FR-32** | The handler **MUST** return `CONTENT_TYPE_LATEST` (Prometheus text format) and **MUST NOT** be behind Flask's login/RBAC gate — a scraper cannot log in, and the handler is outside that gate by construction (it is an ASGI route). |
| **FR-33** | The `/metrics` path **MUST NOT** be counted by its own metrics, and the scrape path **MUST NOT** touch the database, the LLM, or any network (INV-6, NFR-3). |

### 3.5 Write-time domain metrics

| ID | Requirement |
| --- | --- |
| **FR-34** | Flask-served paths **MUST** be instrumented from `before_request`/`after_request` hooks in `app/__init__.py`, labelled `surface="flask"`, recording the metrics of §5.2. |
| **FR-35** | FastAPI paths **MUST** be instrumented by a Starlette middleware registered in `asgi.py`, labelled `surface="fastapi"`. The `route` label **MUST** be read from the resolved route after the downstream call (`request.scope["route"].path`), never from the raw URL. |
| **FR-36** | The Flask hooks **MUST** skip every path FastAPI owns, and both surfaces **MUST** skip `/metrics`, `/health`, and `/api/v2/health`. `/api/v2` never reaches Flask, so the two surfaces are disjoint by construction; the `surface` label exists so a dashboard can split them (**INV-3**, §6). |
| **FR-37** | HTTP **route labels MUST use the route rule** — `/inspection/<int:inspection_id>`, never `/inspection/4821` (V-3). |
| **FR-38** | Metric calls in `RetrievalLogger` (`app/rag/retrieval/logger.py`) and `GenerationLogger` (`app/rag/generation/logger.py`) **MUST** be placed **outside** the `try` that swallows DB errors. A metric that fires only after a successful DB write would under-count exactly when the pipeline is unhealthy. |
| **FR-39** | The QStash webhook handler (`app/tasks_webhook/routes.py`) **MUST** record the task metrics of §5.2. |
| **FR-40** | `csp_report` (`app/health/routes.py:158`) **MUST** increment the CSP counter instead of only `logger.warning`-ing (`app/health/routes.py:191`). |

### 3.6 Snapshot gauges and the refresh job

| ID | Requirement |
| --- | --- |
| **FR-41** | A `ScheduledJob` **MUST** be added to `JOBS` (`app/services/scheduled_jobs.py:43`) with `name="refresh_observability_metrics"`, `flag_key="METRICS_ENABLED"`, `cron_key=None`, `default_cron="*/5 * * * *"`, `description="Refresh snapshot gauges (RAG quality, backup freshness, DB pool)."` (D10). |
| **FR-42** | The handler **MUST** live under `app/scripts/` (mirroring `app/scripts/cleanup_rag_logs.py`) and **MUST** be registered in `TASK_REGISTRY` (`app/utils/qstash_client.py:25`). The handler **MUST** be import-safe and idempotent, and **MUST NOT** raise into the webhook (the QStash delivery contract is best-effort, `app/services/scheduled_jobs.py:81-82`). |
| **FR-43** | The handler **MUST** set every gauge in §5.3, including `Gauge.set(0.0)` when the underlying query returns no rows — a vanished series **MUST** read as a drop to zero, not as a stale "last known good". |
| **FR-44** | Gauges **MUST** be refreshed on the schedule and read from memory at scrape time. A heavy aggregate **MUST NOT** run inside a scrape — Prometheus is pull-based and synchronous, and the scrape timeout is 10 s by default (`NFR-3`). |
| **FR-45** | `nsa_backup_last_success_timestamp_seconds` **MUST** be sourced from the same `last_backup_status()` that backs `/health/backups` (`app/health/routes.py:149-154`), so the alertable series and the health check cannot disagree. |
| **FR-46** | `nsa_build_info` **MUST** carry `version` and `git_sha` from `RENDER_GIT_COMMIT`. It is an `Info` metric and is therefore unavailable under multi-process mode (§14, Q1) — that is acceptable because multi-process is deferred (D11). |

### 3.7 Scraper, dashboards, alerts

| ID | Requirement |
| --- | --- |
| **FR-47** | The scraper **MUST** be the **Grafana Cloud "Metrics Endpoint" integration** pointing at `https://<service>/metrics` with the bearer token, at the integration default of **60 s**. No persistent Prometheus is run. Grafana Cloud bills on active series — hence NFR-4 and NFR-5. |
| **FR-48** | Two dashboards **MUST** exist: **Platform** (HTTP rate/latency/5xx, DB pool, process memory, backup freshness) and **RAG Quality** (queries by `query_type`, groundedness, hallucination rate, tokens, p95 LLM latency). |
| **FR-49** | The alerts in §8.2 (`AL-1`…`AL-8`) **SHOULD** be created. Each is tied to a documented failure mode in this repo, not to a generic best practice. |
| **FR-50** | Rollback **MUST** be documented and **MUST** be exactly: set `METRICS_ENABLED=false` / unset `SENTRY_DSN`. Phases 0–3 add no schema, no migration, and no change to any existing response. |

---

## 4. Non-functional requirements

| ID | Requirement |
| --- | --- |
| **NFR-1** | With `SENTRY_DSN` blank and `METRICS_ENABLED=false`, no test in the fast shard **MAY** reach the network by virtue of observability. There is no transport without a DSN (`make_transport()` ends in `if transport is None and options["dsn"]`), so skipping `init()` is a true zero-network switch — the property `tests/conftest.py`'s stub-LLM contract needs. |
| **NFR-2** | Neither integration **MAY** block application boot. Both are wrapped; failure degrades to "not configured" with a warning (`app/utils/logging.py:12-15` is the precedent). |
| **NFR-3** | The scrape **MUST** complete well inside the scraper's `scrape_timeout`. No DB query, LLM call, or network I/O on the read path; the read path is serialization only. |
| **NFR-4** | No label **MAY** carry an unbounded value. Target **< 10 values per label**; past ~100 is a design bug. Never label with `query`, `fso_name`, `case_id`, `inspection_id`, `license_no`, or `content_hash` (V-15). |
| **NFR-5** | Series growth **MUST** be bounded and **SHOULD** be reduced by setting `PROMETHEUS_DISABLE_CREATED_SERIES=true` if the Python client's per-metric `_created` series push the Grafana Cloud free tier. That env var is set in `render.yaml`, not through `cfg` (FR-7). |
| **NFR-6** | Latency **MUST** be recorded with **Histograms**, never `Summary`: the Python client's `Summary` does not compute quantiles locally, and precomputed quantiles cannot be aggregated across workers. Bucket layouts are fixed by §5.6; the default buckets top out at 10 s, which is **too low** for an LLM answer over a cross-encoder rerank plus Neo4j expansion. |
| **NFR-7** | No application module **MAY** `print` (`CODING_STANDARDS.md`, the `T201` exemption is `evaluation/*.py` only). |
| **NFR-8** | No root-level `*.py` file **MAY** be added (FR-12). |
| **NFR-9** | The two new dependencies **MUST** be the only runtime additions, both pinned, and both **MUST** be included in the `pip-audit` surface (`requirements.txt` ⇒ `-e .` ⇒ `pyproject.toml`, so no separate edit is needed — FR-8/FR-9). |
| **NFR-10** | Metric names **MUST** be `nsa_`-prefixed; `process_` and `scrape_` are reserved and **MUST NOT** be used (V-1…V-15, INV-9). |
| **NFR-11** | `app/observability/*` **MUST** be importable with no app context and no environment set — `tests/test_observability_metrics.py` imports it directly (§13). |

---

## 5. Data contracts

### 5.1 `init_sentry()` and the `sentry_sdk.init` kwargs

```python
# app/observability/sentry.py
def init_sentry() -> None:
    """Error tracking. Zero network when disabled; never blocks boot."""
```

| Kwarg | Value | Requirement |
| --- | --- | --- |
| `dsn` | `cfg.sentry_dsn` (blank ⇒ function returns before the call) | FR-1, FR-13 |
| `environment` | `cfg.sentry_environment` or `RENDER`-derived | FR-2, FR-15 |
| `release` | `nsa-webservice@<RENDER_GIT_COMMIT>` (`dev` fallback) | FR-15 |
| `send_default_pii` | `False` — **hard-wired** | FR-16, SEC-1 |
| `max_request_body_size` | `"never"` | FR-17, SEC-2 |
| `before_send` | module-level scrubber | FR-18, SEC-3 |
| `traces_sample_rate` | `cfg.sentry_traces_sample_rate` (default `0.0`) | FR-3, FR-22 |
| `integrations` | `[FlaskIntegration(http_methods_to_capture=(…))]` | FR-23 |
| `ignore_errors` | `["werkzeug.exceptions.NotFound", "werkzeug.exceptions.Unauthorized"]` | FR-20 |
| `enable_backpressure_handling` | default (`True`) — not overridden | FR-21 |

**Not a kwarg:** `error_sample_rate` (does not exist — FR-21).

### 5.2 Metric catalogue — events (counters and histograms, recorded at write time)

| ID | Metric | Type | Labels (← allowlist) |
| --- | --- | --- | --- |
| **MET-E1** | `nsa_http_requests_total` | Counter | `surface`(V-1), `method`(V-2), `route`(V-3), `status`(V-4) |
| **MET-E2** | `nsa_http_request_duration_seconds` | Histogram | `surface`, `method`, `route` |
| **MET-E3** | `nsa_http_requests_in_progress` | Gauge (`livesum`) | `surface`, `route` |
| **MET-E4** | `nsa_rag_queries_total` | Counter | `query_type`(V-5), `pipeline`(V-6), `outcome`(V-7) |
| **MET-E5** | `nsa_rag_retrieval_duration_seconds` | Histogram | `stage`(V-8) |
| **MET-E6** | `nsa_rag_pipeline_duration_seconds` | Histogram | `pipeline`(V-6) |
| **MET-E7** | `nsa_llm_generation_duration_seconds` | Histogram | `model`(V-9) |
| **MET-E8** | `nsa_llm_generations_total` | Counter | `model`, `mode`(V-10) |
| **MET-E9** | `nsa_llm_tokens_total` | Counter | `model`, `direction`(V-11) |
| **MET-E10** | `nsa_rag_hallucinations_total` | Counter | `query_type` |
| **MET-E11** | `nsa_rag_degraded_total` | Counter | `reason`(V-12) |
| **MET-E12** | `nsa_qstash_tasks_total` | Counter | `task`(V-13), `outcome`(V-7) |
| **MET-E13** | `nsa_qstash_task_duration_seconds` | Histogram | `task`(V-13) |
| **MET-E14** | `nsa_csp_violations_total` | Counter | `directive`(V-14) |

### 5.3 Metric catalogue — snapshots (gauges, refreshed on the schedule of FR-41)

| ID | Metric | Gauge mode | Source |
| --- | --- | --- | --- |
| **MET-S1** | `nsa_rag_groundedness_avg` | `mostrecent` | `avg(rag_query_log.groundedness_score)` over 24 h |
| **MET-S2** | `nsa_rag_hallucination_rate` | `mostrecent` | share of `hallucination_detected` over 24 h |
| **MET-S3** | `nsa_rag_queries_24h` | — | `count(rag_query_log)` over 24 h |
| **MET-S4** | `nsa_backup_last_success_timestamp_seconds` | `mostrecent` | `last_backup_status()` (FR-45) |
| **MET-S5** | `nsa_db_pool_checkedout` | — | `db.engine.pool.checkedout()` |
| **MET-S6** | `nsa_build_info` | Info | `version`, `git_sha` (FR-46) |

Under `mostrecent`, `inc()`/`dec()` raise — snapshots **MUST** use `set()` (INV-7).

### 5.4 `render_metrics()`

```python
def render_metrics() -> bytes:
    """Single-process: the default registry. Multi-process: a fresh registry
    holding ONLY the MultiProcessCollector (app metrics write their own files)."""
```

The function **MUST** branch on `os.environ.get("PROMETHEUS_MULTIPROC_DIR")`: when unset, render the
default `REGISTRY`; when set, build a **fresh** `CollectorRegistry` containing **only**
`multiprocess.MultiProcessCollector` and render that. A fresh registry is required — reusing the
default registry exports every metric twice in multi-process mode. This branch is the *only* thing
D11 costs today.

### 5.5 Config rows (declaration table)

```python
Setting("SENTRY_DSN", "sentry_dsn", str, "",
        help="Sentry DSN. Blank means Sentry is never initialised (no transport, no network).")
Setting("SENTRY_ENVIRONMENT", "sentry_environment", str, "",
        help="Sentry environment. Blank derives 'production' on Render, else 'development'.")
Setting("SENTRY_TRACES_SAMPLE_RATE", "sentry_traces_sample_rate", float, 0.0,
        help="Transaction sample rate. 0.0 = errors only (default); does NOT sample errors.")
Setting("METRICS_ENABLED", "metrics_enabled", bool, False,
        help="Expose GET /metrics for Prometheus scraping (bearer-token guarded).")
Setting("METRICS_TOKEN", "metrics_token", str, "",
        help="Bearer token required by GET /metrics when metrics are enabled.")
```

### 5.6 Histogram bucket layouts

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

The default buckets top out at 10 s — too low for an LLM answer over a cross-encoder rerank plus a
Neo4j expansion (NFR-6). Each bucket is a live series, so `model` **MUST** stay a small bounded set.

---

## 6. Invariants

Normative. Every one **MUST** hold at all times, not merely after a call.

| ID | Invariant | Origin |
| --- | --- | --- |
| **INV-1** | Observability is **not** the audit trail. `RecordAudit` (`app/services/audit.py`) remains the hash-chained record of truth; no legal record **MAY** depend on Sentry or Prometheus availability. | §1, AUD-1 |
| **INV-2** | For every event or transaction transmitted, **no** request body, cookie, `Authorization` header, FSSAI licence/registration number, person name, address, or query text is present. | FR-16…FR-18 |
| **INV-3** | The series produced by HTTP metrics is bounded by `\|routes\| × \|methods\| × \|statuses\|` — finite and **independent of traffic volume**. `/api/v2` is counted exactly once (never by both surfaces). | FR-34…FR-37 |
| **INV-4** | With `SENTRY_DSN` blank, no bytes are sent to Sentry. With `METRICS_ENABLED=false`, no `/metrics` exposition exists. | NFR-1, FR-31 |
| **INV-5** | With both integrations unset, the application's observable HTTP behaviour is byte-for-byte unchanged — same routes, same statuses, same bodies. | FR-31, FR-50 |
| **INV-6** | A scrape performs no I/O beyond serializing in-memory state; its cost is O(registered series). | FR-33, NFR-3 |
| **INV-7** | Snapshot gauges are **set**, never `inc()`/`dec()`. | §5.3 |
| **INV-8** | Counters never decrease; gauges may. Counters end in `_total`. | NFR-10 |
| **INV-9** | Every application metric name is `nsa_`-prefixed; `process_`/`python_` names come only from the client library. | NFR-10 |
| **INV-10** | `/metrics` **never** returns exposition text without a valid token. | FR-30 |
| **INV-11** | Tracing **MUST NOT** be enabled while a single request can produce two server transactions (the `a2wsgi` ASGI→WSGI pair). De-duplication is a precondition, not a follow-up. | FR-22, D9 |

---

## 7. Validation rules (label value allowlists)

Applied by `metrics.py` before any label is recorded. A value that fails its allowlist **MUST** be
replaced by the literal `"other"` (or the call **MUST** be dropped) — never recorded raw (NFR-4).

| ID | Label | Allowed values |
| --- | --- | --- |
| **V-1** | `surface` | `flask`, `fastapi` |
| **V-2** | `method` | `GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS` |
| **V-3** | `route` | A **registered route rule**; `"unmatched"` when routing failed. An interpolated path (an id, a UUID, a slug) **MUST NOT** be recorded |
| **V-4** | `status` | A valid HTTP status integer |
| **V-5** | `query_type` | `section_lookup`, `case_law`, `provision_search`, `general_qa`, `amendment_query` (`app/models/rag.py:44-46`) |
| **V-6** | `pipeline` | `legacy`, `agent` (`app/models/rag.py:62`) |
| **V-7** | `outcome` | `ok`, `error`, `abstained`, `degraded` |
| **V-8** | `stage` | `dense`, `sparse`, `rerank`, `kg`, `sql` |
| **V-9** | `model` | The set of configured model identifiers; **never** a user-supplied string |
| **V-10** | `mode` | `live`, `stub` |
| **V-11** | `direction` | `prompt`, `completion` |
| **V-12** | `reason` | `stub_llm`, `circuit_open`, `no_qdrant`, `no_kg`, `no_llm` |
| **V-13** | `task` | The `JOBS` names ∪ `TASK_REGISTRY` keys (`app/services/scheduled_jobs.py:43`, `app/utils/qstash_client.py:25`) |
| **V-14** | `directive` | The CSP directive set configured by Talisman in `create_app()` |
| **V-15** | *(forbidden)* | **`query`, `fso_name`, `case_id`, `inspection_id`, `license_no`, `content_hash`, any request header, any path segment.** The naming guidance is explicit: *"Do not use labels to store dimensions with high cardinality."* |

---

## 8. Route contracts

| Route | Method | Added by | Writes? | Gating |
| --- | --- | --- | --- | --- |
| `/metrics` | GET | `asgi.py` (FastAPI), before the mount | no | `METRICS_ENABLED` + `METRICS_TOKEN` |
| *(unchanged)* | — | `/api/v2/*`, Flask `/` | — | **MUST NOT** change |

No other route may be added by this work (§2.1). `/metrics` **MUST** carry no trailing-slash variant
and **MUST NOT** redirect (FR-29).

### 8.1 `/metrics` decision table

| `METRICS_ENABLED` | `METRICS_TOKEN` | Request | Response |
| --- | --- | --- | --- |
| false | — | any `/metrics` | Route not registered: the request falls through to the Flask surface and its normal access rules (no exposition text — INV-5) |
| true | blank | any | **503** + an `ERROR` log (`metrics_misconfigured`) — fail closed (FR-30) |
| true | set | no/incorrect `Authorization` | **401** (FR-30) |
| true | set | `Authorization: Bearer <token>` | **200**, `CONTENT_TYPE_LATEST` (FR-32) |

### 8.2 Alert set (Grafana Cloud)

| ID | Alert | Expression (sketch) | Why it matters here |
| --- | --- | --- | --- |
| **AL-1** | Endpoint down | `up == 0` | also covers Render killing the service |
| **AL-2** | 5xx rate | `sum(rate(nsa_http_requests_total{status=~"5.."}[5m])) / sum(rate(nsa_http_requests_total[5m])) > 0.02` | baseline error budget |
| **AL-3** | **Stub LLM in production** | `increase(nsa_llm_generations_total{mode="stub"}[15m]) > 0` | `/api/rag/health` exists precisely because *"stub answers look plausible but are canned"* (`app/rag/routes.py:120-122`); this is the automated form of that audit |
| **AL-4** | RAG degrading | `increase(nsa_rag_degraded_total[15m]) > 0` | circuit-open / no-Qdrant / no-Neo4j fallbacks are silent by design |
| **AL-5** | Groundedness drop | `nsa_rag_groundedness_avg < 0.6` | answers still return; quality falls silently |
| **AL-6** | Hallucination spike | `nsa_rag_hallucination_rate > 0.2` | legal-answer correctness risk |
| **AL-7** | Backup stale | `time() - nsa_backup_last_success_timestamp_seconds > 26*3600` | codifies the `/health/backups` rule as an alert (FR-45) |
| **AL-8** | Memory near cap | `process_resident_memory_bytes > 4.8e8` | the 512 MB Render free tier is a documented constraint (`RAG_EMBED_REMOTE_FALLBACK` exists to avoid OOM) |

An LLM-latency alert is intentionally **not** in the day-one set: it is derivable from `MET-E7`
(`histogram_quantile(0.95, …) > 30`) but needs a settled `model` allowlist (V-9, §14 Q5).

---

## 9. Security requirements

| ID | Requirement |
| --- | --- |
| **SEC-1** | `send_default_pii=False` **MUST** be hard-wired. **No** config row, env var, or request parameter **MAY** re-enable PII capture (D5). |
| **SEC-2** | `max_request_body_size="never"` **MUST** be set. Audit and legal payloads **MUST NOT** be captured. |
| **SEC-3** | The `before_send` scrubber **MUST** drop `request.data` and cookies, and **MUST** filter or hash anything identifying: `fso_name`, licence/registration numbers, addresses, query text. `EventScrubber(recursive=True)` **MUST** be used if the built-in denylist is insufficient — the built-in denylist is **not recursive by default**. |
| **SEC-4** | Sending raw `RecordAudit` bodies or chain hashes to Sentry is **forbidden** (FR-26). *"Scrub any data before it is sent"* is the documented rule; Sentry is an error sink, not a copy of the record of truth. |
| **SEC-5** | The `METRICS_TOKEN` comparison **MUST** use `hmac.compare_digest`. The token **MUST NOT** be logged, and **MUST NOT** be accepted from the query string — header only. |
| **SEC-6** | `METRICS_TOKEN` **MUST** be distinct from `API_V2_KEY` and `SECRET_KEY`. A blank token with metrics enabled is a **fail-closed** 503 (FR-30), never an open endpoint. |
| **SEC-7** | The Sentry DSN **MUST** be treated as a secret: set via the Render dashboard (`sync: false`, FR-6), never committed, never logged, never rendered into a response or template. |
| **SEC-8** | No route, template, or JSON API **MAY** echo raw metric values or the token to a browser user. `/metrics` is machine-facing only. |
| **SEC-9** | The audit trail **MUST NOT** gain a runtime dependency on Sentry or Prometheus (INV-1). |

---

## 10. Audit requirements

| ID | Requirement |
| --- | --- |
| **AUD-1** | Observability data **MUST NOT** be cited as evidence for any legal record, and **MUST NOT** be used to reconstruct one (INV-1). |
| **AUD-2** | Controlling cardinality or retention **MUST NOT** delete or mutate `RecordAudit` rows. |
| **AUD-3** | An ADR under `docs/adr/` **SHOULD** record the observability-vs-audit boundary and the PII hard-wiring (D5), so a future contributor does not "improve" either into a liability. |
| **AUD-4** | Log retention is governed by the existing `rag_query_log` cleanup (`app/scripts/cleanup_rag_logs.py`, 90-day default). The metrics pipeline **MUST NOT** introduce a second, divergent retention policy. |

---

## 11. Failure taxonomy

| Condition | Code / outcome | Behaviour |
| --- | --- | --- |
| `SENTRY_DSN` blank | `sentry_disabled` | No transport exists; `init_sentry()` returns before `init()`. Zero network (NFR-1). |
| `SENTRY_DSN` malformed | `sentry_init_failed` | `BadDsn` is caught; a warning with `exc_info=True` is logged; boot continues (FR-14). |
| Sentry unreachable | `sentry_dropped` | The SDK's backpressure handling drops events; the request path is unaffected (FR-21). |
| Sentry 429 / quota exhausted | `sentry_throttled` | Events are dropped server-side; the app is unaffected. There is no client-side rate limit beyond sampling. |
| `METRICS_ENABLED=true`, `METRICS_TOKEN` blank | `metrics_misconfigured` | **503** + `ERROR` log; never `200` (FR-30). |
| Token missing/incorrect | `metrics_unauthorized` | **401**; no partial exposition (INV-10). |
| `METRICS_ENABLED=false` | `metrics_disabled` | Route absent; the request reaches the Flask surface and its normal rules (FR-31). |
| Refresh job fails | `metrics_refresh_failed` | Handler swallows and logs (FR-42); gauges retain their last value. A **stale snapshot** is therefore possible and is the one residual gap — see §14 Q6. |
| QStash unconfigured | `metrics_refresh_inactive` | The schedule does not exist remotely; this is the existing behaviour for every `ScheduledJob` (`app/services/scheduled_jobs.py:105-113`) and is why §14 Q2 remains open. |
| Duplicate ASGI/WSGI transaction | *(not an error)* | Error capture is duplicated-but-harmless (Sentry groups on the exception) and **costs double** while tracing is on — the reason for INV-11. |

---

## 12. Acceptance criteria

Written as testable assertions. Each maps to a test in §13.

**Configuration**
- **AC-1** Given a fresh checkout, then `tests/test_shared_config.py::test_env_example_keys_are_declared`
  passes with the five new keys and no `.env.example` drift. *(FR-1…FR-6)*
- **AC-2** Given `METRICS_ENABLED=1` (not `"true"`), then `cfg.metrics_enabled is False` — the opt-in
  convention is preserved. *(FR-4)*
- **AC-3** Given `SENTRY_TRACES_SAMPLE_RATE=abc`, then the value falls back to `0.0` without raising.
  *(FR-3)*

**Sentry**
- **AC-4** Given a blank `SENTRY_DSN`, then `sentry_sdk.init` is **never** called. *(FR-13, NFR-1)*
- **AC-5** Given a blank `cfg.sentry_dsn` **and** `SENTRY_DSN` exported in `os.environ`, then `init`
  is still never called — proving the SDK's env fallback is not reachable. *(FR-13, D3)*
- **AC-6** Given a non-blank DSN, then `init` is called with `send_default_pii is False`,
  `max_request_body_size == "never"`, a `release` matching `nsa-webservice@…`, and
  `traces_sample_rate == 0.0`. *(FR-15…FR-17, FR-22)*
- **AC-7** Given a malformed DSN, then `create_app()` returns normally and one warning is logged.
  *(FR-14, NFR-2)*
- **AC-8** Given a hostile event fixture whose `request.data` contains a licence number and a person
  name, then `before_send` returns an event with no request body, no name, and no licence number.
  *(FR-18, SEC-2, SEC-3)*
- **AC-9** Given an `werkzeug.exceptions.NotFound` event, then `before_send` returns `None`.
  *(FR-20)*

**`/metrics`**
- **AC-10** Given `METRICS_ENABLED=false`, then a `GET /metrics` response contains no
  `nsa_`-prefixed exposition text. *(FR-31, INV-5)*
- **AC-11** Given metrics enabled with a token, then `GET /metrics` without `Authorization` returns
  **401**. *(FR-30)*
- **AC-12** Given metrics enabled with a token, then `GET /metrics` with `Authorization: Bearer
  <token>` returns **200** with `CONTENT_TYPE_LATEST`, and the body parses as Prometheus text.
  *(FR-32)*
- **AC-13** Given metrics enabled with a **blank** token, then `GET /metrics` returns **503**, never
  **200**. *(FR-30, SEC-6)*
- **AC-14** Given two scrapes in a row, then counts do not reset and no duplicate metric family
  appears. *(INV-8, FR-33)*

**Domain metrics**
- **AC-15** Given a request to `/inspection/4821`, then the `route` label is
  `/inspection/<int:inspection_id>` and no label value contains `4821`. *(FR-37, V-3)*
- **AC-16** Given one request to `/api/v2/rag/generate`, then `nsa_http_requests_total` increases by
  exactly one across **all** `surface` values. *(FR-34…FR-36, INV-3)*
- **AC-17** Given requests to `/metrics`, `/health`, and `/api/v2/health`, then no
  `nsa_http_requests_total` series is created for them. *(FR-36)*
- **AC-18** Given a `RetrievalLogger` whose DB write raises, then `nsa_rag_queries_total` still
  increments. *(FR-38)*
- **AC-19** Given a seeded `rag_query_log`, then the refresh handler sets `nsa_rag_queries_24h` to the
  row count and `nsa_rag_groundedness_avg` to the mean. *(FR-41…FR-43, MET-S1…MET-S3)*
- **AC-20** Given an empty `rag_query_log`, then the refresh handler sets the gauges to `0.0` rather
  than leaving a stale value. *(FR-43)*
- **AC-21** Given `PROMETHEUS_MULTIPROC_DIR` unset, then `render_metrics()` uses the default
  registry; given it set, then it uses a fresh registry whose output contains no duplicate family.
  *(§5.4)*
- **AC-22** Given both integrations unset, then `import app.observability.metrics` succeeds outside an
  app context and `GET /health` is unchanged. *(NFR-11, INV-5)*

**Discipline**
- **AC-23** Given the full fast shard, then no test performs network I/O attributable to
  observability. *(NFR-1)*
- **AC-24** Given `pytest tests/test_repo_root_layout.py`, then the root inventory is unchanged and no
  `print` exists in `app/`. *(NFR-7, NFR-8)*

---

## 13. Traceability

| Requirement | Test file | Phase |
| --- | --- | --- |
| FR-1…FR-7, AC-1…AC-3 | `tests/test_shared_config.py` (existing `:195`, `:206`) | 0 |
| FR-8, FR-9, NFR-9 | `tests/test_shared_config.py` *(new assertion on the pins)* | 0 |
| FR-10…FR-12, NFR-11 | `tests/test_repo_root_layout.py` (existing) + `tests/test_observability_metrics.py` *(new)* | 1 |
| FR-13…FR-26, SEC-1…SEC-4, SEC-7, INV-1, INV-2, INV-4, INV-11, AC-4…AC-9 | `tests/test_observability_sentry.py` *(new)* | 1 |
| FR-27…FR-33, FR-47…FR-50, SEC-5, SEC-6, SEC-8, INV-5, INV-6, INV-10, AC-10…AC-14, AC-21, AC-22 | `tests/test_observability_metrics.py` *(new)* | 2 |
| FR-34…FR-40, V-1…V-15, INV-3, INV-8, INV-9, AC-15…AC-18 | `tests/test_observability_metrics.py` *(new)* + `tests/test_metrics_routes.py` *(new)* | 2, 3 |
| FR-41…FR-46, AUD-4, AC-19, AC-20 | `tests/test_scheduled_jobs.py` (existing, extended) + `tests/test_observability_refresh.py` *(new)* | 3 |
| AL-1…AL-8 | **manual — Grafana Cloud UI. Not unit-tested.** | 4 |
| NFR-1, NFR-7, NFR-8, AC-23, AC-24 | `tests/conftest.py` (existing contract), `tests/test_repo_root_layout.py` (existing) | 0–3 |
| AUD-1…AUD-3 | **manual — ADR under `docs/adr/`.** | 4 |

**Fast-shard command** (what CI runs, `.github/workflows/validation.yml:209`):

```bash
pytest -m "not slow" --tb=short -q legal_paragraph_detection_engine/tests/ tests/
```

> The exact shard invocation is whatever `validation.yml` currently runs; this spec does not change
> it. The new test files are ordinary members of `tests/` and are picked up automatically.

---

## 14. Question status

### 14.1 Resolved — answered against primary sources, 2026-10-17

| # | Answer | Evidence |
| --- | --- | --- |
| **R1** | **The docs site is ahead of the installable SDK.** `sentry-sdk==2.72.0` is the stable PyPI line; `3.0.0a1…a7` are alphas while `docs.sentry.io` already documents the 3.x flow. Pin 2.72.0. | PyPI JSON metadata; §3.2 of the research |
| **R2** | **`dsn=None` is not an off switch.** `_get_options()` falls back to `os.environ["SENTRY_DSN"]`. Skip `init()` instead. | `sentry_sdk/client.py` |
| **R3** | **Render is not auto-detected as a release source and injects no Sentry env vars.** `get_default_release()` checks Heroku, Vercel, CircleCI, GitLab, GAE — not Render. Pass `release` explicitly. | `sentry_sdk/utils.py`; Render env-var and log-stream docs |
| **R4** | **There is no `error_sample_rate` option.** Errors are `sample_rate`/`error_sampler`; `traces_sample_rate` governs transactions only. | Sentry options reference |
| **R5** | **A malformed DSN raises out of `init()`** (`BadDsn` from `Transport.__init__`) and is unguarded. | `sentry_sdk/client.py` |
| **R6** | **`make_asgi_app()` + `add_route()` raises `TypeError` on `prometheus-client` 0.26.0**, and the `mount()` workaround 307-redirects — which Grafana Cloud's integration explicitly rejects. | client_python ASGI docs; Grafana Cloud integration docs |
| **R7** | **A Flask-only exporter would miss `/api/v2/rag/*`** — the latency-heavy routes are FastAPI's. | `asgi.py:37`, `:184`, `:310` |
| **R8** | **Multi-process mode is a future concern, not a current one:** `render.yaml:43` runs one uvicorn process. In multi-process mode, custom collectors, `set_function`, `Info`/`Enum`, and per-worker `process_*` metrics all break. | `render.yaml:43`; client_python multiprocess docs |
| **R9** | **Grafana Cloud requires authentication.** *"it is mandatory to provide authentication details, and Grafana Cloud won't accept a public URL that is not protected by authentication."* | Grafana Cloud metrics-endpoint integration docs |

### 14.2 Still open

| # | Question | Blocks | Owner |
| --- | --- | --- | --- |
| **Q1** | Keep single-process, or adopt `gunicorn -k uvicorn.workers.UvicornWorker --workers N` with `PROMETHEUS_MULTIPROC_DIR`? `uvicorn --workers` has no `child_exit` hook for `mark_process_dead`. | D11, FR-46 (`nsa_build_info`) | **Operator decision** |
| **Q2** | Refresh jobs: QStash `ScheduledJob` (needs credentials; silently inactive without them) or an in-process daemon thread (no infrastructure; correct while there is one worker)? | FR-41, §11 `metrics_refresh_inactive` | **Engineering decision** |
| **Q3** | Tracing now or later? Also: OpenTelemetry instead of Sentry tracing if LLM-latency *traces* become the goal — one API for traces + metrics + logs, and both Render and Grafana Cloud ingest OTLP. | INV-11, D9 | **Product decision** |
| **Q4** | Where do alerts land? No on-call channel is defined in the repo. Grafana Alerting can fan out to email/Slack with no new infrastructure. | AL-1…AL-8 | **Operator decision** |
| **Q5** | Source of the `model` allowlist (V-9): `RAG_LLM_MODEL`/`RAG_GENERATION_MODEL` plus a stub literal, or an explicit constant in `metrics.py`? | MET-E7…MET-E9, AL (LLM latency) | Engineering decision |
| **Q6** | How is a **stale** snapshot detected? If the refresh job stops, gauges hold their last value and AL-5/AL-6 stay quiet. Options: a `nsa_metrics_refreshed_timestamp_seconds` gauge, or rely on Sentry's cron monitor (FR-25). | §11 `metrics_refresh_failed`, FR-25 | Engineering decision |
| **Q7** | Should the freshness threshold be shared between `/health/backups` (26 h) and **AL-7**? Today AL-7 would restate the constant; a shared constant would make drift impossible. | AL-7, FR-45 | Engineering decision |

---

## 15. Change log

| Date | Change |
| --- | --- |
| 2026-10-17 | Initial specification, derived from `OBSERVABILITY_SENTRY_PROMETHEUS_RESEARCH.md`. Decisions D1–D11 fixed to the research's recommended defaults. Requirements FR-1…FR-50, NFR-1…NFR-11, INV-1…INV-11, V-1…V-15, SEC-1…SEC-9, AUD-1…AUD-4, MET-E1…MET-E14, MET-S1…MET-S6, AL-1…AL-8, AC-1…AC-24. |

---

## 16. Sources (primary only; the full list is research §8)

**Sentry**

- Options reference (`dsn`, `release`, `environment`, `sample_rate`, `error_sampler`, `ignore_errors`,
  `send_default_pii`, `event_scrubber`, `traces_sample_rate`, `max_request_body_size`,
  `enable_backpressure_handling`) — https://docs.sentry.io/platforms/python/configuration/options/
- Flask integration — https://docs.sentry.io/platforms/python/integrations/flask/
- Logging integration — https://docs.sentry.io/platforms/python/integrations/logging/
- Filtering (`before_send`, hints) — https://docs.sentry.io/platforms/python/configuration/filtering/
- Scrubbing sensitive data — https://docs.sentry.io/platforms/python/data-management/sensitive-data/
- Cron monitoring — https://docs.sentry.io/product/monitors-and-alerts/monitors/crons/
- Source: `sentry_sdk/client.py` (DSN fallback, `make_transport`) — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/client.py
- Source: `sentry_sdk/utils.py` (`Dsn`/`BadDsn`, `get_default_release`) — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/utils.py
- Source: `sentry_sdk/integrations/flask.py` — https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/integrations/flask.py
- PyPI metadata (version + `flask` extra) — https://pypi.org/pypi/sentry-sdk/json
- Render environment variables (`RENDER_GIT_COMMIT`) — https://render.com/docs/environment-variables
- Render log streams (Sentry **not** listed) — https://render.com/docs/log-streams

**Prometheus**

- Metric and label naming — https://prometheus.io/docs/practices/naming/
- Histograms and summaries — https://prometheus.io/docs/practices/histograms/
- Instrumentation practices — https://prometheus.io/docs/practices/instrumentation/
- Writing exporters (synchronous scrape, custom-collector rules) — https://prometheus.io/docs/instrumenting/writing_exporters/
- `client_python` — https://github.com/prometheus/client_python
- Multiprocess mode — https://prometheus.github.io/client_python/multiprocess/
- ASGI / FastAPI exporting — https://prometheus.github.io/client_python/exporting/http/asgi/
- Grafana Cloud metrics endpoint integration (mandatory auth, 60 s default) — https://grafana.com/docs/grafana-cloud/monitor-infrastructure/integrations/integration-reference/integration-metrics-endpoint/
