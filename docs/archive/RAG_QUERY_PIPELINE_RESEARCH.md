# RAG Query Pipeline Research — How One User Query Travels Through the RAG UI (Primary-Source Trace)

> **Date:** 2026-08-23 · **Method:** primary-source investigation only. Every claim below is
> cited to the owning source file (+ function/line where useful). Documentation files
> (`agents.md`, `task.md`, `plan.md`) are cited **only as context**, never as evidence.
>
> **Headline finding:** a query typed into the interactive RAG UI (`GET /api/rag/`) always
> POSTs to **`/api/rag/query/agent`** — but by default it never reaches LangGraph. The agent
> route validates the payload, then **delegates verbatim to the legacy `query()` handler**,
> which wraps `run_generation_pipeline` in a module-level **circuit breaker**
> (`ResilientRAGPipeline`, 3-failure threshold / 30 s cooldown) whose fallback degrades to
> stub LLM generation with `debug.degraded_mode=true`. The pipeline itself is
> classify → parse → hybrid retrieve (dense + Qdrant-side BM25 + identifier arm, RRF k=60,
> ensemble rerank) → optional KG fusion/expansion → grounded generation ([n]-citations,
> sanitizer) → claim-level hallucination detection (default **on**) → hash-chained audit
> logging. Every stage degrades best-effort: no single failure can hard-fail a query except
> via the breaker's final error dict.

---

## Question

"How is a user's query processed via the RAG UI?" — the complete journey of one query typed
into the interactive UI, traced end-to-end through first-party source code.

---

## 0. End-to-end flow at a glance

```
Browser — GET /api/rag/  (login-gated)
  └─ rag.query_ui renders rag/query.html + domain dropdown from DOMAIN_COLLECTIONS
       │   (app/rag/routes.py::query_ui:104-131; fail-closed 404 when RAG_ENABLED=false)
       ▼ user types question → clicks "Ask" (or Enter)
JS fetch POST /api/rag/query/agent  {query, top_k, collection_name?, use_agent?}
  │   CSRF auto-attached by base.html fetch interceptor (X-CSRFToken)
  │   (app/static/js/rag_query.js::submitQuery:272-337)
  ▼
Flask blueprint "rag" (url_prefix="/api/rag", app/rag/__init__.py:15-21)
  ├─ RAG_USE_AGENT_PIPELINE=false (DEFAULT) or use_agent=false in body
  │    → delegate to legacy query()            [app/rag/agent/routes.py:108-116]
  │    └─ _get_query_breaker().run(...)        [app/rag/routes.py:39-57, 307-312]
  │         └─ ResilientRAGPipeline (closed→open→half-open; fallback = stub generation
  │              with debug.degraded_mode=true)   [app/rag/resilient.py]
  │              └─ run_generation_pipeline   [app/rag/tasks.py:421-634]
  │                   ├─ run_retrieval_pipeline (tasks.py:122-312)
  │                   │    classify → parse → [cache?] hybrid retrieve → rerank → audit log
  │                   ├─ KG fusion (RAG_KG_FUSION) OR expansion (RAG_KG_EXPANSION)  [off]
  │                   ├─ GroundedGenerationService.generate  [grounded_service.py:74-143]
  │                   │    ContextBuilder → PromptTemplate → GroundedLLMClient (stub|live)
  │                   │    → CitationTracker → ResponseSanitizer → GenerationLogger
  │                   ├─ HallucinationDetector hot path [RAG_HALLUCINATION_DETECTOR, ON]
  │                   └─ return RAGResponse-schema dict (pipeline="legacy")
  └─ flag ON (or use_agent=true per-request override)
       → LangGraph graph: classify → retrieve → generate → verify
            ─(groundedness < 0.7 && retry_count < 2)→ expand_query ↺ retrieve
            (M5 HITL: review interrupt before finalize when RAG_AGENT_HITL=true → 202)
            [app/rag/agent/graph.py:156-223; agent/nodes.py]
  ▼
Browser renderResponse(): stub banner + gauges + answer + hallucination banner +
verification strip + citations + agent block + retrieved chunks + localStorage history
  (app/static/js/rag_query.js::renderResponse:76-227)
```

---

## 1. Entry & reception — the interactive UI

### 1.1 The page itself

- **Route:** `GET /api/rag/` → `query_ui()` in `app/rag/routes.py:104-131`. Renders
  `"rag/query.html"` and passes `domains=sorted(DOMAIN_COLLECTIONS.keys())` so the dropdown
  needs no extra AJAX round-trip (routes.py:121-131). The domain→collection map lives in
  `app/rag/collections.py:24-31` (`fssai/env/commercial/animal/wb_state/criminal` →
  `<domain>_legal_768`).
