# Legal AI — Frontend Implementation Plan

> **Status:** ✅ The RAG query UI described here is **already implemented** in the codebase.
> This file elaborates the original high-level bullets into a **detailed, step-by-step
> implementation plan grounded in the actual code**, mapping every user-facing goal
> to the concrete file(s) / function(s) that deliver it, and flagging what remains
> to do.
>
> **Scout verdict:** of the four original bullets — _Implement Frontend, HTML template
> with a searchbox, JS to accept/deconstruct/gather/reconstruct_ — **all four are
> already shipped** and exceed the original vision (agent pipeline, human-in-the-loop
> review, groundedness scoring, KG expansion, batch evaluation). The sections below
> document the implementation steps so a new developer can trace the full flow end to
> end.

---

## 0. Codebase evaluation (what exists vs. the original brief)

| Original bullet                                   | Actual implementation                                                                                                                                         | File(s)                                                           |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Implement Frontend                                | Full interactive page: query box, domain picker, top-K, agent toggle, HITL review, status, results                                                            | `app/rag/templates/rag/query.html` + `app/static/js/rag_query.js` |
| HTML template with searchbox                      | `textarea#ragQuery` + controls, styled to the app's CSS variables                                                                                             | `app/rag/templates/rag/query.html`                                |
| JS — accept the query                             | `getPayload()` reads the textarea + domain + top-K; `validateQuery()` enforces non-empty + 2000-char limit                                                    | `app/static/js/rag_query.js`                                      |
| JS — deconstruction of query into legal questions | **Server-side**, not JS: `QueryClassifier.classify()` → `QueryType`; `QueryParser` extracts sections/authorities/case-law/jurisdictions                       | `app/rag/retrieval/query_classifier.py`                           |
| JS — gather chunks                                | POSTs to `/api/rag/query/agent` → `run_generation_pipeline` → `run_retrieval_pipeline` (HybridRetriever: dense + sparse + optional identifier arm + reranker) | `app/rag/tasks.py`, `app/rag/retrieval/hybrid_retriever.py`       |
| JS — reconstruction of answer + output            | `renderResponse()` renders answer card, gauges (groundedness/confidence/latency/model), hallucination warning, citations, agent block, retrieved chunks       | `app/static/js/rag_query.js`                                      |

**Result schema the frontend consumes:** `RAGResponse` (dict) — see §5 for the
field-by-field contract. The backend serializes it in
`app/rag/tasks.py::run_generation_pipeline`.

---

## 1. Blueprint + route setup — `app/rag/__init__.py` → `app/__init__.py`

**Step 1.1 — Define the blueprint** (`app/rag/__init__.py`, lines 13–20)
Create the Flask blueprint with the canonical URL prefix so every route is namespaced
under `/api/rag/*`:

```python
rag_bp = Blueprint("rag", __name__, url_prefix="/api/rag")
```

**Step 1.2 — Register it (gated)** (`app/__init__.py`)

- `app/__init__.py` line 210 sets `app.config["RAG_ENABLED"]` from the `RAG_ENABLED`
  env var (default `true`).
- `app/__init__.py` lines 586–588 registers the blueprint inside the factory:

    ```python
    from app.rag import rag_bp
    app.register_blueprint(rag_bp)
    ```

- The nav link in `app/templates/base.html` (lines 217–222) is wrapped in
  `{% if current_app.config.get('RAG_ENABLED', True) %}` so the "Legal RAG" tab only
  appears when the module is active.

**Step 1.3 — Health probe (public, fail-closed)** (`app/rag/routes.py`)
`GET /api/rag/health` returns `{"status":"ok","phase":"5","phase_name":"ingestion_api"}`.
No auth (the global `require_login` gate lists it in `public_endpoints`). This lets
the UI know whether the RAG service is reachable before the user types.

---

## 2. HTML template — the searchbox page

**File:** `app/rag/templates/rag/query.html` (extends `base.html`)

**Step 2.1 — Layout** (`#rag-page`)
A centered card (max-width 920px) with a header (`#rag-header`) carrying the
`<i class="fa-robot"></i> Legal RAG` title and a "Ready" badge. CSS uses the app's
design tokens (`--border`, `--accent-primary`, `--radius`, etc.) so it inherits the
site theme without duplication.

**Step 2.2 — The searchbox** (`#rag-query-box`)
A `<textarea id="ragQuery">` (maxlength 2000) labeled "Legal question". This is the
single element the user types their legal question into. `resize: vertical` and the
Merriweather serif font keep it legal-reading-friendly.

**Step 2.3 — Controls** (`#rag-controls`)

- `#ragDomain` `<select>` — domain picker. The template renders `domains` (passed from
  the route — `sorted(DOMAIN_COLLECTIONS.keys())`) so the user can scope retrieval to
  one of the per-domain Qdrant collections (`env_legal_768`, `commercial_legal_768`,
  …). The value maps to `collection_name` = `<domain>_legal_768`.
- `#ragTopK` `<input type="number">` — results count (default 10, range 1–50).
- `#ragUseAgent` checkbox — toggles the LangGraph agent pipeline (defaults to the
  legacy pipeline when off; see `RAG_USE_AGENT_PIPELINE`).
- `#ragSubmitBtn` — primary "Ask" button.

**Step 2.4 — Status / review / results zones**
Three empty containers the JS populates:

- `#ragStatus` — info/error/success banner with a spinner.
- `#ragReview` — HITL review panel (approve/reject buttons) — only shown when the
  agent route returns HTTP 202 `awaiting_review`.
- `#ragResults` — rendered answer card (empty-state placeholder until first query).

**Step 2.5 — Wire the script** (`{% block extra_js %}`)

