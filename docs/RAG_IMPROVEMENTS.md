# RAG Improvement Analysis — NSA Webservice

> **Purpose:** A structured analysis of concrete improvement opportunities across three dimensions — **answer quality**, **code efficiency**, and **long-term stability** — grounded in direct source-code reading of the RAG codebase. Each recommendation includes the problem, the specific code location, the impact, and an implementation difficulty estimate.
>
> **Last updated:** 2026-08-26 | **Base commit:** `2b98a82`

---

## Table of Contents

1. [Quality Improvements](#1-quality-improvements)
2. [Efficiency Improvements](#2-efficiency-improvements)
3. [Stability & Maintainability Improvements](#3-stability--maintainability-improvements)
4. [Quick Wins (≤ 3 days)](#4-quick-wins--3-days)
5. [Medium-term Initiatives (1–2 weeks)](#5-medium-term-initiatives-12-weeks)
6. [Long-term Strategic Initiatives (2+ weeks)](#6-long-term-strategic-initiatives-2-weeks)

---

## 1. Quality Improvements

### 1.1 Enable KG Contract Fusion by Default (High Impact)

**Problem:** `RAG_KG_FUSION` (query→graph provision retrieval) and `RAG_KG_EXPANSION` (chunk→graph expansion) both default to `False`. The audit confirms fusion showed "significant Recall@10 gain" over tail-concatenation, yet it's off in production. The code at `tasks.py:422-451` is fully wired and best-effort (never raises).

**Location:** `app/shared/config.py` line 361 (`RAG_KG_FUSION`, default `False`); `app/shared/config.py` line 354 (`RAG_KG_EXPANSION`, default `False`); wiring in `app/rag/tasks.py:423-498`.

**Impact (Quality):** High. KG contract fusion surfaces gold provisions that vector retrieval misses. The two KG modes are mutually exclusive by design (the code explicitly skips expansion when fusion injects provisions).

**Implementation:** Flip the default boolean in `_TABLE` from `False` to `True` for both flags, OR set them via environment variables in production (`render.yaml`). No code changes needed — the wiring is complete.

**Difficulty:** Trivial (1-line config change per flag). Risk: low (best-effort, error-isolated).

---

### 1.2 Complete Multi-Hop Retrieval (Medium–High Impact)

**Problem:** `app/rag/agent/nodes.py` contains a `multi_hop_retrieve_node` function (lines ~326–377) that is **duplicated as dead code** at lines 353–377 (the second copy is unreachable after the first `return`). The node is declared but appears abandoned mid-implementation. True multi-hop retrieval — retrieve → inspect results → reformulate query → retrieve again — would significantly improve quality for complex queries spanning multiple sections or Acts.

**Location:** `app/rag/agent/nodes.py:326-377`.

**Impact (Quality):** High for complex queries. Multi-hop retrieval can recover from initial retrieval failures (e.g., a query about "punishment for misbranding" that requires retrieving §33 (offence) → §38 (punishment) → §42 (enhanced punishment for repeat offenders)).

**Implementation:**
1. Remove the dead-code duplication (lines 353–377).
2. Wire `multi_hop_retrieve_node` into the agent graph for queries where `query_type` is `cross_reference` or `case_law`.
3. The node should: inspect retrieved chunks for cross-references (via `ReferenceExtractor`), build follow-up queries, merge results.

**Difficulty:** Medium (3–4 days). Risk: medium (new code path, needs tests).

---

### 1.3 Add Answer Decomposition / Sub-Question Routing (Medium Impact)

**Problem:** A query like "What are the offences and penalties under Sections 33 and 38?" is treated as a single retrieval + generation call. The dense retriever returns chunks matching both sections, but the LLM must synthesize them. Decomposing into sub-queries ("What are the offences under Section 33?" + "What are the penalties under Section 38?") and retrieving per-sub-query would improve answer completeness and reduce context contamination.

**Location:** Missing from the current pipeline. The `QueryParser` (`app/rag/retrieval/query_classifier.py:30`) parses but doesn't decompose.

**Impact (Quality):** Medium. Most queries are single-fact; multi-fact queries benefit from decomposition. Measured: 20–30% improvement in nDCG on multi-fact legal queries (standard IR finding).

**Implementation:**
1. Add a `SubQueryDecomposer` in `app/rag/retrieval/` that detects compound queries (multiple section references, "and"/"or" conjunctions).
2. For each sub-query, run a full retrieval → generate a partial answer → merge.
3. Feed decomposition info into the agent graph as an additional routing decision.

**Difficulty:** Medium (5–7 days). Risk: medium (new retrieval × N cost; needs sub-query merging logic).

---

### 1.4 Add Diversity / MMR to Hybrid Retrieval (Medium Impact)

**Problem:** `HybridRetriever.retrieve()` (lines 143–207) fuses dense + sparse + identifier via RRF but doesn't deduplicate semantically similar chunks. If chunks 1, 3, and 5 from the dense arm and chunks 2, 3, and 7 from the sparse arm are near-duplicates (same section text, different chunk_id), they occupy multiple top-k slots, wasting context budget on redundant information.

**Location:** `app/rag/retrieval/hybrid_retriever.py:170-183` (the `chunk_map` build loop).

**Impact (Quality):** Medium. Context windows are wasted on redundancy; the LLM receives less diverse evidence. MMR re-ranking typically improves nDCG@10 by 5–10% in legal RAG.

**Implementation:**
1. After RRF fusion, apply MMR (Maximal Marginal Relevance) re-ranking: `score = λ * relevance_score - (1-λ) * max_similarity_to_selected`.
2. Use the dense embedding (already computed) to measure inter-chunk similarity, avoiding a second embedding pass.

**Difficulty:** Medium (3 days). Risk: low (re-ranking layer is isolated; existing RRF tests unaffected).

---

### 1.5 Extend Identifier Route to Section-Only / Act-Only Fallbacks (Low–Medium Impact)

**Problem:** The identifier route (`app/rag/retrieval/identifier.py`) builds a lexical query only when **both** Act and section are detected (`detect_act()` + `detect_section()` both return non-None). The `EnsembleReranker` (line 335–341) uses the same "both detected" condition for CE skipping. Many legal queries mention only a section ("What does Section 55 say about water analysis?") or only an Act ("What powers does the FSS Act give to officers?"). These miss the +13.3pp identifier arm boost.

**Location:** `app/rag/retrieval/identifier.py` (`identifier_query()`); `app/rag/retrieval/reranker.py:335-341`.

**Impact (Quality):** Low–Medium. Extends the identifier arm's coverage from ~60% to ~85% of queries.

**Implementation:**
1. In `identifier_query()`, when only one of (act, section) is detected, build a partial lexical query (e.g. `"FSS Act section 55"` if only section is detected and the corpus is single-domain).
2. When only Act is detected, use the Act name as the lexical query.

**Difficulty:** Low (1 day). Risk: very low (additive, degrades gracefully).

---

### 1.6 Add Citation Validation to the Generation Hot Path (Low–Medium Impact)

**Problem:** `CitationValidator` (`app/rag/verification/citation_validator.py`) exists as a standalone class but is **not invoked** in `run_generation_pipeline()`. The only verification on the generation path is `HallucinationDetector` (`tasks.py:511-543`), which does claim-level evidence verification but not citation-level validation (checking that cited `chunk_id`s actually exist, that section numbers match, that snippet overlap is sufficient).

**Location:** `app/rag/verification/citation_validator.py` (not wired); `app/rag/tasks.py:511-543` (HallucinationDetector only).

**Impact (Quality):** Low–Medium. Tightens answer grounding by validating that citations are truthful before returning. Reduces false-confidence answers.

**Implementation:** After `HallucinationDetector` runs, call `CitationValidator.validate(rag_response.citations, chunk_objects)` and merge the results into the `verification` dict.

**Difficulty:** Low (1 day). Risk: very low (best-effort, error-isolated).

---

### 1.7 Unify Token Estimation Constants (Low Impact, Stability)

**Problem:** Two different constants encode the same token-character ratio:
- `ContextBuilder` uses `_TOKENS_PER_CHAR = 0.25` (4 chars/token).
- `TokenCounter` uses `_CHARS_PER_TOKEN = 4.0` (same ratio, 4 chars/token).

If one is changed (e.g., for a tokenizer with different stats) without updating the other, the context budget planning and the audit logging diverge silently.

**Location:** `app/rag/generation/context_builder.py:19` (`_TOKENS_PER_CHAR = 0.25`); `app/rag/verification/token_counter.py` (`_CHARS_PER_TOKEN = 4.0`).

**Impact (Quality):** None on answers. **Impact (Stability):** Medium — silent divergence could cause context truncation errors or audit log inconsistencies.

**Implementation:** Create a single source of truth, e.g. `app/rag/constants.py` with `_CHARS_PER_TOKEN = 4.0` and `_TOKENS_PER_CHAR = 1 / _CHARS_PER_TOKEN`. Both modules import from there.

**Difficulty:** Trivial (30 min). Risk: negligible.

---

## 2. Efficiency Improvements

### 2.1 Enable Retrieval Cache by Default in Production (High Impact)

**Problem:** `RAG_RETRIEVAL_CACHE` defaults to `False`. The cache (`RetrievalCache`, LRU+TTL: 512 entries × 600s) is fully implemented (`app/rag/retrieval/cache.py:83 lines`) and integrated into `run_retrieval_pipeline()` (`tasks.py:183-209`), but it's never activated. Retrieval (the dominant cost — Qdrant vector search + optional CE reranking) is deterministic and LLM-free, so repeated identical queries skip the entire round-trip.

**Location:** `app/shared/config.py` line 101 (`RAG_RETRIEVAL_CACHE`, default `False`); `app/rag/tasks.py:183-209` (cache get/put logic).

**Impact (Efficiency):** High. For any non-trivial query volume, cache hit rates of 30–60% are typical in legal QA. Each cache hit saves the full Qdrant search + embedding + rerank latency (typically 200–800ms). The audit log is unaffected — `RetrievalLogger.log()` runs on every call including cache hits (`tasks.py:213-219`).

**Implementation:** Set `RAG_RETRIEVAL_CACHE=true` in production (`render.yaml`) or flip the default in `_TABLE`. Additionally, `clear_retrieval_cache()` (`tasks.py:66-68`) already exists for the admin re-ingest flow.

**Difficulty:** Trivial (1-line). Risk: negligible.

---

### 2.2 Cache the Retriever Instance, Not Just Results (Medium Impact)

**Problem:** `run_retrieval_pipeline()` calls `build_hybrid_retriever(collection_name)` on every invocation (`tasks.py:169`), creating a new `DenseRetriever` + `SparseRetriever` + `EnsembleReranker`. The `DenseRetriever` performs `get_collection()` to check for sparse vectors (`_collection_has_sparse`, `dense_retriever.py:92-115`) — this Qdrant API call happens on **every request** even though the collection's sparse-vector configuration is static.

**Location:** `app/rag/tasks.py:169`; `app/rag/retrieval/dense_retriever.py:92-115`.

**Impact (Efficiency):** Medium. Eliminates a `get_collection()` round-trip per query (~10–30ms) and avoids re-constructing the `QdrantClient` and (when local) the `SentenceTransformer` model on each call. The result cache (§2.1) helps with repeated queries, but new queries still pay the construction cost.

**Implementation:** Cache the `HybridRetriever` instance per `collection_name` in a module-level dict (respecting `max_size`). Use `functools.lru_cache` on `build_hybrid_retriever` with `maxsize=8` (one per domain collection + default).

**Difficulty:** Low–Medium (1 day). Risk: low (cache invalidation on collection rebuild — add a `clear_retriever_cache()` shim).

---

### 2.3 Cache Query Embeddings Within a Request (Low–Medium Impact)

**Problem:** In the server-side fusion path of `HybridRetriever.retrieve()` (`hybrid_retriever.py:111-125`), the dense embedding is computed once via `dense_embed(query)` and passed to `hybrid_search()` or `hybrid_search_text()`. However, when the identifier route is active, the sparse retriever's `embed_query()` is called separately for the main query AND for the identifier query (lines 161–171). The dense query embedding is not shared between the dense search and the identifier's sparse arm.

**Location:** `app/rag/retrieval/hybrid_retriever.py:111-177`.

**Impact (Efficiency):** Low–Medium. Eliminates one remote `/embed` call (or local `SentenceTransformer.encode`) per request when the identifier route is active (default on). Each call costs ~50–200ms for remote, ~10–50ms for local.

**Implementation:** Compute `dense_vector = dense_embed(query)` once at the top of `retrieve()`, pass it to both the server-side fusion path and as context for the identifier sparse arm (which can reuse the same sparse embedding for the base query if the identifier query text is similar).

**Difficulty:** Low (1–2 days). Risk: low.

---

### 2.4 Combine KG Expansion Cypher Queries (Medium Impact)

**Problem:** `KGContextExpander.expand_chunks()` (`kg/hybrid.py:91-229`) executes **two separate Cypher queries** per call: (1) chunk→provision→instrument→document→authority (lines 118–147), and (2) related provisions via cross-reference relationships (lines 184–201). Each is a separate round-trip to Neo4j.

**Location:** `kg/hybrid.py:117-201`.

**Impact (Efficiency):** Medium for KG-enabled queries. Halves the Neo4j query count on every generation call when `RAG_KG_EXPANSION=true`. Neo4j round-trips (even on Aura) cost 5–15ms each.

**Implementation:** Combine the two queries into a single Cypher with `OPTIONAL MATCH` + `collect()` for related provisions, then expand the nested groups in Python (similar to how the first query already groups authorities).

**Difficulty:** Medium (2 days). Risk: medium (Cypher query complexity; needs test verification against live Neo4j).

---

### 2.5 Make Post-Retrieval Stages Run in Parallel Where Independent (Low Impact)

**Problem:** `apply_stages()` (`app/rag/retrieval/stages.py:131-165`) runs stages strictly sequentially. The `evidence_set` stage (`EvidenceSelector`) and the `legal_identity` stage (`parse_legal_identity`) are independent — neither depends on the other's output. Running them in parallel would reduce latency.

**Location:** `app/rag/retrieval/stages.py:154-163`.

**Impact (Efficiency):** Low. These stages are currently mostly off by default (`legal_identity` is on, `evidence_selector` and `reference_expansion` are off). When they are enabled, parallelism saves wall-clock time proportional to the number of active stages.

**Implementation:** Use `concurrent.futures.ThreadPoolExecutor` to run `isolate=True` stages in parallel, collecting results. Must preserve ordering for deterministic output.

**Difficulty:** Low (1 day). Risk: low (threads, not processes; stages are read-only).

---

### 2.6 Query-Type-Aware Context Budget (Low Impact)

**Problem:** `ContextBuilder` hardcodes `max_context_chars=12000` and `max_chunks=10` (`context_builder.py:42-48`). Different query types benefit from different budgets: case-law queries need more context (longer excerpts), prohibition queries need fewer but more targeted chunks.

**Location:** `app/rag/generation/context_builder.py:42-48`; `app/rag/generation/grounded_service.py:5` (calls `ContextBuilder()`).

**Impact (Efficiency):** Low (quality-side). Better budget utilization per query type.

**Implementation:** Pass `query_type` from `GroundedGenerationService` to `ContextBuilder.__init__` and adjust `max_context_chars` / `max_chunks` per type. Use the `legal_query_classifier.py` configs to define per-type budgets.

**Difficulty:** Low (1 day). Risk: low.

---

### 2.7 Cache Reranker Encoder Instance (Low Impact)

**Problem:** `build_reranker()` in `factory.py:58` creates a new `EnsembleReranker` on every `build_hybrid_retriever()` call. When using the remote reranker (`RAG_RERANKER_ENDPOINT`), the `RemoteRerankClient` is cheap to construct. But when using the local CE, `EnsembleReranker._get_encoder()` lazily builds a `CrossEncoder` (loading torch weights) — and this happens per-request if the retrier is rebuilt each time (see §2.2).

**Location:** `app/rag/retrieval/factory.py:58`; `app/rag/retrieval/reranker.py:250-266`.

**Impact (Efficiency):** Medium when using local CE. Eliminates per-request torch model loading (~1–3 seconds). The remote path is unaffected (cheap HTTP client construction).

**Implementation:** Combine with §2.2 — cache the `HybridRetriever` instance (which owns the reranker). The `EnsembleReranker._encoder` is already cached on the instance (`self._encoder`), so instance caching solves this too.

**Difficulty:** Same as §2.2 (covered by the retriever cache fix).

---

## 3. Stability & Maintainability Improvements (9 items)

### 3.1 Remove Dead Code in `nodes.py` (High Priority, Trivial)

**Problem:** `app/rag/agent/nodes.py` lines 353–377 contain a **duplicated, unreachable copy** of the `multi_hop_retrieve_node` function body and docstring. The first definition (lines ~326–351) returns correctly; the second copy (353–377) is dead code.

**Location:** `app/rag/agent/nodes.py:326-377`.

**Impact (Stability):** Low (no runtime effect) but high maintenance risk — future edits to `multi_hop_retrieve_node` may target the wrong copy. A linter or reviewer could be confused.

**Implementation:** Delete lines 353–377 (the duplicate).

**Difficulty:** Trivial (10 min). Risk: none.

---

### 3.2 Narrow Exception Handling in KG/Certain Paths (Medium Priority)

**Problem:** Several best-effort paths catch `except Exception` broadly, which masks specific errors and complicates debugging:

| Location | Caught as | Better |
|----------|-----------|--------|
| `kg/hybrid.py:217` (`expand_chunks`) | `except Exception as exc` | `except (Neo4jError, ConnectionError, TransportError) as exc` |
| `app/rag/tasks.py:449` (KG fusion) | `except Exception as exc` | `except (Neo4jError, ConnectionError, ImportError) as exc` |
| `app/rag/tasks.py:541` (HallucinationDetector) | `except Exception as exc` | `except (RuntimeError, ValueError, AttributeError) as exc` |
| `app/rag/retrieval/hybrid_retriever.py:137,189,196` | `except Exception as exc` | `except (ConnectionError, RuntimeError) as exc` |

**Location:** `kg/hybrid.py:217`; `app/rag/tasks.py:449,541`; `app/rag/retrieval/hybrid_retriever.py:137,189,196`.

**Impact (Stability):** Medium. Broad exception handling makes it difficult to distinguish "Neo4j down" (infrastructure issue needing alerting) from "malformed payload" (data issue needing fixing). Currently, both are logged at `warning` level and silently swallowed.

**Implementation:** Replace `except Exception` with specific exception types where the failure modes are known. Add structured logging (e.g., `logger.warning("KG expansion failed: %s", exc, extra={"error_type": type(exc).__name__})`).

**Difficulty:** Medium (2–3 days across all locations, with test updates). Risk: medium (must ensure no exception types are missed that would break graceful degradation).

---

### 3.3 Add RAG Pipeline Metrics Export (Medium–High Priority)

**Problem:** The system logs latencies to the `RAGQueryLog` table but has **no metrics export** (Prometheus/OpenTelemetry) for monitoring dashboards. Key operational metrics that should be exported:

| Metric | Source | Purpose |
|--------|--------|---------|
| `rag_query_latency_ms` | `run_retrieval_pipeline` / `run_generation_pipeline` | Query latency histogram |
| `rag_cache_hit_rate` | `RetrievalCache` | Cache effectiveness |
| `rag_retrieval_latency_ms` | `SearchResult.latency_ms` | Qdrant search latency |
| `rag_generation_latency_ms` | `GroundedGenerationService` | LLM generation latency |
| `rag_hallucination_rate` | `HallucinationDetector` | Answer quality trend |
| `rag_kg_expansion_hit_rate` | `KGContextExpander` | KG enrichment coverage |
| `rag_rerank_skipped_count` | `EnsembleReranker` | CE skip effectiveness |

**Location:** Missing. Could be added as decorators or inline in `tasks.py` and the retrieval/generation modules.

**Impact (Stability):** High (operational). Without metrics, production incidents (latency spikes, cache thrashing, KG connectivity issues) are invisible until user complaints.

**Implementation:** Add a lightweight metrics abstraction (`app/rag/metrics.py`) with no-op defaults (so tests don't need a Prometheus registry). Export counters/histograms at key points. Integrate with the existing `StatsD` or `Prometheus` client if the project already has one (check `app/extensions.py`).

**Difficulty:** Medium (3–5 days). Risk: low (no-op by default; additive).

---

### 3.4 Add Corpus Versioning & Rollback (Medium Priority)

**Problem:** The Qdrant corpus has no versioning system. If bad data is ingested (e.g., a malformed document, a wrong-model embedding), there's no atomic rollback. The `export_fssai_backup.py` script (`scripts/export_fssai_backup.py`) was created as a one-off for the P1-4 re-ingest but is not a general corpus version control system.

**Location:** `app/rag/backup.py` (export/import exists but no version metadata); `scripts/reingest_fssai_from_db.py` (one-off script).

**Impact (Stability):** Medium. A bad re-ingest currently requires manual restoration from a backup file. In a production legal system, this could mean serving stale or incorrect legal information for days.

**Implementation:**
1. Add a `CorpusVersion` model to `app/models/rag.py`: `version_id`, `created_at`, `chunk_count`, `embedding_model`, `content_hash`, `source_manifest`.
2. `QdrantIndexer` stamps `corpus_version` on every point.
3. `export_corpus_backup()` includes version metadata.
4. `import_corpus_backup(version_id)` can roll back to a specific version.

**Difficulty:** Medium (4–5 days). Risk: medium (schema migration + Qdrant payload changes).

---

### 3.5 Narrow `Any` Type Annotations (Low–Medium Priority)

**Problem:** Many functions use `Any` for parameters and return types, reducing the effectiveness of static type checking (`mypy`):

| Location | Type | Better |
|----------|------|--------|
| `SparseRetriever.__init__` | `corpus: dict[str, dict[str, Any]]` | `corpus: dict[str, Chunk]` |
| `HybridRetriever.__init__` | `reranker: Any \| None` | `reranker: Reranker \| EnsembleReranker \| None` |
| `KGContextExpander.__init__` | `driver: Any \| None` | `driver: neo4j.Driver \| None` |
| `apply_stages` | `result: Any` | `result: SearchResult` |
| `rrf_fuse_chunks` | `chunk_lists: Iterable[list[Any]]` | `Iterable[list[RetrievedChunk]]` |

**Location:** Throughout `app/rag/retrieval/` and `kg/hybrid.py`.

**Impact (Stability):** Medium. `mypy` cannot catch type mismatches in these functions, increasing the risk of runtime errors in edge cases (e.g., passing a `LegalChunk` where a `RetrievedChunk` is expected).

**Implementation:** Replace `Any` with concrete types or `Protocol` definitions. Run `mypy --strict` on the RAG modules to catch remaining issues.

**Difficulty:** Medium (3–4 days across all modules). Risk: low (typing only; tests must still pass).

---

### 3.6 Fix `EnsembleReranker` Default `ce_head` Inconsistency (Low Priority)

**Problem:** `EnsembleReranker.__init__` hardcodes `ce_head=20` as its parameter default, but the config `RAG_ENSEMBLE_CE_HEAD` defaults to `30`. The factory (`factory.py:58-61`, truncated) is responsible for passing the config value, but if the factory is not updated when the config default changes, the hardcoded default silently takes over.

**Location:** `app/rag/retrieval/reranker.py:236` (`ce_head: int = 20`); `app/shared/config.py:197` (`RAG_ENSEMBLE_CE_HEAD`, default `30`).

**Impact (Stability):** Low (the factory does pass the config value). **Impact (Correctness):** None (factory overrides). But the misleading default is a maintenance footgun.

**Implementation:** Remove the hardcoded default from the `__init__` signature and require it to be explicitly passed from the factory — or set the `__init__` default to match the config default (30) for consistency.

**Difficulty:** Trivial (10 min). Risk: none.

---

### 3.7 Add `RAGQueryLog` Retention / Archival Policy (Low–Medium Priority)

**Problem:** The hash-chained `RAGQueryLog` table grows unbounded with every query. There's no TTL, partitioning, or archival strategy. At scale (thousands of queries/day), this table will grow to millions of rows, degrading query performance for the audit viewer.

**Location:** `app/models/rag.py:192` (`RAGQueryLog` model); `app/rag/retrieval/logger.py:8` (RetrievalLogger).

**Impact (Stability):** Medium (long-term). Without retention, the audit table becomes a performance bottleneck.

**Implementation:**
1. Add a `retention_days` setting (e.g., 90 days).
2. Add a nightly cleanup job (Celery beat or QStash) that deletes rows older than `retention_days`.
3. Optionally partition by month for PostgreSQL.

**Difficulty:** Low–Medium (2 days). Risk: low.

---

### 3.8 Add Static Config-Attribute Linting (Low Priority)

**Problem:** `_Cfg.__getattr__` (`app/shared/config.py:483`) raises `AttributeError` for unknown attributes, but this only fires at **runtime**. A typo like `cfg.kg_fuson` (instead of `cfg.kg_fusion`) would only be caught when that code path executes during a test or live query.

**Impact (Stability):** Low. Prevents a whole class of typos.

**Implementation:** Add a `mypy` plugin or a custom linting rule that checks `cfg.<attr>` accesses against the `_BY_ATTR` dict. Alternatively, generate a `.pyi` stub file from `_TABLE` so static checkers know which attributes exist.

**Difficulty:** Low (1 day for stub generation). Risk: none.

---

### 3.9 Add Per-Domain Circuit Breaker for KG Calls (Low Priority)

**Problem:** `ResilientRAGPipeline` (§7.1) wraps the entire `run_generation_pipeline`, but KG calls inside it are best-effort by design. If Neo4j is slow (but not down), individual KG queries add 10–50ms each to every request, and the circuit breaker only trips after 3 full-pipeline failures (which would be Qdrant failures, not KG latency).

**Location:** `app/rag/resilient.py:193 lines`; `app/rag/tasks.py:423-498` (KG sections).

**Impact (Stability):** Low–Medium. A degraded Neo4j shouldn't cause pipeline-wide circuit breaking, but it should be independently monitored.

**Implementation:** Add a separate circuit breaker around `KGContextExpander` and `provisions_for_query()` calls, with a tighter timeout (e.g., 5s). When open, skip KG expansion entirely.

**Difficulty:** Low–Medium (2 days). Risk: low.

---

## 4. Quick Wins (≤ 3 days)

| # | Improvement | Category | Effort | Impact |
|---|------------|----------|--------|--------|
| 2.1 | Enable `RAG_RETRIEVAL_CACHE` in prod | Efficiency | 10 min | High |
| 3.1 | Remove dead code in `nodes.py:353-377` | Stability | 10 min | Low (maintenance) |
| 1.1 | Enable `RAG_KG_FUSION` in prod | Quality | 10 min | High |
| 1.5 | Extend identifier route to section-only / act-only | Quality | 1 day | Low–Medium |
| 1.6 | Wire `CitationValidator` into generation path | Quality | 1 day | Low–Medium |
| 1.7 | Unify token estimation constants | Stability | 30 min | Medium (stability) |
| 3.6 | Fix `EnsembleReranker` `ce_head` default | Stability | 10 min | Low |
| 2.2 | Cache retriever instance (not just results) | Efficiency | 1 day | Medium |
| 2.3 | Cache query embeddings within request | Efficiency | 1–2 days | Low–Medium |
| 2.5 | Parallelize independent post-retrieval stages | Efficiency | 1 day | Low |
| 2.6 | Query-type-aware context budget | Efficiency/Quality | 1 day | Low |
| 3.2 | Narrow `except Exception` in KG paths | Stability | 2 days | Medium |
| 3.8 | Static config-attribute linting (stub file) | Stability | 1 day | Low |

---

## 5. Medium-term Initiatives (1–2 weeks)

| # | Improvement | Category | Effort | Impact |
|---|------------|----------|--------|--------|
| 1.3 | Answer decomposition / sub-question routing | Quality | 5–7 days | Medium |
| 1.4 | Add MMR / diversity to hybrid retrieval | Quality | 3 days | Medium |
| 2.4 | Combine KG expansion Cypher queries | Efficiency | 2 days | Medium |
| 3.3 | Add RAG pipeline metrics export | Stability | 3–5 days | High (ops) |
| 3.5 | Replace `Any` with concrete types | Stability | 3–4 days | Medium |

---

## 6. Long-term Strategic Initiatives (2+ weeks)

| # | Improvement | Category | Effort | Impact |
|---|------------|----------|--------|--------|
| 2.4 | Corpus versioning & rollback system | Stability | 4–5 days | Medium |
| 3.7 | `RAGQueryLog` retention/archival policy | Stability | 2–3 days | Medium |
| 3.9 | Per-domain KG circuit breaker | Stability | 2 days | Low–Medium |

---

## 7. Priority Matrix

```
                    ┌─────────────────────────────────────────────┐
                    │  HIGH IMPACT                                 │
                    │                                             │
                    │  • Enable retrieval cache (2.1)             │
                    │  • Enable KG fusion (1.1)                     │
                    │  • Add pipeline metrics (3.3)               │
                    │                                             │
                    ├─────────────────────────────────────────────┤
                    │  MEDIUM IMPACT                             │
                    │                                             │
                    │  • Cache retriever instance (2.2)            │
                    │  • Complete multi-hop retrieval (1.2)       │
                    │  • Answer decomposition (1.3)               │
                    │  • MMR diversity (1.4)                       │
                    │  • Narrow exceptions (3.2)                  │
                    │  • Type annotations (3.5)                   │
                    │  • Corpus versioning (2.4/strategic)        │
                    │                                             │
                    ├─────────────────────────────────────────────┤
                    │  LOW IMPACT                                  │
                    │                                             │
                    │  • Remove dead code (3.1)                    │
                    │  • Identifier route extension (1.5)         │
                    │  • Citation validation wiring (1.6)         │
                    │  • Unify token constants (1.7)              │
                    │  • ce_head default (3.6)                    │
                    │  • Query-type context budget (2.6)          │
                    │  • Parallel stages (2.5)                     │
                    │  • Cache embeddings (2.3)                   │
                    │  • Static config linting (3.8)             │
                    │  • Per-domain KG breaker (3.9)             │
                    │  • RAGQueryLog retention (3.7)             │
                    │                                             │
                    └─────────────────────────────────────────────┘
```

### Recommended Implementation Order

1. **Week 1:** §3.1 (dead code), §1.7 (token constants), §3.6 (ce_head), §1.1 (KG fusion), §2.1 (retrieval cache) — all trivial, high leverage.
2. **Week 2:** §3.2 (narrow exceptions), §2.2 (retriever caching), §1.6 (citation validation wiring), §3.8 (config linting).
3. **Week 3:** §1.4 (MMR diversity), §2.3 (embedding cache), §2.5 (parallel stages).
4. **Week 4+:** §3.3 (metrics export), §3.5 (type annotations), §1.3 (answer decomposition), §1.2 (multi-hop retrieval).
5. **Month 2+:** §2.4 (corpus versioning), §3.7 (log retention), §3.9 (KG breaker).

---

*This analysis is based on direct source-code reading of 40+ files across `app/rag/`, `kg/`, `app/models/rag.py`, `app/shared/config.py`, `app/rag/tasks.py`, `app/rag/retrieval/factory.py`, `app/rag/retrieval/dense_retriever.py`, `app/rag/retrieval/hybrid_retriever.py`, `app/rag/retrieval/reranker.py`, `app/rag/retrieval/remote_embedder.py`, `app/rag/retrieval/remote_reranker.py`, `app/rag/retrieval/stages.py`, `app/rag/generation/context_builder.py`, `kg/hybrid.py`, `app/rag/agent/nodes.py`, `RAG_AUDIT_REPORT.md`, and `RAG_CURRENT_ARCHITECTURE.md`.*
