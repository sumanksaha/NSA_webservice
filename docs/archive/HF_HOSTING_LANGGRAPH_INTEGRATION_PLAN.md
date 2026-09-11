# HF Model Hosting + LangGraph Integration Plan

**Date:** 2026-08-16
**Status:** ✅ Approved with decisions locked (2026-08-16)
**Supersedes / extends:** `docs/LANGGRAPH_IMPLEMENTATION_EVALUATION.md` (LangGraph part unchanged in spirit; this adds the HF hosting layer and re-orders the work)

### Locked decisions

- **Hosting (revised 2026-08-16):** Docker Space on free cpu-basic now **requires a PRO subscription** (HF policy change — create-repo returned 402). Primary path is now the **HF Serverless Inference API** (`mode="serverless"`, free tier, no Space/Endpoint) with TEI as the paid/self-hosted production alternative. See §3.2.
- **LangGraph scope:** Full M3+M4 in this pass — agent graph **plus** the conditional groundedness retry loop.
- **Scope correction:** only the cross-encoder goes remote. The sec_act feature half of `EnsembleReranker` stays local (deterministic, reads chunk payload metadata, no model to serve).
- **M2 implementation** (remote CE client + fallback chain + wiring + tests) lands first as the foundation both pipelines share. Implemented with both backends: `mode="tei"` and `mode="serverless"`.

### Status

- **M0 done (2026-08-16):** `legal_ce_v2_K500` pushed to `sumanksaha/Foodmultidomain` (public) with library-generated `modules.json`; Hub-reload parity verified (scores identical). `scripts/push_ce_models.py`.
- **M1+M2 done (2026-08-16) — via Modal, not HF Serverless:** the HF Serverless Inference API for the fine-tuned CE turned out to be **decommissioned** (404), and a Docker Space needs PRO — so the remote CE is served by **Modal** (`modal_deploy/app.py`, `POST https://sumanksaha--rerank.modal.run/rerank`), deployed and live-verified (score −0.821 matches the local checkpoint exactly). Dense embeddings also moved to Modal (`/embed`, `all-mpnet-base-v2`, same 768-dim — no re-index needed) and BM25 is computed **inside Qdrant** (`RAG_QDRANT_BM25=true`, `Qdrant/bm25`). Render now runs **zero local models**. See `task.md` **ENV-10** for the full deployment + the 8 Render env vars.
- **M3+M4 done (2026-08-16):** LangGraph agent package implemented — `app/rag/agent/` (state/nodes/graph/routes), `POST /api/rag/query/agent`, `RAG_USE_AGENT_PIPELINE` flag (default false → delegates to the legacy pipeline), conditional groundedness retry loop (< 0.7, max 2 retries) with query expansion reusing `GroundedLLMClient`. 41 new tests green; `langgraph>=1.0.0` added to `pyproject.toml` (imported lazily — the legacy path never touches it).
- **M5 done (2026-08-16):** checkpointing + human-in-the-loop implemented. `build_graph(hitl=True)` inserts a `review` node that pauses via `interrupt()` before finalize; `POST /api/rag/query/agent` returns **202 awaiting_review** (thread_id + review payload), `POST /api/rag/query/agent/resume` takes `{thread_id, approved}` — approved → finalize, rejected → expand-and-retry. Checkpointers: `RAG_AGENT_CHECKPOINTER=memory` (default, `MemorySaver` singleton — dev/tests) or `postgres` (`PostgresSaver` against `DATABASE_URL`, requires `langgraph-checkpoint-postgres>=3.0` + `psycopg-binary`, pinned in `pyproject.toml`). `RAG_AGENT_HITL` flag (default false). 15 new M5 tests (`tests/test_rag_agent_m5.py`).

---

## 0. Goal

1. **Host the fine-tuned cross-encoders** (`evaluation/out/models/legal_ce_v1`, `legal_ce_v2_K500`) on Hugging Face and serve reranking **via API** instead of loading torch locally.
2. **Integrate the whole RAG concept via LangGraph** — a stateful, conditional agent pipeline on top of the existing services.