```html
<script src="{{ url_for('static', filename='js/rag_query.js') }}"></script>
<script>
    (function () {
        "use strict";
        window.RagQueryUI.init();
    })();
</script>
```

---

## 3. JS — accept the query

**File:** `app/static/js/rag_query.js` (IIFE module exported as `window.RagQueryUI`)

**Step 3.1 — Read inputs** (`getPayload()`)
Reads the DOM values and returns `{payload, query}`:

- `document.getElementById("ragQuery").value.trim()` — the raw legal question.
- `document.getElementById("ragDomain").value` — maps to `collection_name`
  (`<domain>_legal_768`) when non-empty.
- `parseInt(ragTopK.value, 10) || 10` — the `top_k`.

The agent-route toggle (`#ragUseAgent`) is a client-side display concern; the server
decides which pipeline actually runs based on `RAG_USE_AGENT_PIPELINE`.

**Step 3.2 — Validate** (`validateQuery(query)`)
Returns an error string or `null`:

- Empty → "Please enter a legal question."
- Length > 2000 → "Question is too long (max 2000 characters)."

**Step 3.3 — Submit** (`submitQuery()`)

- Runs validation; if it fails, `showStatus(error, "error")` and aborts.
- `setLoading(btn, true)` → swaps the icon to `fa-spinner fa-spin` and disables the
  button.
- `showStatus("Querying the legal knowledge base…", "info")`.
- Builds the fetch body from `getPayload()`; if a prior review paused the agent
  (`_reviewState.thread_id`), it threads `thread_id` into the body so the resume flow
  continues the same conversation.
- `fetch("/api/rag/query/agent", {method:"POST", headers, body: JSON.stringify(body)})`
  — CSRF token is attached automatically by `base.html`'s global fetch interceptor.
- `.then` → `{ok, status, data}` — branches on HTTP status (see §6).

---

## 4. Deconstruction of the query into pertinent legal questions

This step is **server-side** (rule-based, no LLM/API key needed). The client only
hands the raw string over; the backend classifies and parses it before retrieval.

**File:** `app/rag/retrieval/query_classifier.py`

**Step 4.1 — Classify the query type** (`QueryClassifier.classify(query) → QueryType`)
`QueryType` is a `StrEnum`:

- `SECTION_LOOKUP` — "What does Section 55 say?" / "u/s 33"
- `CASE_LAW` — "2023 SCC 123" / "Supreme Court" / "v. …"
- `PROVISION_SEARCH` — act/regulation references ("FSS Act", "sub-regulation")
- `AMENDMENT_QUERY` — "amend", "repeal", "inserted" (checked first, before sections)
- `GENERAL_QA` — fallback

Patterns are ordered by priority (amendment → section → case law → provision → general)
and reuse regex idioms from `app/cross_reference/engine.py` (`KNOWN_SECTIONS`,
`_SECTION_RUN_RE`) and `app/metadata_extractor/`.

**Step 4.2 — Parse structured filters** (`QueryParser.parse(query, query_type) → dict`)
Dispatches to a sub-parser based on the classified type:

- `SectionQueryParser` — extracts `section_number`, `subsection`, `section_numbers`
  (runs like "Sections 55, 56 and 58"). Regex: `_SECTION_NUMBER_RE`, `_SECTION_RUN_RE`,
  `_SUBSECTION_RE`.
- `AuthorityQueryParser` — extracts `authority` from `_KNOWN_AUTHORITIES`
  (FSSAI, Ministry of Health, etc.) with a fuzzy ministry fallback.
- `CaseLawQueryParser` — extracts `citation` ("2023 SCC 123") and `court`
  (Supreme/High Court).
- `JurisdictionQueryParser` — extracts `jurisdiction` + `level` from a 30-state Indian
  states set, or "central/India/federal".

**Step 4.3 — Merge with caller filters** (`app/rag/tasks.py`, `run_retrieval_pipeline`)

```python
merged_filters = {**(parsed or {}), **(filters or {})}
```

The parsed `section_number`/`authority`/etc. become Qdrant payload filters passed
into the hybrid retriever, scoping retrieval to the legally-relevant subset.

**Step 4.4 — (Optional, feature-flagged) legal query typing**
`app/rag/retrieval/legal_query_classifier.py::classify_legal_query(query)` produces a
finer-grained legal query type used for query-type-aware reranking
(`RAG_LEGAL_QUERY_TYPING`). Different legal query families are weighted differently
during reranking (e.g. prohibition regresses with hierarchy boosting; authority needs
more cross-encoder head coverage).

---

## 5. Gathering chunks — retrieval pipeline on the server

**File:** `app/rag/tasks.py::run_retrieval_pipeline` (plain entry point — no Celery needed)

**Step 5.1 — Classify + parse** (§4 above)
`classifier = QueryClassifier()` → `query_type`; `QueryParser().parse(...)` → `parsed`.
Result is merged with any caller filters and stamped to `RAGQueryLog.pipeline`
(`"legacy"` / `"agent"`) for the rollout A/B comparison.

**Step 5.2 — Build the retrievers**

- `DenseRetriever(collection_name=...)` — Qdrant vector search (768-dim, cosine).
  Uses `RAG_EMBED_ENDPOINT` (remote) or local `all-mpnet-base-v2`; degrades to
  `RAG_EMBED_REMOTE_FALLBACK` if the remote is unreachable. On Render free, the
  fallback flag must be `false` (torch OOMs 512MB).
- `SparseRetriever(store=QdrantStore(collection_name=...), server_bm25=RAG_QDRANT_BM25)`
  — Qdrant-side BM25 (no local fastembed at query time) or the in-memory rapidfuzz
  fallback for dense-only collections. **The collection must be passed through**
  (multi-domain fix, 2026-08-14) so env/commercial/animal domains don't bleed into the
  FSSAI pool.