- **Fail-closed gating:** returns **404** when `cfg.rag_enabled` is false so the nav link
  disappears (routes.py:113-119). Flag declared at `app/shared/config.py:68-75` — default
  **True**, opt-out convention (`opt_in=False`).
- **Auth:** the global `before_request` gate `require_login` blocks every endpoint not in
  `public_endpoints` (`app/__init__.py:345-348`). That set contains exactly one rag entry —
  `rag.health` (`app/__init__.py:331-332`). The page, `POST /api/rag/query` and
  `POST /api/rag/query/agent` therefore all require an authenticated session.
- **CSRF:** `csrf.init_app(app)` (`app/__init__.py:277-284`). The browser side is covered by
  the base-template fetch interceptor that reads `meta[name="csrf-token"]`
  (`app/templates/base.html:22-23`) and attaches `X-CSRFToken` to every fetch
  (`base.html:416-430`); `rag_query.js` relies on this rather than setting headers itself
  (file header comment, `rag_query.js:8`).

### 1.2 What the user submits

Template `app/rag/templates/rag/query.html`:

| Control | Element | Constraints | Lines |
|---|---|---|---|
| Question | `textarea #ragQuery[name=query]` | `maxlength="2000"` (browser-enforced) | 83-89 |
| Domain | `select #ragDomain[name=domain]` | server-rendered options; empty = "All domains (FSSAI)" | 95-100 |
| Results | `input number #ragTopK[name=top_k]` | `min=1 max=50 value=10` | 105 |
| Pipeline toggle | `checkbox #ragUseAgent[name=use_agent]` | unchecked by default | 109-110 |
| Submit | `button #ragSubmitBtn` ("Ask") | — | 113-115 |

Plus a HITL review panel (`#ragReview` with Approve/Reject buttons, lines 121-135), a results
container (137-139) and a session-history panel (141).

### 1.3 Client-side controller (`app/static/js/rag_query.js`, IIFE)

- **Payload assembly** — `getPayload()` (lines 248-264): trims the question, `top_k =
  parseInt(...) || 10`; selecting a domain maps it to
  `payload.collection_name = domain + "_legal_768"` (255-257); the checkbox value is sent as
  `use_agent` whenever the element exists — i.e. **both true and false are sent**, making the
  checkbox authoritative per request ("fixes the previously dead checkbox", comment 258-262).
- **Client-side validation** — `validateQuery()` (266-270): non-empty, ≤ 2000 chars
  (mirrors the textarea `maxlength`). Failures show a status banner and abort before any
  network call (278-282).
- **Submission** — `submitQuery()` (272-337): disables the button + spinner (284),
  POSTs JSON to `/api/rag/query/agent` (294-298); if a HITL review is pending it re-attaches
  `thread_id` to the body (290-292). Enter-without-shift also submits (init, 514-521).
- **Error mapping** — status-code-keyed friendly messages for 400/500/503; raw detail only to
  the console (229-246).
- **Session history** — `localStorage["ragSessionHistory"]`, max 20 entries, saved on success,
  reloadable/clearable (400-493).

---

## 2. HTTP layer

### 2.1 Blueprint wiring

`app/rag/__init__.py:15-21` defines `rag_bp` with `url_prefix="/api/rag"`; routes modules are
imported at the bottom so decorators register (lines 25, 31). The agent route exists
unconditionally but "delegates to the legacy pipeline until RAG_USE_AGENT_PIPELINE=true";
`langgraph` itself is imported lazily inside `app/rag/agent/graph.py`, so the app boots
without it (docstring lines 27-30).

### 2.2 `POST /api/rag/query/agent` — `app/rag/agent/routes.py::query_agent` (74-158)

Server-side validation, in order (each failure → 400 unless noted):

1. `_rag_enabled()` — `cfg.rag_enabled`; off → **503** `{"error": "RAG is disabled."}` (89-90).
2. Body must be a JSON object via `request.get_json(silent=True)` (92-94).
3. `query` must be a non-empty string (96-98).
4. `top_k` must be an int ≥ 1, default 10 (100-102).
5. `use_agent`, if present, must be a boolean (104-106).

**Per-request override semantics** (the key behavior, agent/routes.py:108-116):

```python
use_agent = _use_agent_pipeline() if requested_agent is None else requested_agent
if not use_agent:
    from app.rag.routes import query
    return query()          # identical behaviour to /api/rag/query
```

The UI checkbox therefore wins over the deploy-time flag for that single request; when neither
requests the agent, the response is exactly the legacy pipeline's.