This doc evaluates the plan, corrects a few technical assumptions, and gives the best implementation path.

---

## 1. Evaluation — what the plan gets right, what needs correcting

### 1.1 Terminology correction ("Hugging Spaces")

"Spaces" is HF's *app* hosting (Gradio/Streamlit/Docker). For serving a cross-encoder model by API the idiomatic combo is:

| Piece | HF product | Role |
|---|---|---|
| Model storage | **Hub** (model repo) | Push `legal_ce_v1` / `legal_ce_v2_K500` here |
| Inference server | **TEI** (text-embeddings-inference) | Natively serves cross-encoders with `POST /rerank` |
| Deployment | **Docker Space** (CPU, free tier) or **Inference Endpoint** (paid, auto-scale, token auth) | Runs TEI against the Hub model |

The plan should say "Hub + TEI", not "Spaces" — but the user's Spaces instinct is fine: a Docker Space is the zero-cost way to run TEI.

### 1.2 What actually goes remote (scope correction)

The user said "my reranker and CE". Two distinct pieces:

- **CE (cross-encoder)** — a real model (90 MB `BertForSequenceClassification`, MiniLM-L-6-v2 backbone). **This is what gets hosted.**
- **The reranker** — in this codebase the production reranker is `EnsembleReranker` (sec_act features + CE head). **Only the CE half can go remote.** The sec_act half is deterministic Python that reads chunk payload metadata (`section_number`, `act_name`, `document_title`, `hierarchy_level`); it has no model, costs ~0ms, and is the strongest single reranker measured (R@10 0.474). Sending it remote would mean shipping chunk metadata over HTTP for zero benefit.

**Decision: keep sec_act local, host only the CE, call it via API.**

### 1.3 The good news — the integration seam already exists

Both `Reranker` and `EnsembleReranker` accept an injected `encoder` with a `predict(pairs) -> list[float]` contract (tests already inject mock encoders with exactly this shape). A remote client is just **another encoder object**:

```python
reranker = EnsembleReranker(model_name=..., encoder=RemoteRerankClient(endpoint=...))
```

**Zero changes to reranker scoring logic.** The remote CE slots into the exact seam the test suite already exercises.

### 1.4 LangGraph evaluation (vs. `LANGGRAPH_IMPLEMENTATION_EVALUATION.md`)

The existing doc's recommendation (**Option A — gradual, feature-flagged, sync mode**) is correct. Adjustments:

1. **Order of work changes.** Host the CE on HF *first* (M0–M2, ~4 days, orthogonal, removes torch from the app runtime → smaller memory footprint on Render free 512 MB). LangGraph (M3+) is independent and can use the same remote CE via the encoder seam.
2. **Parallel retrieval is weak in sync mode.** LangGraph's `Send` fan-out is async-oriented. In a sync Flask request handler you'd need `asyncio.run` or a thread pool for true parallelism — and retrieval is already only ~300–800 ms sequentially. **Defer the parallel arms**; keep them sequential in v1 of the agent graph.
3. **Checkpointing has a 2026 security advisory.** A June 2026 LangGraph checkpointer RCE chain (reconstructs Python objects from state) was disclosed. If/when `PostgresSaver` lands, pin the latest patched `langgraph-checkpoint-postgres` and treat persisted states as untrusted. Defer to a later phase.
4. **Dependency weight matters on Render free.** `langgraph` pulls `langchain-core`. Mitigate by lazy-importing langgraph *only* inside `app/rag/agent/` so the default pipeline (and 2,203-test suite) never imports it.

---

## 2. Target architecture