- `Reranker` — `app/rag/retrieval/reranker.py`; built via `_build_reranker()`. Honours
  `RAG_ENSEMBLE_RERANK` (sec_act legal features + optional cross-encoder), and
  `RAG_RERANKER_MODEL` (a fine-tuned legal CE, e.g.
  `sumanksaha/Foodmultidomain`, is a drop-in). Degrades to pure sec_act ranking, then
  to the plain reranker when no CE is available.
- Optional **identifier arm** (`RAG_IDENTIFIER_ROUTE`) — builds a lexical
  `"{Act} section {N}"` query from section references in the question and runs it as a
  parallel additive arm through the hybrid retriever (measured +13.3pp candidate
  ceiling; the section-stamp backfill pushed the pool to 100%).

**Step 5.3 — Hybrid retrieval** (`HybridRetriever.retrieve(...)`)
`app/rag/retrieval/hybrid_retriever.py`:

- Runs dense + sparse, then **RRF fusion** (reciprocal rank, k=60).
- Optionally adds the identifier-arm results, the KG-contract provisions
  (`RAG_KG_FUSION`, §-to-graph retrieval), or KG chunk-expansion
  (`RAG_KG_EXPANSION`, chunk-to-graph) — the two KG paths are **alternatives** and
  never both run (re-fusing the already-fused list muddies ordering).
- Returns a `SearchResult` with `chunks: list[RetrievedChunk]`, `total`, `latency_ms`,
  `query_type`, `source` ("hybrid"/"dense"/"reranked").

**Step 5.4 — Log the retrieval** (`RetrievalLogger`)
`app/rag/retrieval/logger.py` persists a `RAGQueryLog` row (DB) with the hash-chained
audit trail, query type, chunk count, and latency. Returns `log_entry.id`
(`log_id`) threaded back to the client in the response so the frontend can correlate
follow-ups.

**Step 5.5 — (Optional, feature-flagged) evidence + cross-ref layer**

- `legal_identities` (`RAG_LEGAL_IDENTITY`) — `parse_legal_identity(c)` stamps each
  chunk with a stable legal identity.
- `expanded_candidates` (`RAG_REFERENCE_EXPANSION`) — graph-based cross-reference
  candidate expansion from the retrieved chunks.
- `evidence_set` (`RAG_EVIDENCE_SELECTOR`) — `select_evidence_set(query, chunks,
max_size=5, min_size=2)` → `EvidenceSet.to_dict()`.

---

## 6. Reconstruction of answer + output — the frontend renderer

**File:** `app/static/js/rag_query.js::renderResponse(data)`

This is the client-side "reconstruction" step — it turns the serialized
`RAGResponse` dict (§5, below) into the DOM answer card. The server-side generation
step is `run_generation_pipeline` (§7).

**Step 6.1 — Reset state**
`hideStatus()`, `hideReview()`, then `#ragResults` is rebuilt from scratch.

**Step 6.2 — Answer meta-gauges** (`.rag-answer-meta`)
Reads from the response dict:

- `groundedness_score` (0–1) — formatted to 3 decimals.
- `confidence` (0–1).
- `total_latency_ms` (ms).
- `llm_model` (only rendered if present).

**Step 6.3 — Answer text** (`.rag-answer-text`)
`data.answer` — the LLM's grounded answer. Falls back to "(no answer generated)".

**Step 6.4 — Hallucination warning** (conditional)
If `data.hallucination_detected` is true, renders a ⚠ banner listing
`data.hallucinated_claims` (the `HallucinationReport` claims). This is the
Phase-3 verification verdict surfaced to the user.

**Step 6.5 — Citations** (`.rag-citations`)
Iterates `data.citations` (a list of `Citation.to_dict()` / `asdict` entries):
label = `document_title §section_number` (or just `document_title`), snippet truncated
to 200 chars, `conf: <confidence>`.

**Step 6.6 — Agent block** (conditional)
If `data.pipeline === "agent"` and `data.agent` exists, renders a footer:
"Pipeline: agent | retries: <retry_count>" and the `expanded_query` if present.

**Step 6.7 — Retrieved context** (`.rag-chunks`)
Iterates `data.retrieved_chunks` (`RetrievedChunk.to_dict()`): each chunk renders its
`document_title §section_number / act_name`, a `score:` badge, and a 300-char
truncated `text`.

**Step 6.8 — Error path** (`renderError(message)`)
Stamps the answer card with a red border + red text when the request fails (non-202,
non-200), so the user sees _why_ (validation error, 503 RAG-disabled, network error).

---

## 7. Server-side answer reconstruction — generation → verification

**File:** `app/rag/tasks.py::run_generation_pipeline` (the entry the JS posts to)

**Step 7.1 — Run retrieval** (§5)
If the caller did not pass pre-retrieved `chunks`, calls `run_retrieval_pipeline(query,
top_k, collection_name, filters, pipeline)` to obtain them. Reuses the query_type and
classifier output.

**Step 7.2 — KG graph expansion** (optional, behind `RAG_KG_EXPANSION`)
`KGContextExpander().expand_chunks(chunk_ids)` walks the Neo4j legal KG
(`app/knowledge_graph/`, `kg/hybrid.py`) and expands retrieved chunk IDs into
provisions/domains/status/authorities/provenance. Provisions are RRF-fused into the
context slot budget (`ContextBuilder.max_context_chunks`). Best-effort — never raises.

**Step 7.3 — Generate grounded answer** (`GroundedGenerationService.generate`)
`app/rag/generation/grounded_service.py`:

- `ContextBuilder` formats the chunks into `[Source 0]`, `[Source 1]` … blocks and
  builds the system + user prompt (`PromptTemplate` registry).
