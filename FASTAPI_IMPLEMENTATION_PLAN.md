# FASTAPI IMPLEMENTATION PLAN — Readiness Evaluation & Change Blueprint

> **Status:** Planning / evaluation only — **no code changed** in this task.
> **Scope:** Assess readiness for introducing **FastAPI**, enumerate the changes required, and define **how** the migration will be implemented without breaking the live system.
> **Date:** 2026-08-11
> **Target repo:** `NSA_webservice` (Flask 2.x / SQLAlchemy 2.x / Jinja2 / Celery / Qdrant / Neo4j)

---

## 1. OBJECTIVE & SCOPE

Evaluate whether FastAPI should be introduced into the platform, how ready the codebase is, what must change, and the phased implementation path. Two distinct ambitions are assessed separately because they have very different cost/risk:

| Ambition | Verdict (this doc) |
| --- | --- |
| **A. Full rewrite of the whole app in FastAPI** | **NOT READY / NOT RECOMMENDED now** — the app is a server-rendered Jinja2 UI on the Flask/WSGI + Flask-ecosystem stack; FastAPI gives little to the HTML UI and the migration cost dwarfs the benefit. |
| **B. Scoped FastAPI API layer, coexisting with Flask (strangler pattern)** | **READY, with pre-conditions** — add FastAPI for the JSON API surface, keep Flask for the UI, unify under one ASGI server. Recommended path. |

This plan is written for **Ambition B** and includes the steps to reach **Ambition A** *if* the UI is later rebuilt as a decoupled SPA.

---

## 2. CURRENT-STATE BASELINE (evidence from the codebase)

| Area | Current implementation | FastAPI-relevant implication |
| --- | --- | --- |
| Web framework | Flask 2.x via `create_app()` in `app/__init__.py`; `App(Flask)` subclass | WSGI request/response model |
| UI | **Server-rendered Jinja2** (`return render_template(...)` in ~20 route files; `app/templates/base.html`) | No benefit from FastAPI; stays on Flask |
| Blueprints | 23 registered blueprints (`@bp.route`) | Native Flask concept — no FastAPI equivalent for UI routes |
| JSON API surface | 32 `return jsonify(...)` call sites (RAG, AI assistant, search, validation, health, knowledge_graph, webhook, etc.) | These are the natural FastAPI candidates |
| Auth | `flask_login.LoginManager` (session cookie, `login_required`) | No drop-in FastAPI equivalent → must re-implement/decouple |
| Security | `flask_talisman.Talisman` (CSP/HSTS) + `flask_wtf.csrf.CSRFProtect` | No FastAPI equivalent → need middleware |
| DB | `flask_sqlalchemy.SQLAlchemy` `db` (app-context-bound scoped sessions); models re-exported via `app/models/__init__.py`; Alembic migrations | Tightly coupled to Flask app context |
| Config | Env-driven, read inside `create_app()` via `os.environ` / `current_app.config`; pydantic already present (via qdrant-client) | Needs a shared config object |
| Async tasks | Celery (`make_celery(app)` wraps tasks in a Flask `ContextTask`) + QStash webhooks | Celery is async already; FastAPI only dispatches, does not replace |
| Serving / deploy | WSGI: `gunicorn --bind 0.0.0.0:10000 app:app` (`render.yaml`); migrations via `FLASK_APP=app:create_app flask db upgrade` | FastAPI needs an ASGI server (uvicorn/hypercorn) or a WSGI→ASGI bridge |
| Templates/Jinja | Jinja2 auto-render + Jinja2 bytecode cache; `app/static` assets | Unchanged; served by Flask |
| Tests | `pytest` + **pytest-flask** fixtures; 1,757 tests | Flask-specific fixtures; need parallel FastAPI `TestClient` tests (keep old suite green) |
| Service layer | Many modules use Flask globals (`current_app`, `request`, `url_for`, `db.session`) | Biggest refactor surface for reuse |

**Headline:** ~90% of the app is server-rendered HTML workflow UI; only a small, well-delineated slice is JSON API. The codebase is *not* shaped for a framework swap, but it *is* shaped for a **scoped API layer**.

---
## 3. READINESS ASSESSMENT

Scored per dimension (0–100) for the two ambitions.

### 3.1 Full migration (Ambition A)

| Dimension | Score | Rationale |
| --- | ---: | --- |
| Architecture fit | 25 | Flask globals (`current_app`, `request`, `url_for`, `db.session`) pervasive in services |
| Runtime/async benefit | 35 | Only a minority of endpoints are hot JSON; true concurrency value is limited |
| Ecosystem extension parity | 25 | No native Flask-Login/Talisman/CSRF/Migrate/Jinja2 parity in FastAPI |
| UI path (Jinja2) | 10 | Server-rendered UI gains nothing; would need a full SPA rewrite to justify |
| Deployment fit | 35 | WSGI gunicorn → must move to ASGI + proxy + health integration |
| Test migration | 30 | pytest-flask fixtures don't transfer; 1,757 tests touch Flask request contexts |
| Team/tooling alignment | 55 | Python + pytest + pydantic already present |
| **Overall (full rewrite)** | **~30 / 100** | **NOT READY / NOT RECOMMENDED now** |