**Agent execution** (flag on): optional `thread_id` validated only when `RAG_AGENT_HITL` is on
(118-120); `initial_state(...)` + `run_agent(state, thread_id=..., hitl=...)` imported lazily
(122-132); missing `langgraph` → **503**, like the disabled case (133-136). A paused run
(`__interrupt__` in result) returns **202** `awaiting_review` carrying `thread_id`, the review
payload, a `durable` durability flag and a resume hint (141-155); a completed run returns
`result.get("response")` — the same `RAGResponse` schema plus `pipeline:"agent"` + an `agent`
block (157-158; stamped by `finalize_node`, §7).

**HITL durability warning:** `_warn_hitl_durability` logs once per process when HITL runs on
the non-durable memory checkpointer, and surfaces `durable` in the 202 payload (49-71).

### 2.3 `POST /api/rag/query/agent/resume` (161-214)

Validates `RAG_ENABLED` (503), `RAG_AGENT_HITL` (off → **400**, 174-175), `thread_id`
non-empty string (181-183), `approved` bool default true (185-187); calls `resume_agent`
(189-192); another pause → another 202 (200-212); completion → final response dict (214).

### 2.4 Legacy `POST /api/rag/query` — `app/rag/routes.py::query` (274-317)

Same 503/dict/query/top_k checks (286-299), then:

```python
result = _get_query_breaker().run(
    query=query_str, top_k=top_k,
    collection_name=payload.get("collection_name"),
    filters=payload.get("filters"))
```
(routes.py:301-312)

`_get_query_breaker` (34-57) builds a **module-level singleton** `ResilientRAGPipeline` so the
open/closed state survives across requests ("that is the point of a breaker", 34-35); its
`pipeline_fn` resolves `tasks_mod.run_generation_pipeline` *at call time* (late binding so
tests can monkeypatch it, 42-46). Any exception escaping the breaker → **500** (313-315).

### 2.5 Related endpoints

- `GET /api/rag/health` — public probe reporting `llm.mode` (`stub`/`live`),
  `agent_hitl`, `agent_checkpointer`, `agent_hitl_durable` (routes.py:60-101).
- `POST /api/rag/generate` — same `run_generation_pipeline`, accepts pre-retrieved `chunks`
  to skip retrieval (226-271). Unused by the UI JS but shares the entire downstream path.
- The FastAPI gateway exposes the same services under `/api/v2/*` (`asgi.py`,
  `app/api/routers.py`) — outside this trace's UI scope.

---

## 3. Retrieval pipeline — `app/rag/tasks.py::run_retrieval_pipeline` (122-312)

### 3.1 Classify + parse

- `QueryClassifier` is **rule-based, not LLM-based** (deterministic, no API keys;
  `app/rag/retrieval/query_classifier.py:1-19`). Priority: amendment → section → case law →
  provision → general (`classify`, 64-84; ordered regex patterns 48-61; `QueryType` enum
  31-38).
- `QueryParser.parse(query, query_type)` dispatches to a per-type sub-parser
  (`SectionQueryParser` / `AuthorityQueryParser` / `CaseLawQueryParser` /
  `JurisdictionQueryParser`, 246-259); its extracted filters are merged with the caller's
  `filters` (tasks.py:154-157).

### 3.2 Flag-gated pre-retrieval arms

- **Legal query typing** — `cfg.legal_query_typing` (**default True**, opt-out,
  config.py:114-121): `classify_legal_query(query)` produces a legal query type used for
  query-type-aware reranking weights (tasks.py:164-168).
- **Identifier route** — `cfg.identifier_route` (**default True**, opt-out,
  config.py:122-129): builds a lexical `"{Act} section {N}"` query from identifiers detected
  in the question text, run as a *parallel additive* retrieval arm
  ("+13.3pp candidate-pool ceiling", tasks.py:170-183). Best-effort: no identifiers → no arm.

### 3.3 The retrieval stack — composition root

`build_hybrid_retriever(collection_name)` (`app/rag/retrieval/factory.py:101-114`) is "the ONE
place the retrieval stack is built" (module docstring 1-8) after a historical multi-domain bug
where a hand-copied sparse store silently searched `fssai_legal_768` for every domain:

- **Dense** — `DenseRetriever(collection_name or "")`; empty resolves to `fssai_legal_768`
  (`dense_retriever.py:46`). Query embedding via `embed_query`: remote `RemoteEmbedClient`
  when `RAG_EMBED_ENDPOINT` is set (Modal-hosted inference), else local `SentenceTransformer`;
  lazy local fallback controlled by `RAG_EMBED_REMOTE_FALLBACK` (dense_retriever.py:121-162;
  flags at config.py:204-219). Search goes through `QdrantStore.dense_search`, using the
  named dense vector when the collection declares sparse vectors (240-251).