- `GroundedLLMClient` posts to OpenRouter/OpenAI via `httpx` (`OPENROUTER_API_KEY` in
  prod; `RAG_USE_STUB_LLM=true` in dev returns a deterministic stub so no API is
  needed).
- `CitationTracker` annotates the LLM response with the `[n]` bracket citations.
- `ResponseSanitizer` validates every cited chunk still exists and is grounded.
- `TokenCounter` tallies prompt/completion/total tokens.

**Step 7.4 — Verify** (`app/rag/verification/`)

- `ClaimExtractor` splits the answer into atomic claims.
- `EvidenceVerifier` (rapidfuzz + §5.1 field match) checks each claim against chunks.
- `CitationValidator` — chunk_id + section_number consistency.
- `GroundednessScorer` — weighted blend → `groundedness_score`.
- `HallucinationDetector` → `hallucination_detected` + `hallucinated_claims`.
- Results are written to the same hash-chained `AuditLog` (`app/models/inspection.py`).

**Step 7.5 — Serialize the contract** (`RAGResponse` → `dict`)
`app/rag/retrieval/result.py::RAGResponse` (dataclass). `run_generation_pipeline`
returns a dict with this exact shape:

```
query, query_type, answer, citations[], retrieved_chunks[],
groundedness_score, hallucination_detected, hallucinated_claims[],
confidence, retrieval_latency_ms, generation_latency_ms, total_latency_ms,
prompt_tokens, completion_tokens, llm_model, token_usage{prompt,completion,total},
debug{}, kg_expansion?, kg_contract?, pipeline
```

### Step 6 ↔ Step 7 data contract

| Frontend reads (renderResponse)                        | Backend emits (run_generation_pipeline)                        |
| ------------------------------------------------------ | -------------------------------------------------------------- |
| `data.answer`                                          | `answer`                                                       |
| `data.citations[]` → label, snippet, confidence        | `citations[]` (`Citation.to_dict`)                             |
| `data.retrieved_chunks[]` → title, §, score, text      | `retrieved_chunks[]` (`RetrievedChunk.to_dict`)                |
| `data.groundedness_score`                              | `groundedness_score`                                           |
| `data.hallucination_detected` + `.hallucinated_claims` | verification verdicts                                          |
| `data.total_latency_ms`, `data.llm_model`              | timing + model                                                 |
| `data.pipeline === "agent"` → agent block              | `pipeline` + `agent{retry_count, expanded_query, audit_trail}` |

---

## 8. HTTP lifecycle — route handlers

**File:** `app/rag/routes.py` (the blueprint now lives in `app/rag/agent/routes.py`
for the agent sub-routes; the legacy query route remains here.)

**Step 8.1 — GET the UI** (`GET /api/rag/`)
`query_ui()` renders `rag/query.html`, passing `domains` (from
`app/rag/collections.py::DOMAIN_COLLECTIONS` — env/commercial/animal/wb_state/criminal

- the default `fssai_legal_768`) and `default_collection`. Returns **404** when
  `RAG_ENABLED=false` (so the nav link disappears in RAG-less environments).

**Step 8.2 — POST the query** (`POST /api/rag/query/agent`)
`app/rag/agent/routes.py::query_agent()`:

- 400 if payload is not a dict / `query` missing / `top_k` invalid.
- 503 if `RAG_ENABLED=false`.
- If `RAG_USE_AGENT_PIPELINE` is false → delegates to the legacy pipeline (the
  documented `POST /api/rag/query` flow) and stamps `pipeline: "legacy"`.
- If true → runs the LangGraph `StateGraph` (classify → retrieve → generate → verify →
  conditional expand-and-retry on `groundedness < 0.7`, max 2 retries).
- `RAG_AGENT_HITL` true → at the `review` interrupt, returns **202**
  `awaiting_review` with `{thread_id, review}`.

**Step 8.3 — Resume after review** (`POST /api/rag/query/agent/resume`)
`query_agent_resume()` — validates `{thread_id, approved}`; approved → finalize;
rejected → expand-and-retry. 200 + final `RAGResponse` or another 202. Requires a
checkpointer (`RAG_AGENT_CHECKPOINTER` = `memory` | `postgres`).

---

## 9. Tests — verifying each step

All live under `tests/` and run offline (stub LLM, in-memory Qdrant test mode; no
API key needed):

| Step                  | Test file                                                                                                                   | Tests  | What it covers                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------ |
| §4 classify + parse   | `test_query_classifier.py`                                                                                                  | ~27+12 | Every `QueryType` + each parser's section/authority/case-law/jurisdiction extraction |
| §5 retrieval          | `test_dense_retriever.py` (14), `test_sparse_retriever.py` (20), `test_hybrid_retriever.py` (16), `test_reranker.py` (8)    | 58     | Dense/sparse/hybrid search, RRF fusion, rerank, score thresholds, filters            |
| §5 logging            | `test_retrieval_logger.py` (8), `test_query_log_model.py` (11)                                                              | 19     | Persistence, hash chain, token/latency tracking                                      |
| §6 frontend rendering | `test_rag_routes.py`                                                                                                        | 15     | Route 200/400/503, flag-off delegation, collection forwarding                        |
| §7 generation         | `test_rag_generation.py`                                                                                                    | 40     | ContextBuilder, prompt templates, stub LLM, citations, sanitization                  |
| §7 verification       | `test_hallucination_detector.py` (28), `test_citation_validator.py` (6), `test_token_counter.py` (10)                       | 44     | Claim extraction, grounding, citation consistency, token tally                       |
| §8 agent flow         | `test_rag_agent_routes.py` (7), `test_rag_agent_graph.py` (12), `test_rag_agent_nodes.py` (17), `test_rag_agent_m5.py` (15) | 51     | 202 review, resume approve/reject, retry loop, checkpointers                         |
| end-to-end            | `test_rag_e2e.py` (9), `test_rag_e2e_verification.py` (6), `test_resilient_pipeline.py` (10)                                | 25     | Full query→retrieve→generate→verify, circuit breaker + fallback                      |