### 3.2 Scoped FastAPI API layer, coexistence (Ambition B)

| Dimension | Score | Rationale |
| --- | ---: | --- |
| Architecture fit | 70 | JSON endpoints are already thin request/response + service calls |
| Runtime/async benefit | 60 | Pydantic validation, typed routes, async I/O where beneficial (RAG/Qdrant/Neo4j network calls) |
| Ecosystem parity needed | 45 | Only need auth + headers middleware for the API slice; UI keeps its stack |
| Deployment fit | 55 | One ASGI server can mount Flask via `WSGIMiddleware`; no UI change |
| Test strategy | 60 | Add FastAPI `TestClient` tests; existing suite stays green untouched |
| Incremental rollout | 70 | Strangler pattern: path-based routing, feature flags, instant rollback |
| **Overall (scoped layer)** | **~63 / 100** | **READY, with pre-conditions (§5)** |

**Pre-conditions before starting Ambition B:** (1) freeze an explicit endpoint classification, (2) add FastAPI/uvicorn to a split dependency set, (3) establish a shared config + DB session pattern that does not *require* a Flask app context, (4) decide the auth parity model (co-signed session cookie **or** service API keys). These are addressed in §5–§8.

---

## 4. STRATEGY DECISION

**Recommendation: Coexistence via ASGI composition (strangler / strangler-fig), NOT a fork.**

Keep the Flask app as the single source of truth for the UI and for Celery (its `ContextTask` needs a Flask app). Introduce a thin **FastAPI app mounted on the same ASGI server**, delegating only the JSON API surface. Path-based proxying (or a reverse proxy) routes between them. This gives: typed OpenAPI contracts, Pydantic validation, async network calls, and a safe incremental path — while the live workflow UI is untouched.

Two deployment shapes (choose one; recommended = **B1**):

- **B1 — Unified ASGI (recommended):** one ASGI process (`uvicorn`/`hypercorn`) hosts `FastAPI`, which **mounts the existing Flask app under `/`** via `starlette.middleware.wsgi.WSGIMiddleware`, and serves FastAPI routes under a reserved prefix such as `/api/v2`. Migrated endpoints are *removed* from Flask and *added* to FastAPI; unmigrated `/api/*` and all UI routes fall through to Flask. Single port, single health, one restart model.
- **B2 — Two services (reverse-proxy):** keep gunicorn/Flask on one service and a separate uvicorn/FastAPI service on another; an edge proxy (Render proxy / nginx) routes by path. Cleaner isolation but two services, two log streams, two restarts, and cross-service auth/session divergence.

Adopt **B1** first; revisit **B2** only if FastAPI must scale independently of the UI.
## 5. WHAT MUST CHANGE (change inventory)

Each row = a concrete change, why, and how. **None executed in this task.**

### 5.1 Dependencies & runtime

| Change | Why | How |
| --- | --- | --- |
| Add fastapi, uvicorn (+ starlette), pin pydantic v2 | Provide ASGI app + validation | Add to `[project.optional-dependencies]` as a new `api` extra (keep flask in base); nothing else in the base stack changes |
| Optional asyncpg / aiosqlite for async DB | Async DB I/O only where beneficial | Add to the `api` extra; do **not** make SQLAlchemy async app-wide |
| Keep flask_sqlalchemy, flask_login, flask_talisman, flask_wtf, flask_migrate | UI + Celery still depend on them | No removal |

### 5.2 Code structure (new files, additive)

| Change | Why | How |
| --- | --- | --- |
| New asgi.py at repo root | ASGI entry point composing Flask + FastAPI | Build FastAPI(); add security/error middleware; mount the existing Flask app under the root via Starlette WSGIMiddleware; register API routers under /api/v2 |
| New app/api/ package | FastAPI routers kept separate from Flask blueprints | Router-per-domain (rag, ai_assistant, search, health, kg, webhook), each importing only service functions |
| New shared config loader | One config source for both runtimes | Extract env-to-config mapping used by create_app() into a load_settings() module consumed by both Flask and FastAPI |

### 5.3 The critical refactor: decouple services from Flask globals