- **Sparse** — `SparseRetriever` over a collection-aware `QdrantStore` +
  `SparseEmbeddingService`, `server_bm25=cfg.qdrant_bm25` (factory.py:35-55). Primary path:
  real BM25 sparse vectors in Qdrant (`search_sparse`, or `search_sparse_text` when
  `server_bm25` — Qdrant computes the BM25 vector in-cluster); fallback: rapidfuzz fuzzy
  matching against an in-memory corpus dict (sparse_retriever.py:1-19, 149-225).
- **Reranker** — `build_reranker` (factory.py:58-98): `EnsembleReranker` when
  `RAG_ENSEMBLE_RERANK` is on (**default True**, opt-out, config.py:160-167), else plain
  `Reranker`. The ensemble ranks by deterministic `sec_act` legal features first
  (weights 2.0/1.5/1.0), then scores only the top `ce_head` (default 30) with a cross-encoder
  (min-max normalized, weighted by `ce_weight` default 0.5), with dynamic CE skipping on
  exact-match queries (`reranker.py::EnsembleReranker` docstring; weights at the class
  constants below it). When `RAG_RERANKER_ENDPOINT` is set the CE head is scored by a remote
  TEI `/rerank` endpoint injected as the encoder, degrading remote → local → sec_act-only
  (factory.py:66-96; flags config.py:168-201).

### 3.4 Hybrid fusion

`HybridRetriever.retrieve` (hybrid_retriever.py:57-220):

1. **Server-side single-roundtrip fusion** when there is no identifier arm and the sparse
   store is sparse-capable: dense vector + BM25 text fused on the cluster
   (`hybrid_search_text`) or with a client-side-computed sparse vector (`hybrid_search`);
   any failure falls through to client-side RRF (94-140).
2. **Client-side RRF** otherwise: dense arm (142), sparse arm (143), optional identifier arm
   at `top_k*2` without filters (145-154), then Reciprocal Rank Fusion
   `score = Σ 1/(rank + k)` with k=60 (32, 156-196), keeping the higher raw score on merges.
3. **Optional rerank** of the fused top-k (206-210); reranker failure returns unfused results.

### 3.5 Cache + audit log

- **Retrieval cache** — `RAG_RETRIEVAL_CACHE` (**default False**, config.py:100-106):
  stdlib TTL(600 s)+LRU(512) memoization of the deterministic retrieval step keyed on
  (query, top_k, collection, filters, query types, identifier form)
  (tasks.py:39-53, 98-119). Cache hits rebuild a fresh `SearchResult` with
  `source="cache"`, `latency_ms=0` so cached objects are never mutated (207-225).
- **Audit logging always runs** — cache hit or not, `RetrievalLogger.log` persists the query
  to the hash-chained `RAGQueryLog` (237-245), optionally stamped with the calling pipeline
  (`legacy`/`agent`) for A/B comparison (127-138).

### 3.6 Parallel post-processing layer (all best-effort)

After the core result, three flag-gated enrichments attach metadata without changing chunks:
legal-identity parsing (`ENABLE_LEGAL_IDENTITY`, **default True**, config.py:144-151),
reference-graph candidate expansion (`ENABLE_REFERENCE_EXPANSION`, **default False**,
config.py:137-143), evidence-set selection (`ENABLE_EVIDENCE_SELECTOR`, **default False**,
config.py:130-136) — tasks.py:255-296. The returned dict carries `chunks`, `query_type`,
`identifier`, `latency_ms`, `log_id`, etc. (298-312).

---

## 4. Generation — `run_generation_pipeline` (tasks.py:421-634) → `GroundedGenerationService`

### 4.1 Orchestration entry

`run_generation_pipeline` (tasks.py:421-439): if `chunks is None` it first calls
`run_retrieval_pipeline` and adopts its `query_type` (447-459); the `pipeline` argument is
forwarded into the retrieval audit log and echoed back in the result (436-438, 633).

### 4.2 KG hooks (both default off)

- **KG contract fusion** — `RAG_KG_FUSION` (**default False**, config.py:360-366): runs the
  graph-RAG retrieval contract (`provisions_for_query`), converts provisions to chunk-like
  objects and **RRF-fuses** them (`rrf_fuse_chunks`, k=60) with the retrieved chunks up to
  `ContextBuilder().max_context_chunks` — "the production equivalent of eval arm G"
  (tasks.py:468-509). Best-effort; failure records an error in `kg_contract`.
- **KG expansion** — `RAG_KG_EXPANSION` (**default False**, config.py:353-359): expands
  retrieved chunk IDs through the Neo4j KG instead. The two paths are **mutually exclusive**:
  expansion is skipped when fusion already injected provisions (511-537).