**Run them:** `python -m pytest tests/test_rag_routes.py tests/test_rag_agent_routes.py tests/test_query_classifier.py tests/test_rag_e2e.py -v`

---

## 10. What is genuinely still TODO

Everything in §1–§9 is **implemented and tested**. The only items the codebase does
_not_ yet do — and these were never part of this frontend's scope — are the advanced
argumentation-layer features (documented in `RAG_AUDIT_REPORT.md §5.2`): Pydantic
structured LLM output, IRAC decomposition, counterargument handling, persistent claim
ledger, and abstention. These are larger enhancements, not steps in _this_ plan; file
a separate ticket if wanted.

**Quick smoke check** after any frontend change:

```js
// In the browser console on /api/rag/ :
fetch("/api/rag/query/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query: "What does Section 55 of the FSS Act say?", top_k: 5 }),
})
    .then((r) => r.json())
    .then(console.log);
// Should return an RAGResponse with answer + §55 citations + groundedness_score.
```

---

**Implementation order this plan recommends (for reference, not because it's unbuilt):**

1. §1 blueprint + route (gated by `RAG_ENABLED`) — `app/rag/__init__.py` + `app/__init__.py`
2. §2 HTML template — `app/rag/templates/rag/query.html`
3. §3 JS accept/payload/validate — `app/static/js/rag_query.js`
4. §4 deconstruction — `app/rag/retrieval/query_classifier.py`
5. §5 gather chunks — `app/rag/tasks.py::run_retrieval_pipeline`
6. §7 generation+verification — `app/rag/generation/` + `app/rag/verification/`
7. §6 reconstruction — `renderResponse()` in `rag_query.js`
8. §8 routes — `app/rag/routes.py` + `app/rag/agent/routes.py`
9. §9 tests

> The plan above documents the already-shipped implementation. The real "scout"
> finding is that `Legal_AI_implementation.md`'s four bullets are satisfied today by
> the existing `query.html` + `rag_query.js` + `query_classifier.py` +
> `run_generation_pipeline` stack — elaborated here so the wiring is legible end to end.

---

## 11. Render Free-Tier Deployment — Risk Analysis & Blockers

> **Context:** The app is deployed on Render's free tier (512 MB RAM, shared CPU,
> zero persistent disk, auto-sleep after 15 min idle). This section documents every
> concrete risk and the required fix for each.

### 11.1 Soundness Verdict

The implementation plan is **accurate and internally consistent**. Every claim cross-checks
against real code:

| Claim in plan                            | Verified in codebase                                    |
| ---------------------------------------- | ------------------------------------------------------- |
| Blueprint gated by `RAG_ENABLED`         | `app/rag/__init__.py`, `app/__init__.py` L210, L586–588 |
| Query classified server-side (no LLM)    | `app/rag/retrieval/query_classifier.py`                 |
| RRF fusion in hybrid retrieval           | `app/rag/retrieval/hybrid_retriever.py`                 |
| Circuit breaker + degraded fallback      | `app/rag/resilient.py`                                  |
| Torch thread cap (`RAG_TORCH_THREADS`)   | `app/rag/torch_runtime.py`                              |
| LangGraph agent with HITL + resume       | `app/rag/agent/routes.py`, `app/rag/agent/graph.py`     |
| Hash-chained audit log                   | `app/models/inspection.py`                              |
| 200 + offline tests, no API key required | `tests/` directory                                      |

---

### 11.2 🔴 CRITICAL — Memory Overcommit (OOM on First Query)

The dependency stack far exceeds the 512 MB free-tier limit:

| Component                                          | Approx. RAM |
| -------------------------------------------------- | ----------- |
| `sentence-transformers/all-mpnet-base-v2` (loaded) | ~420 MB     |
| Flask + SQLAlchemy + gunicorn worker               | ~80–120 MB  |
| `fastembed` BM25 sparse model                      | ~80 MB      |
| EasyOCR + OpenCV (if imported at startup)          | ~150–300 MB |
| LangGraph + `langgraph-checkpoint-postgres`        | ~40 MB      |

**The local embedding model MUST NOT load on Render free.** The plan notes the risk at
§5.2 (`"torch OOMs 512MB"`) but leaves the fix to `RAG_EMBED_REMOTE_FALLBACK=false`,
which is insufficient — the model is still attempted first.

**Required fixes:**

1. Set `RAG_EMBED_ENDPOINT` in `render.yaml` pointing to a remote embedding API
   (Hugging Face Inference API free tier, or OpenRouter embeddings).
2. Set `RAG_EMBED_REMOTE_FALLBACK=false` to prevent any local torch fallback from loading.
3. Audit every `import easyocr`, `import cv2`, `import pdf2image` for module-level
   side effects — ensure they are lazy (inside functions, not at module top-level).
4. Add a startup RAM probe in `create_app()` (gated by `RAG_ENABLED`) that logs
   `psutil.Process().memory_info().rss` and emits a warning when it exceeds 400 MB,
   so the OOM is visible in Render logs before a user hits it.

**TODO:** `render.yaml` — add:

```yaml
- key: RAG_EMBED_ENDPOINT
  sync: false # point to HF Inference API or remote embed service
- key: RAG_EMBED_REMOTE_FALLBACK
  value: "false" # NEVER fall back to local torch model
- key: RAG_TORCH_THREADS
  value: "1" # minimum threads if torch ever loads (e.g. reranker)
```

---

### 11.3 🔴 CRITICAL — Celery Worker Requires a Paid Plan