```
POST /api/rag/query            (existing, unchanged)
└── run_retrieval_pipeline
    ├── classify ──► legal query type
    ├── dense + sparse + identifier ──► RRF fusion
    ├── EnsembleReranker
    │   ├── sec_act features        (local, deterministic)
    │   └── CE head ──► RemoteRerankClient ──► TEI /rerank (HF Space/Endpoint)
    │                        └─ fallback: local CrossEncoder → features-only
    └── log (RAGQueryLog)

POST /api/rag/query/agent      (new, RAG_USE_AGENT_PIPELINE=true)
└── LangGraph StateGraph (sync invoke, thin adapter nodes)
    classify → retrieve → rerank(remote CE) → [evidence_selector]
    → generate → verify → conditional edge (groundedness < 0.7 & retries < 2)
    → expand_query → loop → finalize → log → END
```

Both pipelines share the remote CE client. The agent graph is a thin orchestrator; all business logic stays in the existing services.

---

## 3. Part A — HF hosting (M0–M1, ~1.5 days)

### 3.1 Push the models to the Hub

Prep script (`scripts/push_ce_models.py`):

1. Load `evaluation/out/models/legal_ce_v2_K500` with `sentence_transformers.CrossEncoder`, add **`modules.json`** (sentence-transformers CrossEncoder format) + a `README.md` model card, and `model.push_to_hub("nsa-webservice/legal-ce-v2-k500")`.
   - **Do NOT push** `tokenized_cache.pt` (298 MB) and `train_state.pt` (272 MB) — training artifacts, not model weights.
2. Repeat for `legal_ce_v1` → `nsa-webservice/legal-ce-v1`.
3. Validate: `CrossEncoder("nsa-webservice/legal-ce-v2-k500")` loads from the Hub and scores a legal pair identically to the local dir (sanity: same float output on 3 pairs).

### 3.2 Serve — Serverless Inference API (free, chosen) vs TEI (production)

**Option 1 — HF Serverless Inference API (chosen for POC, free, zero-ops):**

Cross-encoders are served as ``text-classification`` — one POST per pair with
``query [SEP] text``.  No Space or Endpoint needed; works for any public
model repo.  Downsides: no batching (one request per pair), cold starts on
the first call (~10–30 s), and free-tier rate limits.  Verify with
``scripts/test_hf_inference.py`` (checks serverless scores match the local
checkpoint).

```bash
HF_TOKEN=hf_xxx python scripts/test_hf_inference.py --repo sumanksaha/Foodmultidomain
```

App config:

```
RAG_RERANKER_ENDPOINT=https://api-inference.huggingface.co/models/sumanksaha/Foodmultidomain
RAG_RERANKER_MODE=serverless
RAG_RERANKER_TOKEN=<hf token>
```

**Option 2 — TEI Docker Space (blocked on free tier):** as of 2026-08-16,
hosting Docker Spaces on free cpu-basic requires a PRO subscription (create
returned 402 Payment Required).  Dockerfile (needs PRO or a paid Space tier):

```dockerfile
FROM ghcr.io/huggingface/text-embeddings-inference:cpu-latest
CMD ["--model-id", "sumanksaha/Foodmultidomain", "--port", "7860"]
```

**Option 3 — Inference Endpoint (production):** same TEI image, native
Bearer-token auth, auto-scaling, always-warm, one batched ``/rerank`` POST
per head.  Paid per hour.

**Smoke test (TEI mode):**

```bash
curl -X POST https://<space-or-endpoint>/rerank \
  -H "Authorization: Bearer $HF_TOKEN" \
  -d '{"query": "penalty for selling substandard food", "texts": ["Section 50: penalty", "Chapter 2: definitions"]}'
# → [{"index": 0, "score": 4.2}, {"index": 1, "score": -1.1}]
```

---

## 4. Part B — Remote CE client in the app (M2, ~2 days)

### 4.1 New module: `app/rag/retrieval/remote_reranker.py`

```python
class RemoteRerankClient:
    """Encoder-seam-compatible client for a TEI /rerank endpoint.

    Implements the same ``predict(pairs) -> list[float]`` contract as the
    local CrossEncoder so it can be injected into Reranker/EnsembleReranker.
    One POST per head (query is constant across pairs).
    """
    def __init__(self, endpoint: str, token: str | None = None,
                 timeout: float = 5.0) -> None: ...
    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        # POST {endpoint}/rerank {"query": q, "texts": [t...]}
        # → scores in pair order; raises RuntimeError on transport/HTTP error
```