| Change | Why | How |
| --- | --- | --- |
| Refactor the JSON-endpoint service functions to take explicit inputs and return plain data (stop calling current_app, flask.request, url_for) | FastAPI endpoints cannot rely on Flask request/context globals | Identify the JSON-returning call sites; extract their logic into pure service functions; the Flask route becomes a thin adapter calling the same function; the FastAPI route calls it directly |
| Provide a bindable DB session (not only the app-context db session) | FastAPI runs outside a Flask app context | Add a standalone SQLAlchemy Session factory bound to DATABASE_URL for API use (same models, same engine); or, less preferred, push a Flask app context around sync FastAPI endpoints |
| Config via explicit object | FastAPI has no current_app | Pass settings into routers via a small dependency (Depends(get_settings)); do not read os.environ inside route bodies |
### 5.4 Auth & security parity

| Change | Why | How |
| --- | --- | --- |
| Choose an auth model for the API | FastAPI has no flask_login | Option 1 (browser + API): verify Flask's signed session cookie in FastAPI by re-loading the User from the session's user_id (parity with the UI). Option 2 (machine clients): issue static API keys / JWT for programmatic callers, stored in settings/app_secrets, never inline. A hybrid of both is typical |
| Add security headers + CORS on the FastAPI app | Talisman CSP is Flask-scoped | Add middleware mirroring the non-UI headers (HSTS, X-Content-Type-Options, frame options); enable CORS only for the allowed origin list |
| CSRF on state-changing API endpoints | Match UI posture where needed | For cookie-authenticated API calls, verify the CSRF token from the Flask session; for API-key/JWT calls, the bearer credential is the CSRF control (no cookie) |

### 5.5 Deployment & operations

| Change | Why | How |
| --- | --- | --- |
| Serve via an ASGI worker | FastAPI is ASGI | In B1, replace the gunicorn start command with uvicorn asgi:app, or keep gunicorn with the uvicorn worker class; ensure flask db upgrade still runs before start in render.yaml |
| Health checks | Two stacks, one probe | Keep GET /health public; add /api/v2/health; both report app identity + DB status; keep the existing probe path stable for uptime checks |
| Logging / error contract | Consistent observability | Unify structured logging; map FastAPI HTTPException and validation errors to the app existing error envelope so clients see one shape |
| OpenAPI docs | Typed contracts | FastAPI auto-generates /docs and /openapi.json, scoped to the API prefix only |

### 5.6 Testing

| Change | Why | How |
| --- | --- | --- |
| Add FastAPI test fixtures | New runtime needs coverage | Use fastapi.testclient (or httpx/ASGI transport) in new tests/test_api_*; do **not** modify the existing 1,757 Flask tests |
| Contract / parity tests | Ensure migrated endpoints behave identically | Dual-run each migrated endpoint (Flask old vs FastAPI new) against the same fixture DB and assert equal status/body before cut-over |

---
## 6. IMPLEMENTATION PLAN (how it gets done, phased)

Each phase is independently releasable and reversible. `[UI unaffected]` marks phases that do not touch the Jinja2 UI.

**Phase 0 — Inventory & classification (docs only).** Produce the endpoint inventory table (§9) and tag each JSON endpoint as `CORE-API`, `UI-ONLY`, or `WEBHOOK`. Agree the auth model and the `/api/v2` prefix. *Exit: reviewed inventory, no code.*

**Phase 1 — Coexistence bootstrap `[UI unaffected]`.** Add the `api` extra; create `asgi.py` that builds FastAPI, mounts the existing Flask app under the root via WSGIMiddleware, and adds a stub `/api/v2/health`. Serve with uvicorn in staging; run the full existing pytest suite against it to prove the Flask app behaves identically under the ASGI wrapper. *Exit: staging serves UI via ASGI with all 1,757 tests green; rollback = revert start command.*

**Phase 2 — Shared config + DB session layer `[UI unaffected]`.** Extract `load_settings()`; add the standalone Session factory; add FastAPI dependencies (`get_settings`, `get_db`). No routes moved yet. *Exit: both runtimes can resolve config + DB.*

**Phase 3 — Port the first low-risk endpoints.** Move pure-JSON, low-auth-risk endpoints first (e.g., `/api/v2/health`, `/api/v2/rag/query` read-only, `/api/v2/search`). Refactor their service logic to pure functions (shared by Flask and FastAPI). Add FastAPI tests + parity tests; keep the Flask routes returning the old responses until cut-over (feature-flag / proxy path). *Exit: parity tests pass; FastAPI response identical to Flask.*

**Phase 4 — Port the remaining CORE-API + AI endpoints.** Move RAG query/generate/eval, AI assistant, knowledge-graph API, validation, webhook *parsing* (dispatch stays on Celery). Implement the agreed auth + headers + error-envelope middleware. *Exit: all CORE-API endpoints servable from FastAPI; old Flask routes behind flag.*