`render.yaml` declares a `type: worker` service (`food-adjudication-celery-worker`).
On the Render **free plan**:

- Only **one active service** is allowed (the web service). A second service requires
  a paid Starter plan (~$7/mo).
- Redis (required by Celery) is a paid Render add-on. The free tier has no Redis.

`app/rag/tasks.py` already degrades gracefully when Celery is absent (`celery = None`).
The QStash dependency (`qstash>=3.4.0` in `pyproject.toml`) provides a serverless,
webhook-based alternative that needs no resident worker.

**Required fixes:**

1. Remove (or comment out) the `food-adjudication-celery-worker` service block in
   `render.yaml`, with a note that it requires a paid plan.
2. Verify `REDIS_URL` being absent (`sync: false`) does not crash the web service at
   boot — trace every `celery.conf` access path.
3. Route async tasks through QStash webhooks for free-tier deployments.

**TODO:** `render.yaml` — comment out or remove:

```yaml
# Requires paid Render plan (worker type) + paid Redis add-on.
# On free tier, use QStash webhook-based dispatch instead.
# - type: worker
#   name: food-adjudication-celery-worker
#   ...
```

---

### 11.4 🟠 HIGH — Cold-Start Latency (40–60 s on First Request)

Free-tier services sleep after 15 min idle. On wake, the first request pays:

- ~30 s Render container cold-start
- ~5–10 s lazy sentence-transformer load (even from remote, the HTTP client initialises)
- ~2–8 s first LLM call

Users see a spinner with `"Querying the legal knowledge base…"` and no indication they
are waiting for a container wake.

**Required fixes:**

1. **Keep-alive ping** — use UptimeRobot (free) or a GitHub Actions cron to `GET
/api/rag/health` every 14 minutes. This prevents sleep entirely at zero cost.
2. **JS cold-start UX** — in `rag_query.js::submitQuery()`, after 5 s without a
   response, replace the info banner with:
   `"Service is waking up — this may take up to 30 seconds on first use…"`.
3. **Eager model warm-up** — call `EmbeddingService._get_encoder()` inside
   `create_app()` (gated by `RAG_ENABLED and not RAG_USE_STUB_LLM`) so the cold-start
   cost appears in Render boot logs rather than silently stalling the first user query.

---

### 11.5 🟠 HIGH — LangGraph Postgres Checkpointer Pool Exhaustion

When `RAG_AGENT_CHECKPOINTER=postgres`, `langgraph-checkpoint-postgres` opens its own
connection pool against the same Render free-tier Postgres that has a **97-connection
hard cap** shared with SQLAlchemy's pool.

Under even moderate load (gunicorn worker + LangGraph resume), the connection limit
is hit and queries fail with `too many connections`.

**Required fix:**

Default to `memory` checkpointer on free tier. Add to `render.yaml`:

```yaml
- key: RAG_AGENT_CHECKPOINTER
  value: memory # use postgres only on paid plan with pgBouncer
```

Add a guard in `app/rag/agent/graph.py` — if `RAG_AGENT_CHECKPOINTER=postgres` but
`SQLALCHEMY_POOL_SIZE + LANGGRAPH_POOL_SIZE > 80`, log a `WARNING` at app start.

---

### 11.6 🟡 MEDIUM — PostgreSQL Free Tier 90-Day Expiry

Render free-tier Postgres **expires after 90 days** — the entire database is deleted
with no warning email. `RAGQueryLog`, `AuditLog`, and all app data are lost.

**Required fixes:**

1. Document the 90-day limit prominently in `README.md` and `render.yaml` comments.
2. Set conservative SQLAlchemy pool size: `pool_size=3, max_overflow=2`.
3. Add a periodic cleanup task (QStash cron or a scheduled `/api/admin/cleanup` route)
   that prunes `RAGQueryLog` rows older than 30 days to keep storage under the 1 GB cap.

---

### 11.7 🟡 MEDIUM — Gunicorn Missing Timeout and Worker Config

Current `startCommand`:

```
gunicorn --bind 0.0.0.0:10000 app:app
```

Problems:

- **No `--timeout`** — gunicorn default is 30 s. LLM generation regularly takes 3–8 s
  plus retries, and the full agent graph (2 retries × generation + verification) can
  exceed 30 s. Workers are silently killed mid-request.
- **No worker type** — default `sync` workers block on I/O (Qdrant + LLM HTTP calls).
  `gthread` workers overlap I/O waits and double effective throughput on free-tier CPU.

**Required fix** — update `startCommand` in `render.yaml`:

```
FLASK_APP=app:create_app flask db upgrade && \
gunicorn -w 1 -k gthread --threads 4 --timeout 120 --bind 0.0.0.0:10000 app:app
```

(`-w 1` keeps RAM under 512 MB; `--threads 4` handles concurrent I/O; `--timeout 120`
covers the longest reasonable agent run including 2 retries.)

---

## 12. Usability Improvements — TODO

### 12.1 No Query-Level Caching

Every identical query hits Qdrant + the remote LLM API. For a legal system where queries
like `"What does Section 55 of the FSS Act say?"` are repeated by multiple officers:

**TODO (`app/rag/tasks.py`):**

```python
from cachetools import TTLCache
_response_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)  # 1-hour TTL

def run_generation_pipeline(query, top_k=10, collection_name=None, ...):
    cache_key = (query.strip().lower(), collection_name or "", top_k)
    if cache_key in _response_cache:
        logger.info("Cache hit for query=%r", query)
        return _response_cache[cache_key]
    result = _run_generation_pipeline_impl(...)
    _response_cache[cache_key] = result
    return result
```