- Cap: `RAG_KG_MAX_PROVISIONS` default 5 (config.py:367-369).

### 4.3 `GroundedGenerationService.generate(query, chunks, query_type)` (grounded_service.py:74-143)

Seven steps, each isolated and each degrading gracefully:

1. **Early exit** — no chunks ⇒ empty `RAGResponse` with `debug.empty_context=true`; no LLM
   call at all (95-117).
2. **Context build** — `ContextBuilder.build`: sorts chunks by score desc, caps at
   `max_chunks=10` / `max_context_chars=12_000`, formats `[Source n] <title>, Section X
   (Authority…)` blocks joined by `---`, truncating the last block when over budget
   (context_builder.py:42-111). Failure ⇒ empty context, pipeline continues (149-161).
3. **Prompt render** — `PromptTemplate.render_default("grounded_qa", ...)`. System prompt
   mandates answering *only* from context with `[n]` markers
   (`GROUND_QA_SYSTEM_PROMPT`, prompt_template.py:14-21); user template wraps question +
   context (82-89). Domain-parameterized prompts exist (`DOMAIN_SYSTEM_PROMPTS`, 27-80) but
   the legacy service calls `render_default` **without a domain**, so queries from the UI get
   the FSSAI prompt unless something passes `domain` explicitly (grounded_service.py:163-167).
4. **LLM call** — `GroundedLLMClient.call`: stub vs live resolution at construction time —
   stub when neither `OPENROUTER_API_KEY` nor `OPENAI_API_KEY` is set, or when
   `RAG_USE_STUB_LLM=true` (**default False**, config.py:344-351)
   (llm_client.py:86-90). Model defaults to `poolside/laguna-s-2.1:free`
   (`DEFAULT_MODEL`, 73-74). The stub returns a canned sentence with model name
   `stub-{model}` and placeholder token usage (145-156) — this name is what triggers the UI's
   stub banner (§8). Live path: httpx POST `{base_url}/chat/completions`, 3 attempts with
   exponential backoff, falls back to the `reasoning` field when `content` is null
   (162-232). Call failure ⇒ error response, not an exception (grounded_service.py:174-179).
5. **Citation extraction** — `CitationTracker.extract` maps each `[n]` marker to its chunk via
   the `BuiltContext.citations` index map ([1] = first chunk *in the assembled context*, which
   may differ from retrieval order) (181-207).
6. **Sanitization** — `ResponseSanitizer.sanitize` (sanitizer.py:67-123): a citation is valid
   iff its `chunk_id` exists among retrieved chunks; `groundedness = valid/total citations`;
   `hallucination_detected = any invalid citation OR groundedness < 0.50`
   (`_GROUNDEDNESS_THRESHOLD = 0.50`, line 26); heuristic claim flagging for section numbers
   mentioned but never retrieved (129-152); confidence = weighted blend
   `0.5*groundedness + 0.3*avg_citation_confidence + 0.2*avg_chunk_score` (154-178). The
   response text itself is never rewritten (75-78).
7. **Logging + assembly** — `GenerationLogger.log_generation` best-effort with real token
   estimates from `TokenCounter` (209-243); `_assemble_response` builds the final
   `RAGResponse`, falling back to estimated tokens when the LLM's usage dict is empty/stubbed,
   and packaging diagnostics into `debug` (245-289).

---

## 5. Verification on the hot path — Phase 3 HallucinationDetector (tasks.py:560-601)

Gated by `RAG_HALLUCINATION_DETECTOR` — **default True, opt-out** (config.py:91-98). When the
answer and chunks are non-empty:

- `HallucinationDetector().detect(answer, chunks, citations=...)` runs the claim-extraction →
  evidence-verification → citation-validation chain (573-577).
- Its verdict merges into a top-level **`verification`** block:
  `{enabled, detected, groundedness_score, claims_total/verified/unverified, llm_verified,
  confidence, escalated_claims}` (581-591).
- **Escalate-only semantics:** claim-level hallucinations the sanitizer missed are appended to
  `hallucinated_claims` and set `hallucination_detected=True`; sanitizer flags are *never*
  removed ("Augments (never replaces)", comment 564-567; code 592-598).
- **Best-effort:** detector exceptions degrade to `verification={"enabled": True, "error": ...}`
  without failing the query (599-601).

---

## 6. Resilience — `app/rag/resilient.py::ResilientRAGPipeline`

State machine (`CircuitState`, 33-40; `run` 80-98; internals 125-148):