- Use `httpx` (already a dependency for the LLM client) with a short timeout (2–5 s).
- One request per head batch (TEI takes a single query + N texts).
- Raise on failure — the reranker already catches and falls back.

### 4.2 Config & wiring

Env vars (also mirrored in `app/__init__.py` + `.env.example`):

| Var | Default | Purpose |
|---|---|---|
| `RAG_RERANKER_ENDPOINT` | *(empty)* | TEI `/rerank` base URL; empty ⇒ local CE as today |
| `RAG_RERANKER_TOKEN` | *(empty)* | Bearer token for Inference Endpoints / private Spaces |
| `RAG_RERANKER_TIMEOUT` | `5` | Per-request timeout (s) |
| `RAG_RERANKER_REMOTE_FALLBACK` | `true` | Fall back to local CE when remote fails |

`_build_reranker()` in `app/rag/tasks.py`:

```python
if _reranker_endpoint():
    client = RemoteRerankClient(endpoint, token=_reranker_token(), timeout=_reranker_timeout())
    return EnsembleReranker(model_name=model_name, encoder=client, ce_head=..., ce_weight=...)
return EnsembleReranker(model_name=model_name, ce_head=..., ce_weight=...)   # unchanged
```

Fallback chain (matches the existing graceful-degradation pattern):
`remote CE → local CE (if torch installed) → sec_act features only`.

### 4.3 Latency & cost controls

- **One POST per query** (all head pairs batched), not per-pair.
- **Dynamic CE skipping still works** — when sec_act is decisive (exact sec+act match on the whole head), `EnsembleReranker` skips the CE entirely ⇒ **zero remote calls**, zero latency.
- Query-type configs already bound the head (30 default, 40 for authority/cross-reference). Remote RTT adds ~50–200 ms to the head call — acceptable.
- Optional later: 5-minute per-query result cache keyed by `(query, head-chunk-ids)`.

### 4.4 Security

- Inference Endpoints: token auth built in. Docker Space: add a `SECRET_TOKEN` gate in a small FastAPI wrapper **if** the Space is public — otherwise anyone can burn your CPU quota. (Free CPU Spaces have strict limits; a public unauthenticated Space is a DoS risk to your own quota.)

---

## 5. Part C — LangGraph agent pipeline (M3, ~1 week)

### 5.1 Dependencies (lazy)

- `langgraph>=0.2` (pulls `langchain-core`). Pin exact versions in `pyproject.toml`.
- Import **only** inside `app/rag/agent/*` — the default pipeline never touches it.

### 5.2 New package `app/rag/agent/`

| File | Contents |
|---|---|
| `state.py` | `RAGState` TypedDict — `query`, `query_type`, `chunks: list[RetrievedChunk]`, `retry_count`, `audit_trail`, `groundedness`, `response`, `log_id` |
| `nodes.py` | Thin adapters over existing services: `classify_node`, `retrieve_node` (calls `run_retrieval_pipeline` — **which already uses the remote CE client**), `generate_node`, `verify_node`, `expand_query_node` (reuses `GroundedLLMClient`), `evidence_node` (wraps `select_evidence_set` behind `ENABLE_EVIDENCE_SELECTOR`) |
| `graph.py` | `StateGraph`: classify → retrieve → generate → verify → **conditional** (groundedness < 0.7 and retry_count < 2 → expand_query → retrieve loop; else finalize) → END. `compile()`d once at import, `.invoke(state)` in the route |
| `routes.py` | `POST /api/rag/query/agent` on `rag_bp`; 503 when RAG disabled; delegates to existing `query()` when `RAG_USE_AGENT_PIPELINE` is false |

Config: `RAG_USE_AGENT_PIPELINE` (default `false`). Existing `/api/rag/query` unchanged.

### 5.3 Graph design