> **Evaluation (scouted):** ⚠️ Two corrections to the proposal above.
>
> 1. `cachetools` is **NOT** currently a dependency — `grep` of `pyproject.toml` and
>    `requirements.txt` returns nothing. It must be **added** (`cachetools>=5.0`). The
>    free-tier `@lru_cache` quick-win cited in `AGENTS.md` is `functools.lru_cache` (stdlib,
>    no TTL, unbounded growth — unsuitable for a response cache).
> 2. Caching `run_generation_pipeline` caches **live LLM output**. That is safe for the
>    deterministic stub/legacy path, but the **agent pipeline must skip the cache** (it
>    expands the query on retry, so an identical query can yield a different answer).
>    Cache key must include `pipeline` and the cache must be bypassed when `pipeline ==
"agent"`. Prefer the **deterministic** target `run_retrieval_pipeline` (line 34 of
>    `app/rag/tasks.py` — Qdrant + classification, no LLM) for the highest-ROI, lowest-risk
>    hit.
>
> ```python
> from cachetools import TTLCache  # add cachetools>=5.0 to pyproject.toml
> _retrieval_cache: TTLCache = TTLCache(maxsize=512, ttl=600)
> _generation_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)  # legacy/stub only
> ```
>
> On the paid tier, back the generation cache with `flask-caching` + Redis (the retrieval
> cache is in-process regardless — it is the same on every gunicorn thread).

---

### 12.2 Raw Error Messages Shown to Legal Officers

`renderError()` in `rag_query.js` surfaces raw server strings such as
`"RAG agent query failed: ConnectionRefusedError..."`. Legal officers are not developers.

> **Evaluation (scouted):** ✅ The proposal is **correct and implementable unchanged.**
> `renderError(message)` at `app/static/js/rag_query.js:154` currently takes only `message`
> — no status code. Both call sites (`submitQuery` line 227 and `resumeReview` line 157)
> sit inside a `.then(out => …)` handler where `out.status` is already in scope, so
> threading `out.status` through requires **no new plumbing** — just pass the int. The
> 503/400/500 map covers every failure mode the RAG routes emit (RAG disabled → 503;
> bad payload → 400; exceptions → 500).

**TODO (`app/static/js/rag_query.js` — `renderError()`):**

```js
const USER_FRIENDLY_ERRORS = {
    503: "The legal knowledge base is currently offline. Please try again in a few minutes.",
    400: "Your question could not be processed. Please check the wording and try again.",
    500: "An unexpected error occurred. Please try again shortly.",
};

function renderError(message, statusCode) {
    console.error("[RAG]", message); // raw detail for devs
    const friendly = USER_FRIENDLY_ERRORS[statusCode] || message;
    // ... render friendly in #ragResults
}
```

---

### 12.3 HITL Review Panel Has No Timeout

The 202 `awaiting_review` flow pauses indefinitely. If the reviewer never clicks
Approve/Reject, the LangGraph memory-checkpointer accumulates stale threads and the
frontend hangs silently.

**TODO (`app/rag/agent/graph.py`):**

> **Evaluation (scouted):** ⚠️ The proposal is **technically incorrect** — fix before coding.
> LangGraph's `interrupt()` is called inside `review_node` at `app/rag/agent/graph.py:88`.
> `interrupt()` **pauses the graph indefinitely** with no timeout; the suspended node
> cannot self-resume, so `route_after_review` only runs _after_ an external `resume`.
> A `review_timeout_seconds` value living in `review_node` cannot fire a timeout by itself.
>
> **Corrected implementation** (enforce the timeout _externally_, then feed it back in):
>
> 1. Stamp the review start into the state so a clock/watchdog can read it:
>
>     ```python
>     # app/rag/agent/graph.py — review_node, before interrupt()
>     from time import time
>     return {"review_started_at": time(), "review_timeout_s": 300}
>     ```
>
> 2. Client-side (least effort): in `rag_query.js`, start a 300 s countdown on the 202;
>    on expiry auto-POST `/api/rag/query/agent/resume` `{approved: true}` and flip the
>    banner to "Auto-approved after timeout — click Resend to retry."
> 3. Server-side fallback (robust to a closed tab): a QStash recurring watchdog calls
>    `resume_agent(thread_id, approved=True)` once `review_started_at + 300 < now`,
>    reusing the `TASK_REGISTRY` / Redis-status pattern in `app/utils/qstash_client.py`.
>
> **Free-tier caveat:** `RAG_AGENT_CHECKPOINTER=memory` (the default) lives in-process and
> is wiped when the Render free container sleeps — so a paused HITL thread already dies
> on wake (a crude implicit timeout). The durable watchdog above **requires** paid tier
> (`RAG_AGENT_CHECKPOINTER=postgres`).

---

### 12.4 No Progress Feedback for Long Queries

The frontend issues one synchronous `fetch()`. The 3–10 s end-to-end wait (retrieve
→ generate → verify) is invisible to the user — only a spinner shows.

**TODO (medium effort):** Implement a two-step flow:

1. `POST /api/rag/query/agent` returns `{job_id}` immediately (HTTP 202).
2. Frontend polls `GET /api/rag/status/<job_id>` every second, receiving
   `{phase: "classifying"|"retrieving"|"generating"|"verifying"|"done", partial_answer}`.
3. When `phase === "done"`, call `GET /api/rag/result/<job_id>` for the full
   `RAGResponse`.