| State | Entry condition | Behavior |
|---|---|---|
| **closed** | default / after success | `pipeline_fn` runs; success resets counters (135-139) |
| **open** | `failure_count >= failure_threshold` (default **3**, line 61) via `_on_failure` (141-148) | fail fast → `_safe_fallback(query, ...)` (91-93) |
| **half-open** | open and `cooldown_seconds` (default **30.0**) elapsed since last failure | probe allowed: circuit reopens implicitly closed; next failure re-opens, success closes (129-133) |

- The breaker "never raises": fallback failures are caught twice over. First-level fallback is
  `_default_fallback`: a stub-mode `GroundedGenerationService().generate(query, [])` — i.e.
  **no retrieval** — returning `debug.degraded_mode=true, reason="circuit_open"`
  (150-183). If even that fails, a final error dict with empty answer,
  `hallucination_detected=True` is returned (184-193, 154-164).
- The legacy UI route uses the shared singleton from §2.4, so repeated Qdrant/LLM outages
  degrade *all* subsequent queries to the stub banner until the cooldown probe succeeds.

---

## 7. Agent path (flag-gated) — `app/rag/agent/graph.py` + `nodes.py`

### 7.1 Graph topology (`build_graph`, graph.py:156-223)

```
START → classify → retrieve ─(evidence_selector flag)─?→ generate → verify
        ▲                                                      │ (route_after_verify)
        └────────────── expand_query ◄── groundedness < 0.7 AND retry_count < max_retries(2)
                                                               │ else
                                                            finalize → END   (hitl=False)

hitl=True: verify → review(interrupt) → route_after_review → approved ? finalize : expand_query
```

