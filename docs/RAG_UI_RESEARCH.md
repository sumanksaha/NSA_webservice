# RAG UI Research — Query Reception → Processing → Generation → Display (Primary-Source Trace)

> **Date:** 2026-08-22 · **Method:** primary-source investigation only. Every claim below is
> cited to the owning source file (+ function/line where useful). Documentation files
> (`agents.md`, `task.md`, `plan.md`) are cited **only as context**, never as evidence.
>
> **Headline finding:** contrary to what "RAG is API-only" would suggest, a full interactive
> RAG query UI **exists and is wired**: a server-rendered page at `GET /api/rag/`
> (`app/rag/templates/rag/query.html`), a vanilla-JS controller
> (`app/static/js/rag_query.js`) that POSTs to `/api/rag/query/agent`, and a nav entry in
> `base.html`. The UI is JSON-API-driven (AJAX), not form-post driven, mirroring the
> `ai_assistant.js` pattern.

---

## Question

"The RAG UI — how will the app receive the query, how will it be processed, how will the
answer be generated and how will it be displayed, what is the current implementation state?"

---

## 0. End-to-end flow at a glance

```
Browser
  └─ GET /api/rag/                     → rag.query_ui renders rag/query.html   [IMPLEMENTED]
       └─ user types question, clicks "Ask"
            └─ JS fetch POST /api/rag/query/agent  {query, top_k, collection_name?}
                 │                          (app/static/js/rag_query.js::submitQuery)
                 ▼
Flask blueprint "rag" (url_prefix="/api/rag", app/rag/__init__.py:15-21)
  ├─ RAG_USE_AGENT_PIPELINE=false (default) → delegates to legacy query()      [DEFAULT PATH]
  │    └─ run_generation_pipeline (app/rag/tasks.py:421)
  │         ├─ run_retrieval_pipeline (tasks.py:140+)  classify → parse → hybrid retrieve → rerank → log
  │         ├─ optional KG contract fusion (RAG_KG_FUSION) / KG expansion (RAG_KG_EXPANSION)  [FLAG-GATED]
  │         └─ GroundedGenerationService.generate (generation/grounded_service.py:74)
  │              context build → prompt → LLM call → citations → sanitize → log → RAGResponse
  └─ RAG_USE_AGENT_PIPELINE=true → LangGraph agent graph                       [FLAG-GATED]
       classify → retrieve → generate → verify ─(groundedness<0.7, retries<2)→ expand_query ↺
       (M5 HITL: review interrupt before finalize when RAG_AGENT_HITL=true)

FastAPI gateway (asgi.py): /api/v2/rag/generate|retrieve|query/agent|…agent/resume
  → same services behind ResilientRAGPipeline circuit breaker (asgi.py:178-193)

Browser renders RAGResponse JSON: answer card + groundedness/confidence gauges +
hallucination banner + citation list + retrieved chunks + agent audit block
(app/static/js/rag_query.js::renderResponse, lines 76-186)
```

---

## 1. How a query is received

### 1.1 The HTML entry point (the actual "UI route")

- **Route:** `GET /api/rag/` → `query_ui()` in `app/rag/routes.py:40-67`. It renders
  `"rag/query.html"` and passes the domain list from `app/rag/collections.py::DOMAIN_COLLECTIONS`
  so the template can build its dropdown server-side (routes.py:57-67).
- **Fail-closed gating:** returns 404 when `RAG_ENABLED=false` (routes.py:52-55); the flag is
  resolved through the config seam as `cfg.rag_enabled` (routes.py:29-31;
  declaration in `app/shared/config.py:68-75`, default **True**, opt-out convention).