> **Evaluation (scouted):** ⚠️ Feasible, but two conditions the proposal understates.
>
> 1. `run_generation_pipeline` is **not** in `TASK_REGISTRY` (`app/utils/qstash_client.py`)
>    — it must be added there. Today `/api/rag/query/agent` calls `run_agent`
>    _synchronously_ and returns the full `RAGResponse` immediately, so there is no
>    `job_id` to poll yet.
> 2. The Redis status store (`qstash:task:{message_id}`, 24 h TTL) and the `/tasks/run`
>    webhook (registered `app/__init__.py:476`) are real and reusable — **but only with
>    Redis present.** On Render free (no Redis) `qstash_client.py` falls back to
>    synchronous `.apply()` per its docstring, which _collapses_ the two-step flow into a
>    blocking call and defeats progress polling.
>
> So: implement the two-step flow, gate the async branch on `REDIS_URL` being set, and
> fall back to the current synchronous behaviour otherwise. Concretely:
>
> ```python
> # app/utils/qstash_client.py — TASK_REGISTRY
> "run_generation_pipeline": ("app.rag.tasks", "run_generation_pipeline_task"),
> # new routes in app/rag/agent/routes.py
> GET /api/rag/status/<job_id>   # {phase, partial_answer, status}
> GET /api/rag/result/<job_id>   # full RAGResponse (200) / 404 if expired
> ```
>
> Each phase of the graph (`classify`, `retrieve`, `generate`, `verify`, `finalize`)
> writes a `{phase, partial_answer}` snapshot to `Redis.setex(TASK_STATUS_KEY, ...)`
> so the polling route has something to report.

---

### 12.5 Top-K Default Too High for Free Tier

`top_k=10` (default) retrieves 10 dense + 10 sparse chunks, reranks them, and feeds
them all into the LLM context window. On a 512 MB host this is CPU and memory
intensive for the reranker, and raises LLM API costs.

**TODO:** Set `RAG_DEFAULT_TOP_K=5` in `render.yaml` and read it in the route:

```python
default_top_k = current_app.config.get("RAG_DEFAULT_TOP_K", 10)
top_k = payload.get("top_k", default_top_k)
```

UI tooltip (HTML): `"Higher values improve coverage but increase response time."`

---

### 12.6 No Rate Limiting on the RAG Endpoint

`POST /api/rag/query/agent` has no per-user rate limit. A single session can exhaust
the LLM API budget or spike Render CPU in a burst.

**TODO (`app/rag/agent/routes.py`):**

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# In create_app():
limiter = Limiter(get_remote_address, app=app,
                  default_limits=["200/day", "50/hour"],
                  storage_uri="memory://")   # swap for Redis URI on paid tier

# On the route:
@rag_bp.route("/query/agent", methods=["POST"])
@limiter.limit("10 per minute")
def query_agent(): ...
```

Add `flask-limiter>=3.0.0` to `pyproject.toml` dependencies.

---

### 12.7 Chunk List Has No Pagination

`renderResponse()` renders all `top_k` retrieved chunks inline. With `top_k=50` this
produces an extremely long page.

**TODO (`app/static/js/rag_query.js` — `renderResponse()`):**

- Render only the top 3 chunks by default.
- Add a `<button id="ragShowAllChunks">Show all N chunks ▾</button>` that expands
  the rest on click.
- Add a **"Copy answer"** button (clipboard API) next to the answer card header.

---

## 13. Scale-Up Path (Beyond Free Tier)

When upgrading to a paid Render plan or migrating to another host, apply in this order:

| Priority | Change                                               | Why                                       |
| -------- | ---------------------------------------------------- | ----------------------------------------- |
| 1        | Enable Celery worker + Redis                         | Unblocks async ingestion + batch jobs     |
| 2        | Switch `RAG_AGENT_CHECKPOINTER=postgres` + pgBouncer | Durable HITL resume across restarts       |
| 3        | Dedicated embedding sidecar (Modal / HF endpoint)    | All gunicorn workers share one model load |
| 4        | `gunicorn -w 2 -k gthread --threads 4`               | Doubles throughput without RAM doubling   |
| 5        | `flask-caching` + Redis backend                      | Shared cache across workers               |
| 6        | Enable `RAG_USE_AGENT_PIPELINE=true` + HITL          | Full LangGraph agent for all queries      |

The embedding sidecar is the highest-leverage change: `RAG_EMBED_ENDPOINT` is already
the hook in `app/rag/embedding_service.py` — just point it at a Modal or HF endpoint.

---

## 14. Immediate `render.yaml` Diff

Apply this to unblock free-tier deployment:

```diff
 services:
     - type: web
       name: food-adjudication-portal
-      startCommand: FLASK_APP=app:create_app flask db upgrade && gunicorn --bind 0.0.0.0:10000 app:app
+      startCommand: FLASK_APP=app:create_app flask db upgrade && gunicorn -w 1 -k gthread --threads 4 --timeout 120 --bind 0.0.0.0:10000 app:app
       envVars:
+          - key: RAG_EMBED_ENDPOINT
+            sync: false          # point to HF Inference API or OpenRouter embeddings
+          - key: RAG_EMBED_REMOTE_FALLBACK
+            value: "false"       # NEVER fall back to local torch (OOMs 512MB)
+          - key: RAG_TORCH_THREADS
+            value: "1"           # minimum if torch loads for reranker
+          - key: RAG_AGENT_CHECKPOINTER
+            value: memory        # postgres checkpointer needs pgBouncer on free tier
+          - key: RAG_DEFAULT_TOP_K
+            value: "5"           # reduce memory + LLM context cost on free tier
           - key: RAG_QDRANT_URL
             sync: false
           ...

-    - type: worker
-      name: food-adjudication-celery-worker
-      ...
+    # Celery worker requires a paid Render plan + paid Redis add-on.
+    # On free tier, use QStash webhook-based task dispatch instead.
+    # Re-enable this block when upgrading to a paid plan.
+    # - type: worker
+    #   name: food-adjudication-celery-worker
+    #   ...

 databases:
     - name: nsa-webservice-db
       plan: free
+      # WARNING: Render free-tier Postgres expires after 90 days — all data is deleted.
+      # Upgrade to the "starter" plan before going to production.
```