```
classify ──► retrieve ──► generate ──► verify ──► finalize ──► END
                  ▲                            │
                  └──── expand_query ◄─────────┘   (groundedness < 0.7, retries < 2)
```

- All nodes are synchronous (aligns with Flask + Celery; no FastAPI migration).
- **No parallel `Send` in v1** — async-oriented; defer (see §1.4).
- Evidence selector and KG fusion stay feature-flagged nodes, exactly as today.

### 5.4 Phases after M3 (from the existing LangGraph doc)

- **M4 (~2 days):** conditional retry loop + query expansion (this is the highest-value LangGraph capability; fold into M3 if time allows).
- **M5 (defer):** checkpointing (`MemorySaver` dev / `PostgresSaver` prod) + human-in-the-loop `interrupt()` — pin latest patched `langgraph-checkpoint-postgres` (2026 checkpointer advisory).

---

## 6. Test plan

| Test file | ~Tests | Covers |
|---|---|---|
| `tests/test_remote_reranker.py` | 10 | `predict` contract, one-POST batching, auth header, timeout/transport error → raises, score parsing, endpoint from config |
| `tests/test_reranker_fallback.py` | 5 | remote down → local CE; remote + local down → features-only (existing mock-encoder pattern) |
| `tests/test_rag_agent_state.py` | 5 | RAGState schema, init, serialization |
| `tests/test_rag_agent_nodes.py` | 12 | adapter nodes wrap services, error handling |
| `tests/test_rag_agent_graph.py` | 8 | compile, state flow, conditional routing, retry loop (stub LLM) |
| `tests/test_rag_agent_routes.py` | 6 | `/api/rag/query/agent` 200/400/422/503, flag delegation |

All stub-LLM, no network (mock `RemoteRerankClient` with the same injected-encoder pattern). Existing **2,203 tests stay green** — the default path is untouched.

---

## 7. Milestones & risk

| Milestone | Effort | Risk | Deliverable | Status |
|---|---|---|---|---|
| M0 — Hub push (v1+v2, cleaned) | 0.5 d | Low | `sumanksaha/Foodmultidomain` loads from Hub | ✅ done (2026-08-16) |
| M1 — CE hosting + smoke test | 1 d | Low | `/rerank` returns scores for legal pairs | ✅ done — **Modal** (`sumanksaha--rerank.modal.run`), live-verified |
| M2 — RemoteRerankClient + wiring + fallback + tests | 2 d | Low | CE hosted, app still works with endpoint down | ✅ done — `remote_reranker.py` + `RAG_RERANKER_ENDPOINT` |
| M3 — LangGraph agent package + flag + tests | 1 wk | Med | `/api/rag/query/agent`, opt-in, suite green | ✅ done (2026-08-16) — 41 new tests |
| M4 — conditional retry loop + query expansion | 2 d | Med | Self-correcting pipeline (highest-value LangGraph feature) | ✅ done (folded into M3) |
| M5 — checkpointing + HITL | 2 d | Med | Resume/interrupt (pin patched checkpointer) | ✅ done (2026-08-16) — 15 new tests |

**Actual:** M0–M5 complete (2026-08-16). Remote hosting went to **Modal** (not HF Serverless, which is decommissioned for the fine-tuned CE, and not a Docker Space, which now needs PRO) — see §0 status and `task.md` ENV-10.

---

## 8. Rollout

1. M0–M4 land behind config only — no behavior change until `RAG_RERANKER_ENDPOINT` is set / `RAG_USE_AGENT_PIPELINE=true`.
2. A/B: add `reranker_source` (`local`/`remote`) and `pipeline` (`legacy`/`agent`) fields to `RAGQueryLog`; compare R@10 / groundedness / latency on the frozen 150-question benchmark before flipping either flag in prod.
3. Flip `RAG_RERANKER_ENDPOINT` first (done — live on Modal). Flip `RAG_USE_AGENT_PIPELINE` only after the agent path matches legacy quality on the benchmark.