- **Auth:** the blueprint has no public exemption other than `/health`; the global
  `require_login` gate applies, i.e. the page requires login (module docstring routes.py:8;
  asserted in `tests/test_rag_interface.py` header lines 4-6: "GET /api/rag/ redirects
  unauthenticated users to login").
- **Nav wiring:** `app/templates/base.html:255-262` renders a "Legal RAG" nav link to
  `url_for('rag.query_ui')`, conditionally on `config.get('RAG_ENABLED', True)`.

### 1.2 The browser-side submission (JS)

`app/static/js/rag_query.js` (IIFE, explicitly modeled on `app/static/js/ai_assistant.js`,
header comment lines 9):

- `getPayload()` (lines ~207-217) reads `#ragQuery`, `#ragDomain`, `#ragTopK` and builds
  `{query, top_k}`; selecting a domain maps it to `collection_name = domain + "_legal_768"`
  (lines 213-215).
- `validateQuery()` (lines 219-223): non-empty, max 2000 chars (matches the template's
  `maxlength="2000"` textarea attribute, `query.html:70-76`).
- `submitQuery()` (lines 225-290) does `fetch("/api/rag/query/agent", {method:"POST",
  headers:{Content-Type: application/json}, body})` (lines 247-251). CSRF is attached
  automatically by the base-template fetch interceptor (file header line 8). Enter-key
  submits (init, lines 365-372).
- Note the JS always posts to the **agent** endpoint; whether the LangGraph graph actually
  runs is decided server-side by `RAG_USE_AGENT_PIPELINE` (see §2.3).

### 1.3 Server-side endpoints accepting a query

All under the `rag` blueprint, prefix `/api/rag` (`app/rag/__init__.py:15-21`; routes imported
at module bottom so decorators register, `__init__.py:23-31`; blueprint registered in
`create_app()`):

| Endpoint | Handler | Body schema / validation | Source |
|---|---|---|---|
| `POST /api/rag/query` | `query()` | `{query:str required non-empty, top_k:int≥1 default 10, collection_name?, filters?}`; manual dict checks → 400; 503 when disabled | `app/rag/routes.py:210-250` |
| `POST /api/rag/query/agent` | `agent_routes.query_agent()` | same schema (+ `thread_id` only when HITL on, must be non-empty string → 400); flag-off **delegates verbatim** to legacy `query()` | `app/rag/agent/routes.py:49-120` |
| `POST /api/rag/query/agent/resume` | `query_agent_resume()` | `{thread_id:str required, approved:bool default true}`; 400 when HITL flag off | `app/rag/agent/routes.py:123-174` |
| `POST /api/rag/generate` | `generate()` | `{query, chunks?, query_type?, top_k?, collection_name?, filters?}` — generation without retrieval when chunks supplied | `app/rag/routes.py:162-207` |
| `POST /api/rag/eval` | `eval_batch()` | `{dataset:[{query, expected_answer, expected_citations}], eval_run_id?, top_k?}` | `app/rag/routes.py:253-293` |
| `GET /api/rag/health` | `health()` | none; public probe | `app/rag/routes.py:34-37` |
| `POST /api/rag/ingest`, `/ingest/corpus` | ingestion API (not query-path) | `{text}\|{source}`, optional `document`, `full_enrichment` | `app/rag/routes.py:70+` (docstring lines 1-14) |

Validation style: all Flask query routes use `request.get_json(silent=True)` + explicit
type checks returning 400 (e.g. routes.py:225-235; agent/routes.py:64-84) — there is no
Pydantic/Marshmallow layer on the Flask side.

### 1.4 FastAPI gateway (`/api/v2/*`) — programmatic reception

`asgi.py` builds the Flask app and mounts it at `/` via `a2wsgi.WSGIMiddleware`
(asgi.py:37-41, 336), then owns `/api/v2/*` natively:

- Pydantic request models: `GenerateRequest{query min_length=1, top_k ge=1 le=50,
  collection_name?, filters?}` (asgi.py:48-54) and `QueryAgentRequest` (+ `thread_id`)
  (asgi.py:57-64), `AgentResumeRequest` (asgi.py:67-75).
- Routes: `POST /api/v2/rag/generate` → `get_rag_pipeline().run(...)` — i.e. the
  **ResilientRAGPipeline** (asgi.py:178-193); `POST /api/v2/rag/retrieve` (asgi.py:196+);
  `POST /api/v2/rag/query/agent` with legacy-delegation when the flag is off and 202
  `awaiting_review` on HITL interrupt (asgi.py:~230-292); `.../resume` (asgi.py:295-330).
- Middleware: `SecurityHeadersMiddleware` for `/api/v2/*` (asgi.py:120-131) and
  `ApiKeyAuthMiddleware` requiring `x-api-key` when `API_V2_KEY` is set (asgi.py:139-159).
- The browser UI does **not** use `/api/v2/*` — it talks to the Flask `/api/rag/*` routes
  (rag_query.js:247). The v2 surface is API-consumer facing only (OpenAPI at `/api/v2/docs`
  per `FASTAPI_IMPLEMENTATION_PLAN.md` — context, not evidence).

## 2. How the query is processed

### 2.1 Legacy pipeline — `run_generation_pipeline` (default path)

Entry: `app/rag/tasks.py:421-590` `run_generation_pipeline(query, chunks=None, ...)`.

**Stage A — retrieval:** `run_retrieval_pipeline` (tasks.py:140-312):
1. `QueryClassifier().classify()` + `QueryParser().parse()`, parsed filters merged with
   caller filters (tasks.py:151-157).
2. Legal query-type classification for rerank weighting — flag `cfg.legal_query_typing`
   (tasks.py:159-168).
3. Identifier route — lexical "{Act} section N" parallel retrieval arm, flag
   `cfg.identifier_route` (tasks.py:170-183).
4. Hybrid retrieval built by the composition root `build_hybrid_retriever(collection_name)`
   (`app/rag/retrieval/factory.py`): collection-aware dense + Qdrant-BM25 sparse + ensemble/
   plain reranker fused (tasks.py:185-233). Optional TTL/LRU retrieval cache behind
   `cfg.retrieval_cache` (declaration `config.py:92-98`; used tasks.py:194-235).
5. Hash-chained audit log on every call via `RetrievalLogger` (tasks.py:237-245).
6. Feature-flagged post-layers (all degrade gracefully): legal identity parsing, cross-ref
   candidate expansion, evidence-set selection (tasks.py:255-296).

**Stage B — optional Knowledge-Graph enrichment (both flag-gated, default off):**
- `RAG_KG_FUSION`: query→provisions contract RRF-fused into ranked context
  (tasks.py:468-509; default-false per `.env.example` / `agents.md` §6 — context only).
- `RAG_KG_EXPANSION`: chunk-ID expansion through the Neo4j legal KG via
  `kg/hybrid.py::KGContextExpander`, provisions injected as extra `[Source n]` blocks,
  best-effort/never raises (tasks.py:511-555). Mutually exclusive with fusion
  (fusion-wins guard, tasks.py:519-524).

**Stage C — generation + verification:** see §3.

Return shape: flat dict mirroring `RAGResponse` plus `kg_expansion`, `kg_contract`, and
`pipeline: "legacy"|"agent"` stamping for A/B (tasks.py:569-590).

### 2.2 Resilient wrapper (circuit breaker) — where it applies

`app/rag/resilient.py:43-133` `ResilientRAGPipeline`: closed→open (3 consecutive failures)→
half-open (30 s cooldown probe) state machine around `run_generation_pipeline`, falling back
to a stub generator so users get a degraded answer instead of an error (resilient.py:1-16,
80-103). **Important nuance:** the Flask route `/api/rag/query` calls
`run_generation_pipeline` *directly* (routes.py:237-245) — the circuit breaker wraps only the
FastAPI-native `/api/v2/rag/generate` path (`asgi.py:178-193` via `get_rag_pipeline()` in
`app/api/deps.py`).

### 2.3 Agent pipeline (LangGraph) — implemented but dormant by default

- Flag resolution through the config seam: `cfg.use_agent_pipeline`
  (`app/shared/config.py:76-82`, default **False**) and `cfg.agent_hitl`
  (`config.py:83`, default False), consumed in `app/rag/agent/routes.py:33-46`.
- Graph: `classify → retrieve → generate → verify → finalize`, conditional
  expand-and-retry while `groundedness < GROUNDEDNESS_THRESHOLD` and
  `retry_count < max_retries (2)` (`route_after_verify`, `app/rag/agent/graph.py:42-53`;
  ASCII diagram graph.py:6-8).
- M5 HITL: `review_node` inserts a `langgraph.types.interrupt` between verify and finalize;
  approved→finalize, rejected→expand_query (`graph.py:56-80`). Checkpointer selection
  `memory` (MemorySaver singleton) or `postgres` (`graph.py` module docstring lines 17-21;
  `config.py:84-90`).
- `langgraph` is imported lazily inside the graph module; the app boots without it and a
  missing install surfaces as HTTP 503 (`agent/routes.py:97-100`; lazy-import note
  `app/rag/__init__.py:27-30`; ImportError re-raise `graph.py:162-165`).
- Route behavior with flag off: byte-identical delegation to legacy `query()`
  (`agent/routes.py:76-80`) — so the UI works unchanged today, just without self-correction.
- Nodes reuse the existing services rather than reimplementing them (thin wrappers:
  `app/rag/agent/nodes.py`, e.g. `generate_node` at nodes.py:133; `verify_node` docstring
  "Assess the generated response's groundedness" at nodes.py:172-174; `expand_query_node`
  reuses `GroundedLLMClient` per `tests/test_rag_agent_nodes.py:3-6`).

## 3. How the answer is generated

### 3.1 Orchestration — `GroundedGenerationService.generate`

`app/rag/generation/grounded_service.py:74-143`, seven explicit steps:
1. early-exit `RAGResponse` when no chunks (no LLM call) — grounded_service.py:96-117;
2. `ContextBuilder.build()` (step 1, line 120);
3. `PromptTemplate.render_default()` — template registry for grounded-QA prompts
   (`prompt_template.py` module docstring line 2);
4. `llm_client.call(system, user)` (line 126);
5. `CitationTracker` extraction mapping `[n]` markers onto chunk IDs via the BuiltContext
   citation map (`_extract_citations`, grounded_service.py:181-200);
6. `ResponseSanitizer.sanitize()` (line 132) — see §3.3;
7. `GenerationLogger.log_generation()` best-effort + TokenCounter real context length
   (grounded_service.py:134-138, 256-260) populating `RAGQueryLog` metrics.

Final assembly into `RAGResponse` (answer, valid citations, groundedness, hallucination
flag/claims, confidence, latencies, token usage, debug block):
grounded_service.py:245-289.

### 3.2 LLM client — OpenRouter with deterministic stub fallback

`app/rag/generation/llm_client.py`:
- Provider: OpenAI-compatible HTTP via `httpx`; default base URL
  `https://openrouter.ai/api/v1` (llm_client.py:77); default model hardcoded
  `poolside/laguna-s-2.1:free` (llm_client.py:73-74), overridable via `RAG_LLM_MODEL`
  (llm_client.py:86).
- API key: `OPENROUTER_API_KEY` then `OPENAI_API_KEY` (llm_client.py:87).
- **Stub mode:** active whenever no API key is present or `RAG_USE_STUB_LLM=true`
  (llm_client.py:89-93) — logs "responses are not realistic". Out of the box the deployed
  app therefore answers with stub text until an OpenRouter key is configured.
- Errors are caught and converted to a failed `GroundedLLMResponse` rather than raised
  (grounded_service.py:174-179).

### 3.3 Verification — two tiers, only one is on the live path

- **Live path (Phase 2 heuristic):** `ResponseSanitizer`
  (`app/rag/generation/sanitizer.py:52-121`) marks citations valid iff their `chunk_id` is in
  the retrieved set; groundedness = valid/total citations; `hallucination_detected = True`
  when any invalid citation or groundedness < 0.50 (`_GROUNDEDNESS_THRESHOLD`,
  sanitizer.py:24-26, 104-105); plus heuristic unverifiable-claim flagging
  (sanitizer.py:107-110). Module docstring explicitly says full claim-level detection is
  Phase 3 (sanitizer.py:9-11).
- **Full Phase 3 stack (`app/rag/verification/`: ClaimExtractor, EvidenceVerifier,
  CitationValidator, GroundednessScorer, HallucinationDetector, TokenCounter)** is
  implemented and tested (`tests/test_hallucination_detector.py` header lines 1-4) but a
  repo-wide grep shows `HallucinationDetector` is imported/exported only in
  `app/rag/verification/__init__.py` and instantiated nowhere in `app/` outside that
  package — **it is not wired into either the legacy or the agent request path**.
  `ClaimExtractor`/`EvidenceVerifier` ARE used, but offline: by the evaluation metrics
  (`app/rag/evaluation/metrics.py:21-22, 51-58, 230-240`). The agent graph's verify node
  reads the sanitizer-derived groundedness from the generation result (asserted against
  sanitizer output in `tests/test_rag_agent_nodes.py:209-211`), not the Phase 3 detector.

### 3.4 Logging / observability on the query path

- Per-retrieval: `RetrievalLogger` → hash-chained `RAGQueryLog` row incl. latency, runs on
  cache hits too (tasks.py:237-245).
- Per-generation: `GenerationLogger.log_generation` best-effort with TokenCounter-based
  context length (grounded_service.py:134-138, 240-243).
- Pipeline A/B stamping column `pipeline` ("legacy"/"agent") threaded from routes
  (tasks.py:436-438, 589; agent response marker `agent/routes.py:56-59`).

## 4. How the answer is displayed

### 4.1 Page shell — Jinja2 template

- `app/rag/templates/rag/query.html`, resolved because the blueprint declares
  `template_folder="templates"` (`app/rag/__init__.py:15-21`), extending `base.html`.
  (Verified on disk: `C:\github\NSA_webservice\app\rag\templates\rag\query.html`.)
- Controls: question textarea (`#ragQuery`, maxlength 2000), domain `<select>` populated
  server-side from `domains` (template lines 81-88), top-K number input default 10 range
  1-50 (line 92), "Use agent pipeline" checkbox `#ragUseAgent` (lines 95-98), Ask button
  `#ragSubmitBtn` (lines 100-102).
- HITL review panel `#ragReview` with Approve/Reject buttons (template lines 108-122),
  hidden by default.
- Script include: `{{ url_for('static', filename='js/rag_query.js') }}` +
  `window.RagQueryUI.init()` bootstrap (template lines 130-137).

Caveat found during tracing: the checkbox `#ragUseAgent` is rendered but `getPayload()` in
`rag_query.js` (lines 207-217) never reads it — the payload contains only
`query/top_k/collection_name`. Whether the agent graph runs is decided purely by the
server-side `RAG_USE_AGENT_PIPELINE` flag; the checkbox is currently decorative (dead
control). Flagged as a gap in §6.

### 4.2 Result rendering — vanilla JS

`renderResponse(data)` (`app/static/js/rag_query.js:76-186`) consumes exactly the
`RAGResponse`-schema dict returned by both pipelines:
- meta gauges: `groundedness_score`, `confidence`, `total_latency_ms`, `llm_model`
  (js lines 160-175);
- answer body escaped via `esc()` (textContent round-trip, js lines 14-19; inserted at
  line 177) — XSS-safe rendering;
- ⚠ hallucination banner listing `data.hallucinated_claims` when
  `data.hallucination_detected` (js lines 105-112);
- citation chips: `document_title §section_number` + truncated snippet + confidence
  (js lines 81-103);
- agent audit block when `data.pipeline === "agent"`: retry count + expanded query
  (js lines 147-158);
- retrieved-context list: title/§/act + score (4 dp) + 300-char excerpt per chunk
  (js lines 114-145).

### 4.3 Interaction states

- Loading spinner/status banners (`showStatus`/`setLoading`, js lines 34-55); user-friendly
  error map for 400/500/503 including "knowledge base unavailable" for 503 (js lines 188+).
- HITL loop: a 202 response stores `_reviewState = {thread_id, review}`, shows the review
  panel (js lines 261-269); Approve/Reject POST `/api/rag/query/agent/resume`
  `{thread_id, approved}` (js lines 292-306); another 202 re-displays review; final 200
  renders the answer (js lines 307-345).

### 4.4 UI tests (wiring evidence)

`tests/test_rag_interface.py` pins the whole display contract: page 200 for authenticated
users, 404 when disabled, login redirect, static JS served, nav link presence, expected DOM
ids (textarea/domain selector/submit), HITL section present, domains list passed to the
template, and that the JS references the agent endpoint (file docstring lines 1-16;
`test_js_references_agent_endpoint` at line 264). Route-level tests:
`tests/test_rag_agent_routes.py` (validation 400, 503-disabled, flag-off delegation,
flag-on agent path — header lines 1-5) and `tests/test_asgi_py.py` for `/api/v2/*`.

## 5. Implementation-state matrix

| Component | State | Evidence |
|---|---|---|
| Flask query API (`/api/rag/query`, `/generate`, `/eval`) | **IMPLEMENTED** | `app/rag/routes.py:162-293`; `tests/test_rag_routes.py` |
| HTML query page + nav entry | **IMPLEMENTED** | `app/rag/templates/rag/query.html`; `base.html:255-262`; `tests/test_rag_interface.py` |
| Browser JS controller (submit + render + errors + HITL) | **IMPLEMENTED** (one dead control, §4.1) | `app/static/js/rag_query.js`; interface tests |
| Legacy retrieval pipeline (classify/parse/hybrid/rerank/log) | **IMPLEMENTED** (default path) | `app/rag/tasks.py:140-312`; factory `retrieval/factory.py` |
| Advanced retrieval arms (legal typing, identifier route, ref expansion, evidence selector, retrieval cache) | **PARTIAL — flag-gated**, degrade gracefully | `tasks.py:159-183, 255-296`; `config.py:91-98` |
| KG contract fusion (`RAG_KG_FUSION`) | **PARTIAL — flag-gated, default off** | `tasks.py:468-509` |
| KG chunk expansion (`RAG_KG_EXPANSION`) | **PARTIAL — flag-gated, default off** | `tasks.py:511-555` |
| Grounded generation service (context/prompt/citations/sanitize/token-count/log) | **IMPLEMENTED** | `generation/grounded_service.py:74-289` |
| LLM provider (OpenRouter `poolside/laguna-s-2.1:free`) | **PARTIAL — functional code, stub-mode by default** without `OPENROUTER_API_KEY` | `generation/llm_client.py:73-93` |
| Heuristic verification (`ResponseSanitizer` → hallucination flag) | **IMPLEMENTED** on live path | `generation/sanitizer.py:52-121` |
| Full Phase 3 detector (`HallucinationDetector` chain) | **PARTIAL/DORMANT — built+tested, not wired into any request path**; components reused only by eval metrics | grep: only `verification/__init__.py` imports it; `evaluation/metrics.py:21-22` |
| Circuit breaker (`ResilientRAGPipeline`) | **IMPLEMENTED but scoped to `/api/v2/rag/generate` only**; Flask `/api/rag/query` bypasses it | `resilient.py:43-133`; `asgi.py:178-193` vs `routes.py:237-245` |
| LangGraph agent pipeline (self-correcting loop) | **IMPLEMENTED, DORMANT by default** (`RAG_USE_AGENT_PIPELINE=false` → legacy delegation) | `agent/graph.py:42-64`; `agent/routes.py:76-80`; `config.py:76-82` |
| M5 human-in-the-loop (202 pause + resume + checkpointer) | **PARTIAL — flag-gated** (`RAG_AGENT_HITL=false`); default `memory` checkpointer is dev-grade | `agent/routes.py:105-174`; `graph.py:10-21`; `config.py:83-90` |
| FastAPI v2 query surface (Pydantic schemas, API-key middleware) | **IMPLEMENTED** | `asgi.py:48-330` |
| Multi-domain collection picker (UI dropdown → `collection_name`) | **IMPLEMENTED** | `routes.py:57-61`; js lines 213-215; `collections.py::DOMAIN_COLLECTIONS` |
| Streaming responses / conversation history / saved queries | **PLANNED ONLY** — no code path found anywhere in `app/rag/` or `static/js/` | absence verified by search |

---

## 6. Gaps & open questions

1. ✅ **RESOLVED (2026-08-23) — Dead "Use agent pipeline" checkbox** — `rag_query.js::getPayload()` now sends `use_agent`, and `/api/rag/query/agent` accepts a per-request boolean that overrides the config flag for that single request (`agent/routes.py`; 400 on non-bool). Tests: `tests/test_rag_agent_routes.py` override cases.
2. ✅ **RESOLVED (2026-08-23) — Stub-mode visibility** — `GroundedLLMClient` exposes a public `use_stub` property; `/api/rag/health` reports `llm.mode` ("stub"|"live") + configured model so deployments can assert live-LLM operation (`curl /api/rag/health | jq -e '.llm.mode == "live"'`); the UI renders an amber "Stub mode" banner on any answer whose `llm_model` starts with `stub-`. Tests: `tests/test_rag_ui_gaps.py::TestLLMModeVisibility`.
3. ✅ **RESOLVED (2026-08-23) — Phase 3 detector now on the hot path** — `run_generation_pipeline` runs the claim-level `HallucinationDetector` over every generated answer behind `RAG_HALLUCINATION_DETECTOR` (default on, opt-out). It **augments** (never replaces) the heuristic sanitizer: sanitizer flags are kept, detector-only claims are escalated, and a `verification` block is attached to the response; failures degrade best-effort. The UI renders claim-verification stats. Tests: `tests/test_rag_detector_hotpath.py`.
4. ✅ **RESOLVED (2026-08-23) — Circuit-breaker asymmetry** — Flask `/api/rag/query` now routes through a module-level `ResilientRAGPipeline` singleton (`routes.py::_get_query_breaker`, late-bound pipeline fn); agent delegation inherits the protection. Tests: `tests/test_rag_query_breaker.py`.
5. ✅ **RESOLVED (2026-08-23) — HITL durability signal** — production still requires `RAG_AGENT_CHECKPOINTER=postgres`, but the gap is now *visible*: `graph.checkpointer_is_durable()` is surfaced via `/api/rag/health` (`agent_hitl_durable`) and both HITL 202 payloads carry a `durable` flag, with a once-per-process warning logged when HITL runs on the memory checkpointer. Tests: `tests/test_rag_ui_gaps.py::TestHitlDurabilitySignal`.
6. ✅ **RESOLVED (2026-08-23) — Session semantics in the UI** — `rag_query.js` persists every finalized answer (query + full response payload) to `localStorage` (`ragSessionHistory`, capped at 20 turns) and renders a Session-history panel: click a past turn to reload its answer and prefill the question box for follow-ups; Clear button wipes the session. Server remains one-shot by design — multi-turn context threading is still future work if ever needed.
7. ✅ **RESOLVED (2026-08-23) — Live-key A/B flip gate** — `scripts/ab_agent_vs_legacy.py` no longer pins stub mode: it defaults to **live-LLM mode** (`check_live_llm()` aborts with exit 1 when `GroundedLLMClient` resolves to stub, `--allow-stub` opts into a mechanics-only dry run explicitly marked "NOT flip-gate evidence"), computes aggregate summaries per arm, and applies a tolerance-based **parity gate** (`parity_verdict`: gold-hit@10 and groundedness may trail legacy by ≤0.05) driving the exit code — exit 0 is the recorded evidence a production `RAG_USE_AGENT_PIPELINE` flip needs. Summary written to `reports/ab_agent_vs_legacy_summary.json`. Tests: `tests/test_ab_parity_gate.py` (13).
8. ✅ **RESOLVED (2026-08-23) — CSRF interceptor regression tests** — `tests/test_rag_csrf.py` pins both ends of the contract: the rendered `/api/rag/` page carries the `csrf-token` meta tag AND the base.html `X-CSRFToken` fetch interceptor; with `WTF_CSRF_ENABLED=true` a token-less POST to `/api/rag/query/agent` is rejected with 400 (bogus token too), a POST carrying the session's token passes end-to-end, and the test-suite convention (CSRF disabled) keeps working.

---

*End of research file — all claims above traced to primary sources on 2026-08-22.*