**Phase 5 — Cut-over.** Flip the router/proxy so `/api/v2/*` hits FastAPI; keep the UI on Flask in the same ASGI process. Run load smoke + parity + the full suite. *Exit: live `/api/v2` served by FastAPI; UI unchanged; rollback is a one-line flag revert.*

**Phase 6 (optional, out of scope now) — Full FastAPI.** Only if the UI is rebuilt as a decoupled SPA: assess then whether Flask can be retired. Not recommended in the current roadmap (server-rendered UI stays on Flask).

---

## 7. RISKS & MITIGATIONS

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Dual-runtime divergence (endpoint behaves differently) | Medium | Shared pure service functions + parity tests (§5.6); FastAPI route delegates to the same function as Flask |
| Auth/session parity bug → security regression | High | Option-1 session verification is read-only + SSR-safe; add auth parity tests; ship headers/CSP middleware in Phase 1, not later |
| Flask-app-context coupling of db.session / current_app | High | Standalone Session factory (§5.3) + explicit settings; keep app-context only where Celery/UI needs it |
| Celery ContextTask is Flask-bound | Medium | Do not move Celery; FastAPI only dispatches tasks via the existing broker/webhook |
| Existing 1,757 Flask tests break | High | Phase 1 proves Flask under WSGIMiddleware stays green; add tests, never rewrite old ones |
| Async DB temptation causing data races | Medium | Keep SQLAlchemy sync; async only for network I/O (Qdrant/Neo4j via the existing clients) |
| Ops: ASGI worker vs gunicorn migration | Low-Med | B1 keeps one port/process; validate on staging; document rollback |
| OpenAPI exposes internals | Low | Scope /docs to /api/v2 only; no internal schemas leaked |

---
## 8. ROLLOUT & ROLLBACK

- **Rollout:** additive-only. Every new dependency and file is additive; the existing `FLASK_APP`/gunicorn path remains valid until Phase 5. Migration order is by risk (read-only first, state-changing last).
- **Rollback:** Phase 1–4 require only reverting the `asgi.py` start command and the feature-flag; the Flask routes are never deleted before FastAPI parity is proven. Phase 5 rollback = flip the path router back to Flask.
- **Release discipline:** each phase lands behind a flag; a failing parity test blocks the next phase.

---

## 9. APPENDIX A — ENDPOINT CLASSIFICATION (initial triage)

Readily portable **CORE-API** (JSON, low UI coupling): RAG pipeline (`/rag/query`, `/rag/generate`, `/rag/eval`, `/rag/health`), AI assistant (`/ai-assistant/assist`), search API, validation endpoints, knowledge-graph API, `/health`.

**WEBHOOK / async:** `tasks_webhook` endpoints (QStash signature validation + dispatch to Celery) — keep on Flask initially; port only the *parser*, keep Celery dispatch.

**UI-ONLY (stay on Flask):** all `render_template` routes — case file, adjudication, annexure, evidence, inspection, sample, billing, timeline, version control, settings, document viewer, etc.

> The exact split must be finalized in Phase 0 against the live route map; the lists above are the working triage.

---

## 10. APPENDIX B — RECOMMENDED END STATE (post-Phase 5)

```
public/internet
   │
   ▼
uvicorn asgi:app   (single ASGI process, single port)
   │
   ├─ FastAPI  /api/v2/*   → typed, Pydantic-validated JSON API
   │     (rag, ai_assistant, search, health, kg, webhook-parse)
   │     auth: session-cookie parity + API keys/JWT for machine clients
   ├─ FastAPI  /docs, /openapi.json   (API docs, scoped)
   └─ Flask    /  (mounted via Starlette WSGIMiddleware)
         UI (Jinja2) + Celery ContextTask + QStash  — unchanged
```

---

## 11. DECISIONS RECORDED / OUT OF SCOPE

- **Do NOT** rewrite the Jinja2 UI in React/SPA as part of this work.
- **Do NOT** make the whole stack async; sync SQLAlchemy stays.
- **Do NOT** replace Celery/QStash; FastAPI only dispatches.
- **Do NOT** delete Flask blueprints or the 1,757 Flask tests during migration.
- **Ambition A (full rewrite) is deferred** unless/until the UI is decoupled.

---

## 12. ONE-LINE SUMMARY

> The project is **not ready for a full FastAPI rewrite** (~30/100), but it **is ready for a scoped FastAPI API layer coexisting with Flask** (~63/100, with pre-conditions). Recommended path: mount FastAPI and the existing Flask app on one ASGI server (`asgi.py`), move only the JSON API surface to FastAPI behind `/api/v2` using the strangler pattern, decouple the JSON services from Flask globals (bindable DB session + explicit settings), give the API auth/header parity, and cut over endpoint-by-endpoint with parity tests and instant rollback. The Jinja2 UI and Celery stay on Flask.