Nodes are thin adapters over the production services ("the agent path and the legacy path
share exactly the same retrieval … generation and verification code", nodes.py docstring 1-11):

- `classify_node` — wraps `QueryClassifier`; failure degrades to `"general"` (30-57).
- `retrieve_node` — calls `run_retrieval_pipeline(..., pipeline="agent")` with the
  **expanded query when one exists** (25-27, 60-95); chunks kept as plain dicts so state stays
  JSON-serializable for checkpointing (state.py:38-43).
- `generate_node` — calls `run_generation_pipeline(chunks=state["chunks"], pipeline="agent")`,
  skipping re-retrieval while keeping KG fusion/hallucination detection identical to legacy
  (133-169).
- `verify_node` — records the score already computed during generation for the conditional
  edge (172-184). Threshold constant: `GROUNDEDNESS_THRESHOLD = 0.7` (nodes.py:22);
  `max_retries` fixed at 2 in `initial_state` (state.py:63-93).
- `expand_query_node` — reuses `GroundedLLMClient` with a rewrite prompt
  (temperature 0, max_tokens 120); on failure keeps the original query but always increments
  `retry_count` so the loop terminates (187-233).
- `finalize_node` — stamps `pipeline:"agent"` and the `agent` metadata block
  `{retry_count, expanded_query, groundedness, hallucination_detected, audit_trail}`
  (236-255).

Both graph variants are compiled once at import (231-236); the default graph has no
checkpointer, and `run_agent` rebuilds with one only when a `thread_id` is given (226-230,
272-281).

### 7.2 M5 HITL + checkpointer durability

- `review_node` pauses via `langgraph.types.interrupt`, surfacing query/answer/groundedness/
  hallucination/retry_count for human review; the resume value lands on state as `approved`
  (67-86). Approved → `finalize`; rejected → `expand_query` (re-generate with rewritten
  query) (56-64, 203-212).
- Checkpointer selection (`_build_checkpointer`, 111-153): `memory`
  (**default**, config.py:84-90) = process-wide `MemorySaver` singleton — paused threads are
  lost on restart; `postgres` = `PostgresSaver` against `DATABASE_URL` with idempotent table
  setup, degrading to no-checkpointing on missing deps/bad DSN (126-142); `none` disables
  resume. Only postgres is "durable" (`checkpointer_is_durable`, 94-103) — surfaced in
  `/api/rag/health` and the 202 payloads (§2.2).
- `resume_agent(thread_id, approved)` re-invokes with `Command(resume={"approved": ...})`
  under the same thread id (291-313).

---

## 8. Response shape & browser rendering

### 8.1 The wire format

`RAGResponse` dataclass (`app/rag/retrieval/result.py:108-134`): `query`, `query_type`,
`answer`, `citations[]` (`Citation`: chunk_id, section_number, document_title, document_type,
authority, url, snippet, confidence — 89-105), `retrieved_chunks[]`
(`RetrievedChunk.to_dict`, 38-52), `groundedness_score`, `hallucination_detected`,
`hallucinated_claims`, `confidence`, retrieval/generation/total latencies,
prompt/completion tokens, `token_usage`, `debug`. The route-level dict adds `kg_expansion`,
`kg_contract`, `verification`, and `pipeline` (`"legacy"` default) (tasks.py:612-634). Agent
runs further add `pipeline:"agent"` + the `agent` audit block (nodes.py:236-255).

### 8.2 `rag_query.js::renderResponse` (76-227)

Rendered top-to-bottom inside one answer card:

| UI element | Source fields | Lines |
|---|---|---|
| **Stub banner** — "⚠ Stub mode … without a live LLM (no OPENROUTER_API_KEY…)" | fires when `llm_model` starts with `"stub"` | 188-197 |
| **Gauges** — Groundedness / Confidence / Latency ms / Model | `groundedness_score`, `confidence`, `total_latency_ms`, `llm_model` | 199-215 |
| **Answer text** (HTML-escaped via `esc()`) | `answer` | 216-218 |
| **Hallucination banner** ⚠ with joined claims | `hallucination_detected`, `hallucinated_claims` | 105-112 |
| **Verification strip** — ✓/⚠ "N/M claims evidence-backed · K unverified · E escalated · score"; error variant shows a quiet "unavailable" note | `verification.*` | 114-140 |
| **Citations** — "Title §N" + snippet (200 chars) + conf | `citations[].section_number/document_title/snippet/confidence` | 81-103 |
| **Agent block** — "Pipeline: agent \| retries: n \| expanded query: …" (only when `pipeline === "agent"`) | `agent.retry_count`, `agent.expanded_query` | 175-186 |
| **Retrieved context** — per-chunk title §section / act + score + 300-char text | `retrieved_chunks[]` | 142-173 |

All user-derived strings pass through `esc()` / `truncate()` before insertion (14-32) —
no raw HTML from server data reaches the DOM unescaped.

### 8.3 HITL interaction in the browser

A **202** response stores `_reviewState = {thread_id, review}` and shows the review panel
(308-316; `showReview` 59-69). Approve/Reject call `resumeReview(approved)` →
`POST /api/rag/query/agent/resume {thread_id, approved}` (340-354); another 202 re-pauses
(366-370); completion renders the final response and saves it to history under the reviewed
question (381-387).

---

## 9. Stage-by-stage table (default configuration)

| # | Stage | Owner file :: function | Input → output | Failure mode |
|---|---|---|---|---|
| 0 | Page render | `app/rag/routes.py::query_ui` | GET → HTML + domain list | 404 if RAG disabled |
| 1 | Client validation | `rag_query.js::validateQuery` | textarea → payload | banner, no request |
| 2 | Reception/validation | `agent/routes.py::query_agent` (+ delegation to `routes.py::query`) | JSON body → validated args | 400/503 |
| 3 | Circuit breaker | `routes.py::_get_query_breaker` / `resilient.py::run` | args → pipeline-or-fallback | stub degraded response, `debug.degraded_mode` |
| 4 | Classify + parse | `retrieval/query_classifier.py::QueryClassifier.classify`, `QueryParser.parse` | query → QueryType + filters | general_qa fallback |
| 5 | Hybrid retrieve | `retrieval/hybrid_retriever.py::retrieve` (+ `factory.py`) | query+filters → fused chunks | arm errors degrade; both fail ⇒ empty result w/ error |
| 6 | Rerank | `retrieval/reranker.py::EnsembleReranker.rerank` | fused → reordered top-k | falls back to unfused results |
| 7 | Retrieval audit | `retrieval/logger.py::RetrievalLogger.log` (called at tasks.py:239-245) | result → hash-chained log row | logged, never blocks |
| 8 | KG fusion/expansion | `tasks.py:480-537` behind `kg/hybrid.py` | chunks → +provisions | best-effort, recorded in `kg_contract`/`kg_expansion` |
| 9 | Context build | `generation/context_builder.py::build` | chunks → `[Source n]` context | empty context continues |
| 10 | Prompt render | `generation/prompt_template.py::render_default` | query+context → prompts | minimal fallback prompt |
| 11 | LLM call | `generation/llm_client.py::GroundedLLMClient.call` | prompts → text+usage | error response object |
| 12 | Citations | `generation/citation_tracker.py` (via grounded_service.py:181-207) | `[n]` markers → Citation list | empty citations continue |
| 13 | Sanitize | `generation/sanitizer.py::sanitize` | text+citations → valid/invalid + scores | — |
| 14 | Claim verification | `verification/HallucinationDetector` at tasks.py:569-601 | answer+chunks → `verification` block | `verification.error`, no raise |
| 15 | Generation logging | `generation/logger.py` (grounded_service.py:209-243) | metrics → DB row | warn-only |
| 16 | Render | `rag_query.js::renderResponse` | JSON → DOM | friendly error card on !ok |

## 10. Flag inventory relevant to this journey

All resolved through the config seam (`cfg`, `app/shared/config.py`; "opt-out" = default True).

| Flag | Default | Where it bites |
|---|---|---|
| `RAG_ENABLED` | True (opt-out) | page 404 / endpoints 503 (config.py:68-75) |
| `RAG_USE_AGENT_PIPELINE` | False | agent vs legacy on `/api/rag/query/agent` (76-82); overridable per-request by `use_agent` |
| `RAG_AGENT_HITL` | False | review interrupt + resume flow (83) |
| `RAG_AGENT_CHECKPOINTER` | `"memory"` | HITL durability (84-90) |
| `RAG_HALLUCINATION_DETECTOR` | True (opt-out) | §5 hot-path verification block (91-98) |
| `RAG_RETRIEVAL_CACHE` | False | §3.5 memoization (100-106) |
| `RAG_QDRANT_BM25` | False | Qdrant-side BM25 sparse arms (107-113) |
| `RAG_LEGAL_QUERY_TYPING` | True (opt-out) | rerank weight selection (114-121) |
| `RAG_IDENTIFIER_ROUTE` | True (opt-out) | lexical identifier arm (122-129) |
| `ENABLE_EVIDENCE_SELECTOR` | False | evidence node/set (130-136) |
| `ENABLE_REFERENCE_EXPANSION` | False | reference-graph expansion (137-143) |
| `ENABLE_LEGAL_IDENTITY` | True (opt-out) | chunk identity parsing (144-151) |
| `RAG_ENSEMBLE_RERANK` | True (opt-out) | ensemble vs plain reranker (160-167) |
| `RAG_RERANKER_MODEL/_ENDPOINT/_MODE/_REMOTE_FALLBACK/_CE_HEAD/_CE_WEIGHT` | ms-marco-MiniLM / "" / `"tei"` / True / 30 / 0.5 | CE wiring (168-201) |
| `RAG_EMBED_ENDPOINT/_REMOTE_FALLBACK` | "" / True | remote dense embeddings (204-219) |
| `RAG_USE_STUB_LLM` (+ API-key presence) | False | stub LLM resolution (344-351; llm_client.py:86-90) |
| `RAG_KG_EXPANSION` / `RAG_KG_FUSION` / `RAG_KG_MAX_PROVISIONS` | False / False / 5 | mutually exclusive KG hooks (353-369) |

---

## 11. Gaps & open questions

1. **Domain dropdown does not reach the prompt.** The JS maps domain → `collection_name`
   (rag_query.js:255-257), so retrieval searches the right Qdrant collection — but
   `GroundedGenerationService._render_prompt` calls `render_default(query, built.context)`
   without a domain (grounded_service.py:163-167), so every UI answer uses the FSSAI system
   prompt even for criminal/env questions. `PromptTemplate.DOMAIN_SYSTEM_PROMPTS`
   (prompt_template.py:27-80) exists precisely for this but is not threaded from the UI path.
2. **`top_k` client max is unenforced server-side.** The template constrains `max=50`
   (query.html:105) and the client coerces to an int, but the Flask routes only check
   `int ≥ 1` (agent/routes.py:100-102) — a crafted request may pass any positive int.
3. **Breaker fallback answers are unlabeled in a dedicated field.** They carry
   `debug.degraded_mode=true` (resilient.py:182), but `renderResponse` has no UI affordance
   for `debug.degraded_mode` — users only see the generic stub banner (which *does* fire,
   since the fallback model name starts with `stub`). A distinct "degraded / circuit-open"
   indicator would be more precise.
4. **HITL thread continuity depends on process memory by default.** With
   `RAG_AGENT_CHECKPOINTER=memory` (the default), a worker restart loses the paused thread;
   the 202 payload's `durable:false` + once-per-process warning flag it to operators
   (agent/routes.py:53-71), but the UI does not surface it.
5. **Session history holds full responses in localStorage** (max 20 entries,
   rag_query.js:406-437) — including retrieved chunk text. Acceptable for a single-user
   internal tool; worth noting for shared-browser environments.
6. **The legacy route's breaker is per-process state.** Multi-worker deployments each keep
   their own failure counter/cooldown (module global, routes.py:36); the "3 failures"
   threshold is therefore per-worker, not global.
7. **Open question:** `POST /api/rag/generate` bypasses the breaker entirely (direct
   `run_generation_pipeline` call, routes.py:256-266). Not reachable from this UI, but any
   future UI use of that endpoint would lose circuit protection.

---

*End of research notes. No files other than this document were modified.*

