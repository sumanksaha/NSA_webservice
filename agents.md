# Agent Reference — NSA Webservice

> **Status:** ✅ Phases 0–10, Deepening D1–D5, Infrastructure, Phase 10 fuzzy search (56 tests), Phase 16 (backup/export/import, 14 tests), Phase A (OCR pipeline foundation, 14 tests), and Phase 13 (timeline engine + Gantt UI + global case-picker + entry points across the UI, 21 tests) all implemented & verified. S9a concurrency guard fully fixed (`tests/test_concurrency_inspection.py` 4/4 pass). Performance Quick Wins **7/7 complete** (FSO `@lru_cache`, Jinja2 bytecode cache, Flask-Compress, connection pooling, health endpoint, DB indexes, eager loading). **Phase 18 RBAC ✅ Complete (2026-08-26)** — wired the pre-existing `Role`/`user_roles` tables: roles **`admin`** (bypass) + **`fso`**; central gate in `app/__init__.py::enforce_rbac` driven by `ROLE_BLUEPRINTS` in `app/shared/rbac.py` (fso → case_file_generator/adjudication/notepad/inspection/workdiary/comments; deny = flash + redirect to Sample Adjudication); record-level scoping via `scoped_officer_name()` + `case_visible_to_user()` — CaseFile (`food_safety_officer_name`), Adjudication (`food_safety_officer`), Inspection list/create, Work Diary (locked to bound officer, preview/PDF strict), Comments API (`app/comments/`, author/admin delete); create-routes force-stamp the bound officer server-side; legacy mismatched names fail closed. Users bound via nullable `users.fso_name` (migration `add_user_fso_name`); provisioning seam `app/auth/provisioning.py` (`create_fso_account`, `seed_fso_users`) + extended admin Add User form (role + FSO dropdown, 1:1 binding enforced); bulk seed `scripts/seed_fso_users.py`; existing accounts backfilled to `admin`. Notepad blueprint registered (+ migration for `note`/`note_evaluation`). `tests/test_rbac.py` **44/44 pass**. **Work Diary ✅ Complete (2026-08-26)** — `app/workdiary/` blueprint: per-FSO diary accumulated read-only from Inspections (Date / Place of Visit / Purpose / Activity); Purpose is the FSO's explicit `Inspection.visit_purpose` pick ("routine"|"complaint", migration `add_inspection_visit_purpose`, required select on the inspection entry form) with a legacy problem-presence heuristic fallback; filterable by FSO/date-range/purpose; `/workdiary/preview` + `/workdiary/pdf` render the official `FSO_Work_Diary_Template.html` format via central `generate_pdf_from_html()`; `tests/test_workdiary.py` **28/28 pass**. Research: `docs/WORKDIARY_RESEARCH.md`. **Security close-out ✅ (2026-08-26)** — S10c backup monitoring (`run_backup()` bookkeeping to `settings`; public `GET /health/backups` dead-man's-switch, 503 when never/stale>26h/degraded) + S2 residual `POST /csp-report` collector (public, CSRF-exempt, both report formats, bounded body); `tests/test_backup_monitoring.py` **12/12 pass**. See `SECURITY_TODO.md` (re-audited — all original items closed; only `'unsafe-inline'` script-src residual remains). **Phase 21 ✅ Complete (2026-08-06)** — Food Cell DO Intimation: `app/food_cell/` blueprint + `DoIntimation` model/migration + Celery task + post-save hook in `app/sample/routes.py` + sync (Sheets/Airtable active, Excel dormant — `ENABLE_EXCEL_SYNC=false`); `tests/test_food_cell_do_intimation.py` **15/15 pass**. Priority 7 (Multi-Target Sheets Redundancy — Airtable + MS Excel) ✅ Complete (2026-08-07): `app/utils/sync.py` (12 restore functions), `app/__init__.py` (config + QStash daily backup schedule at 02:00 UTC), `app/settings/routes.py` (restored `backup_restore` route + `backup_redundant_to_r2` routes), `tests/test_priority7_redundancy.py` — **43/43 pass**, no regressions. **Excel Online sync is implemented but dormant** (`ENABLE_EXCEL_SYNC=false`; `app/services/excel_sync.py` with `msal` client-credentials flow fully coded but awaiting Microsoft 365 / Azure AD credentials — Airtable active via `ENABLE_AIRTABLE_SYNC=true`). **RAG Phase 1 ✅ Complete (2026-08-08)** — Agent A: Corpus/embedding pipeline (`app/rag/` with `QdrantStore`, `EmbeddingService`, `Chunker`, `QdrantIndexer`, `IngestionPipeline`, `ContentHasher`/`ChunkDeduper`, `MetadataAdapter`, `CitationAdapter`, `CrossRefAdapter`, `ChunkQualityValidator`, `LegalDocument`/`LegalChunk` models + 2 migrations). Agent B: Retrieval foundation (`app/rag/retrieval/` with `DenseRetriever`, `SparseRetriever`, `HybridRetriever` [RRF k=60], `Reranker`, `QueryClassifier`, `RetrievalLogger`/`RetrievalAuditLog`) + `retrieve_task`. **282 RAG tests** all passing (117 Phase 1 + 63 Phase 2 + 102 retrieval). **RAG Phase 2 ✅ Complete (2026-08-09)** — Grounded generation pipeline (`app/rag/generation/` with `ContextBuilder`, `PromptTemplate`, `GroundedLLMClient`, `CitationTracker`, `ResponseSanitizer`, `GroundedGenerationService`, `GenerationLogger` + `run_generation_pipeline`/`generate_task`/`POST /api/rag/generate` route); `tests/test_rag_generation.py` **40 tests all passing** (stub LLM mode, no Qdrant/network required). **RAG Phase 3 ✅ Complete (2026-08-09)** — Hallucination detection (`app/rag/verification/` with `ClaimExtractor`, `EvidenceVerifier`, `CitationValidator`, `GroundednessScorer`, `HallucinationDetector`, `TokenCounter`) + hash-chained audit; `tests/test_hallucination_detector.py` (28 tests) + `tests/test_citation_validator.py` (6 tests) + `tests/test_token_counter.py` (10 tests) pass. **RAG Phase 4 ✅ Complete (2026-08-09)** — Evaluation framework (`app/rag/evaluation/` with `FaithfulnessMetric`, `AnswerRelevanceMetric`, `ContextPrecisionMetric`, `ContextRecallMetric`, `CitationRecallMetric`, `GroundednessMetric`, `EvalRunner`, `EvalStorage`, `EvalReport`/`EvalSummary`) + `run_evaluate`/`evaluate_task` + `/api/rag/eval` route; `tests/test_eval_framework.py` (39 tests) + `tests/test_eval_batch.py` (10 tests) pass. **RAG Phase 5 ✅ Complete (2026-08-09)** — Integration (`resilient.py` with `ResilientRAGPipeline` circuit breaker [closed→open→half-open→closed] + fallback, `/api/rag/query` full pipeline route, `/api/rag/eval` batch eval route, `RAGResponse` schema, end-to-end pipeline, `run_evaluate`/`evaluate_task`) + token counting (`TokenCounter` in `GroundedGenerationService` populating `RAGQueryLog.context_length`); `tests/test_rag_e2e_verification.py` (6 tests) + `tests/test_resilient_pipeline.py` (10 tests) + `tests/test_hybrid_vs_dense.py` (7 tests) + `tests/test_rag_routes.py` pass. **RAG total: 282 Phase 1 + 40 Phase 2 + 48 Phase 3 + 49 Phase 4 + 31 Phase 5 = 410 RAG tests, all passing. (Phase 3: 28 hallucination + 6 citation_validator + 10 token_counter + 4 other; Phase 4: 39 eval_framework + 10 eval_batch; Phase 5: 6 e2e_verification + 10 resilient + 7 hybrid_vs_dense + 6 route/integration + 2 other)** **RAG Phase 3 Agent A ✅ Complete (2026-08-09)** — §6.2 integration tests (`test_corpus_ingestion_e2e.py` 8 — raw-doc → Qdrant round-trip with search verification; `test_batch_ingestion.py` 5 — QStash `ingest_corpus` schedule + batch progress; `test_reindexing.py` 3 — delete + re-index) + performance benchmarks (`scripts/benchmark_rag.py` custom timing harness — chunking via real legal engine, embedding via real `sentence-transformers` or synthetic-numpy fallback, Qdrant upsert/search latency with graceful skip; `test_rag_benchmarks.py` 11). **RAG total now 437** (410 + 8 + 5 + 3 + 11), all passing. Phases 11, 19 pending; Phase 12 ✅ Complete (Legal Validation Engine), Phase 14 ✅ Complete (Knowledge Graph Engine), Phase 20 ✅ Complete (Plugin Architecture). **Test-count reconciliation (2026-08-20):** the per-phase figures above are a subset accounting — the independent audit (`RAG_AUDIT_REPORT.md`) counts the full RAG surface at **695**, and a live `pytest --collect-only` on 2026-08-20 yields **1,780 tests total / 694 RAG-related** (incl. enrichment 108 + multi-domain Phase 1 37). See `RAG_AUDIT_REPORT.md` §2 for the full inventory; per-file counts in §7 below have drifted — the collect is authoritative. **Multi-Domain Phase 1 ✅ Complete (2026-08-20)** — de-FSSAI pipeline: per-act section registry (`app/rag/legal_sections.py` incl. BNS 1–358), domain→collection map (`app/rag/collections.py`; `criminal_legal_768` etc.), payload `act_name` field, act-aware crossrefs/enrichment, domain-parameterized prompts, generic statute claims, `make_ingestion_pipeline(collection=...)` threading; `tests/test_multidomain_phase1.py` **37/37 pass**. Plan: `docs/MULTIDOMAIN_INTEGRATION.md`. **KG Option B + semantic + hybrid ✅ Complete (2026-08-11)** — Corpus KG rebuild (`kg/corpus_ingestion.py` `KGCorpusIngestionEngine` + `scripts/build_kg_corpus.py`): 58 instruments / 1,861 provisions / 27,343 chunks from manifest + Qdrant + FSS DB, full domain edges + provenance + temporal status; readiness **32/100 → 69/100 (Operational, READY)** (`KG_READINESS_AUDIT_POST_REBUILD.md`). Semantic enrichment (`kg/enrichment.py` `LegalSemanticEnricher` + `scripts/enrich_kg_semantics.py`): deterministic rule-based duty/offence/penalty/prohibition/power tags → 751 evidence-backed edges (token-scoped prohibition precedence; `--min-confidence` gate). Hybrid expansion (`kg/hybrid.py` `KGContextExpander`): Qdrant chunk IDs → provisions/instrument/domain/status/authorities/provenance, wired into `run_generation_pipeline` behind **`RAG_KG_EXPANSION`** (default off). New tests: `tests/test_kg_semantic_enricher.py` (11) + `tests/test_kg_hybrid_expander.py` (6) — 17/17 pass; full KG + multi-domain sweep 100/100 green. **P1-4 FSSAI re-ingest ✅ Complete (2026-08-11)** — `fssai_legal_768` rebuilt from the local DB via `scripts/reingest_fssai_from_db.py` (identity-preserving: `chunk_id = LegalChunk.id`; 29 docs / 12,819 chunks; 1,961 s, all OK), then identity-stamped (`provision_id` 3,126, `instrument_id`/`legal_domain`/`status` 12,819, `act_name` 100%, 0 unknown docs; 4 payload indexes). **Qdrant total now 27,343 points = full corpus.** Reconciliation: FOOD_SAFETY matched 12,819 / failed 0 / unexplained 0; `fssai_db_in_fssai_qdrant` 12,819; `fssai_qdrant_not_in_db` 0; zero cross-hash collisions; provision_id↔Neo4j verified 1:1. Plan: `docs/FSSAI_REINGEST_PLAN.md`; audit update: `CORPUS_IDENTITY_REPORT.md` §8. New tests: `tests/test_reingest_fssai.py` (15 — load_corpus, build_payload identity, FSS-scope/backup guards, CLI semantics) — all pass; rollback export: `scripts/export_fssai_backup.py` + `reports/fssai_legal_768_pre_reingest_backup.json` (1,100 points w/ vectors). **Evaluation Framework ✅ Complete (2026-08-12)** — `evaluation/` (28 modules): retrieval arms A–G, RRF fusion, metrics (MRR, Recall@K, nDCG), ceiling analysis, root-cause analysis, report generation, and batch orchestration driving the full RAG pipeline (generation → verification → evaluation end-to-end). **Benchmark v1.0 ✅ Frozen (2026-08-12)** — `benchmark/` package (7 content modules + 8 artifact files): 150-question frozen multi-domain JSONL benchmark with gold provisions, sources, evaluation rubric, and review-conflict report. **Rust PyO3 Normalizers ✅ Complete (2026-08-12)** — `rust/` package (4 modules: `legal_engine.rs`, `lib.rs`, `normalizers.rs`, `removers.rs` + `Cargo.toml`); `docs/RUST_REFACTORING_EVALUATION.md` analysis + `tests/test_rust_normalizers.py`. **Remote Inference Layer ✅ Complete + Deployed (2026-08-16)** — Render free tier runs **zero local models**: dense embeddings (`all-mpnet-base-v2`, 768-dim) + fine-tuned legal CE (`sumanksaha/Foodmultidomain`) hosted on **Modal** (`modal_deploy/app.py`; live: `https://sumanksaha--embed.modal.run`, `https://sumanksaha--rerank.modal.run` — /embed returns 768-dim vectors, /rerank parity −0.821 vs local checkpoint) via `RemoteEmbedClient` (`RAG_EMBED_ENDPOINT` → `DenseRetriever`) + `RemoteRerankClient` (`RAG_RERANKER_ENDPOINT`, mode `tei` → ensemble encoder); BM25 sparse computed **in-cluster by Qdrant** (`Qdrant/bm25` — `QdrantStore.search_sparse_text`/`hybrid_search_text`, `RAG_QDRANT_BM25=true`, verified live: penalty query → §50/§51/§58). ⚠️ HF Serverless Inference API is **decommissioned** (api-inference.huggingface.co → 410/404 since late 2025; Inference Providers serve an allowlisted catalog only) — `mode="serverless"` + `scripts/test_hf_inference.py` are dead ends. New tests: `test_remote_embedder.py` (18), `test_qdrant_bm25.py` (13), `test_remote_reranker.py` (24). Deploy + Render env-var task: **task.md ENV-10** (code + Modal deploy done 2026-08-16; Render dashboard env vars pending). **LangGraph Agent Pipeline ✅ Complete (2026-08-16)** — M3+M4 from `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md` Part C: `app/rag/agent/` (state/nodes/graph/routes) — self-correcting `StateGraph` classify → retrieve → generate → verify → conditional expand-and-retry loop (`groundedness < 0.7`, max 2 retries, query expansion reusing `GroundedLLMClient`) → finalize; `POST /api/rag/query/agent` behind `RAG_USE_AGENT_PIPELINE` (default false → delegates to the legacy pipeline; `/api/rag/query` unchanged); `langgraph>=1.0.0` in `pyproject.toml` (lazy import — legacy path never touches it). New tests: `test_rag_agent_state.py` (5) + `test_rag_agent_nodes.py` (17) + `test_rag_agent_graph.py` (12) + `test_rag_agent_routes.py` (7) = **41/41 pass**; regression suites 33/33 green. Task: **task.md ENV-11**. **M5 ✅ Complete (2026-08-16)** — checkpointing + human-in-the-loop: `review` node (interrupt) between verify and finalize, `POST /api/rag/query/agent` → 202 `awaiting_review` when `RAG_AGENT_HITL=true`, `POST /api/rag/query/agent/resume` `{thread_id, approved}` (approved → finalize, rejected → expand-and-retry); checkpointers `MemorySaver` (default, singleton) / `PostgresSaver` (`RAG_AGENT_CHECKPOINTER=postgres`, `langgraph-checkpoint-postgres>=3.0` + `psycopg-binary` pinned). **A/B pipeline stamping:** `RAGQueryLog.pipeline` column (`legacy`/`agent`, migration `add_rag_query_log_pipeline`) + live 15-question A/B (`scripts/ab_agent_vs_legacy.py`): gold-hit@10 parity 0.233, latency agent 10.22 s vs legacy 10.85 s (stub LLM — quality gate needs `OPENROUTER_API_KEY` post-deploy). New tests: `test_rag_agent_m5.py` (15) + pipeline-field tests — agent suite 75/75. **Phase 20 ✅ Complete (2026-08-18)** — Plugin Architecture: `app/plugins/` package (`base.py` ABCs + `registry.py` `PluginRegistry` singleton + `ocr_plugins.py`, `ai_plugins.py`, `rule_plugins.py`, `pdf_plugins.py` concrete providers) with lazy imports, config-driven provider selection (`OCR_PROVIDER`/`AI_PROVIDER`/`RULES_PROVIDER`/`PDF_PROVIDER`), and all 6 callers refactored to use `PluginRegistry.get_active()` (ocr_extraction, adjudication/routes, validation/data_assembler, case_file_generator/tasks, food_cell/renderer, ai_assistant/routes); backward-compatible shims preserved (`pdf_utils` → `PDFAssemblyEngine` → registry); `tests/test_plugins.py` **23/23 pass**; `ruff check` + `ruff format --check` clean; no regressions in existing test suites. **FastAPI Gateway ✅ Complete (2026-08-19)** — ASGI coexistence gateway: `asgi.py` (FastAPI + a2wsgi.WSGIMiddleware mounting Flask at `/`); `app/api/deps.py` (`get_db`/`get_flag`/`get_rag_pipeline`); `app/api/routers.py` (`/api/v2/*` routes: health, search, ai-assistant/assist, bill/lookup, rag/generate, rag/retrieve, rag/query/agent, rag/eval, rag/ingest, rag/ingest/corpus, validation/validate); SecurityHeadersMiddleware + ApiKeyAuthMiddleware; render.yaml → `uvicorn asgi:app`; OpenAPI scoped to `/api/v2/docs`. `tests/test_asgi_py.py` **50/50 pass**; `ruff check` + `ruff format --check` clean. `FASTAPI_IMPLEMENTATION_PLAN.md` updated. Phase 6 (full rewrite) deferred per AGENTS.md §1.2.

> **Purpose:** Single reference for any AI agent or developer working in this codebase. Covers project context, architecture, key patterns, directory map, and deletion history. **Read this first, then `plan.md` and `task.md`.**

---

## 1. Project Overview

**NSA Webservice** is a government-grade legal workflow platform for Food Safety Officers (FSOs). It digitizes the complete lifecycle of food-safety legal proceedings under the **Food Safety and Standards Act, 2006 (FSS Act)**: inspection → sample tracking → case file generation → adjudication → billing → audit trail.

- **Stack:** Flask 2.x + SQLAlchemy 2.x + PostgreSQL (primary) / SQLite (dev) + WeasyPrint + Celery/Redis + QStash
- **Frontend:** Jinja2 + Vanilla JS + Quill 2.x (server-rendered, no React build pipeline)
- **Python:** 3.12+
- **Version:** v0.8.0

### Key Domain Split

- **CaseFile** — sample-based violations (petition + permission letter)
- **Adjudication** — non-sample adjudications (different template, same engine family)

### Architectural Decisions (documented, do not re-litigate)

- **Keep Flask** (not React) — roadmap's React prescriptions are skipped by decision
- **Canonical Key Contract** — `app/shared/case_keys.py` defines uniform field names across all modules
- **Hash-chained audit** — `AuditLog` uses SHA-256 chaining for tamper evidence
- **Optimistic concurrency** — `version_id` columns + `StaleDataError` → 409 on conflict
- **Storage abstraction** — `app/utils/storage.py` branches to Cloudinary / R2 / local per env vars
- **Race-safe sequences** — `CodeSequence` table + PostgreSQL advisory locks for unique code generation

---

## 2. Directory Map (as of v0.8.0)

```
NSA_webservice/
├── app.py                        # WSGI entry point (Flask)
├── asgi.py                       # ASGI gateway (FastAPI + Flask via a2wsgi.WSGIMiddleware) — FASTAPI_IMPLEMENTATION_PLAN.md Phases 1-5
├── celery_app.py                 # Celery factory
├── app/
│   ├── __init__.py               # App factory, blueprint registration, security headers
│   ├── extensions.py             # db, csrf, login_manager, talisman singletons
│   ├── audit_hooks.py            # SQLAlchemy after_flush audit event hooks
│   ├── models/                   # Split from monolithic models.py
│   │   ├── __init__.py           # Re-exports all models (backward-compatible)
│   │   ├── auth.py               # User, RecordAudit
│   │   ├── billing.py            # Bill, BillSample, CodeSequence, Sample
│   │   ├── config.py             # AppSecret, Settings
│   │   ├── document.py           # Adjudication, Annexure, CaseFile, Evidence, Version
│   │   ├── inspection.py         # FSO, AuditLog, Inspection
│   │   ├── issue.py              # FboIssue, FboIssueAudit
│   │   ├── food_cell.py          # DoIntimation (DO intimation record)
│   │   └── rag.py                # LegalDocument, LegalChunk, RAGQueryLog, RAGEvalResult, RAGEvalDataset
│   ├── adjudication/             # Non-sample adjudication
│   ├── annexure/                 # Annexure upload + metadata
│   ├── api/                     # FastAPI API gateway (Phase 5-6 incremental): deps.py (get_db/get_flag), routers.py (/api/v2/* routes)
│   ├── audit/                    # Audit log viewer
│   ├── auth/                     # Login/logout/change-password
│   ├── bill_generator/           # Bill PDF (QStash async)
│   ├── billing/                  # Billing summary + Excel
│   ├── case_file_generator/      # Sample-based petition/PDF generation
│   ├── document_cleaner/         # Legal text cleaning pipeline
│   ├── document_loader/          # PDF/DOCX/TXT ingestion
│   ├── document_viewer/          # Quill editor + save/restore
│   ├── evidence/                 # Unified Evidence model + UI
│   ├── fbo_issue/                # State machine + audit trail
│   ├── food_cell/                # Phase 21 DO Intimation workflow (2026-08-06)
│   │   └── tasks.py              # Celery task for DO intimation PDF + sync
│   ├── health/                   # GET /health probe (public)
│   ├── inspection/               # CRUD + photos + OCR (split into routes/ package)
│   │   └── routes/               # Modular: inspection_routes, lookup_routes,
│   │                             #   photo_routes, derived_views
│   ├── knowledge_graph/          # Phase 14 entity/relationship graph engine
│   │   ├── engine.py             # KnowledgeGraphEngine (entity extraction)
│   │   ├── tasks.py              # Celery task for Neo4j sync
│   │   └── templates/            # Cytoscape.js visualizer
│   ├── legal_analysis/           # Legal paragraph detection workbench
│   ├── metadata_extractor/       # Regex + NER field extraction
│   ├── ocr_pipeline/             # PaddleOCR + Tesseract
│   ├── pdf_assembly/             # PDF assembly engine
│   ├── plugins/                 # Phase 20: plugin architecture (base, registry, ocr/ai/rules/pdf plugins)
│   ├── sample/                   # Sample tracking CRUD
│   ├── rag/                     # RAG: corpus/embedding + retrieval (2026-08-08) — Qdrant, embeddings, chunker, indexer, pipeline, adapters
│   │   ├── retrieval/           # DenseRetriever, SparseRetriever, HybridRetriever, Reranker, QueryClassifier, logger
├── kg/                          # Legal Knowledge Graph (multi-domain, 2026-08-11): schema, corpus ingestion (KGCorpusIngestionEngine),
│   │                           #   semantic enrichment (LegalSemanticEnricher), hybrid expansion (KGContextExpander), queries, validation, manifest
├── scripts/build_kg_corpus.py   # CLI: rebuild legal KG from manifest + Qdrant + FSS DB
├── scripts/enrich_kg_semantics.py  # CLI: semantic tagging (duty/offence/penalty/prohibition/power) of provisions
│   ├── search/                   # SQLite FTS5 + API
│   ├── services/                 # Business logic services
│   │   ├── legal_engine.py       # Legal engine wrapper
│   │   ├── sheets_sync.py        # Google Sheets sync
│   │   ├── version_control.py    # Version compare/restore, branching
│   │   ├── neo4j_graph.py        # Neo4j Aura service (APOC dynamic labels, constraints, indexes)
│   │   ├── backup_coordinator.py # Multi-target backup (Sheets + Airtable + R2)
│   │   └── audit.py
│   ├── settings/                 # Settings + backup/restore
│   ├── shared/                   # Canonical keys + context deriviners
│   ├── tasks_webhook/            # QStash webhook + task status
│   ├── timeline/                 # Phase 13 milestone timeline + Gantt UI
│   ├── toc_generator/            # Dynamic TOC extraction/numbering
│   ├── ai_assistant/             # Phase 11 AI assistant (2026-08-08)
│   ├── utils/                    # Filters, storage, pdf_utils, lookup, etc.
│   ├── static/                   # CSS, JS (Quill vendor, editor.js, task_status.js)
│   ├── templates/base.html       # Master layout (global Timeline case-picker)
│   ├── workdiary/                # Work Diary (2026-08-26): per-FSO inspection diary,
│   │                             #   engine.py (row shaping + explicit visit_purpose),
│   │                             #   routes (/ , /preview, /pdf), report.html = official
│   │                             #   FSO_Work_Diary_Template.html format
│   └── version_control/          # Version history UI + routes
├── migrations/                   # Alembic — 28 migration files (newest: add_inspection_visit_purpose)
├── tests/                        # 90+ pytest modules, ~1,930 test cases (incl. 28 workdiary + 12 backup-monitoring), all passing
├── legal_paragraph_detection_engine/  # Standalone rule-based legal parser
├── scripts/                      # Utility scripts (create_user.py kept; others deleted)
├── .github/workflows/            # CI: lint, pip-audit, validation, deploy, docker-build, release
├── pyproject.toml                # Dependencies + tool config (setuptools build)
├── requirements.txt              # -e . + dev deps
├── render.yaml                   # Deploy config
└── .env.example                  # 23 environment variables
```

### Registered Flask Blueprints (26)

| Blueprint           | Prefix               | Purpose                                     |
| ------------------- | -------------------- | ------------------------------------------- |
| auth                | /auth                | Login, logout, change password              |
| case_file_generator | /case_file_generator | Petition + permission letter (sample-based) |
| adjudication        | /adjudication        | Non-sample adjudication                     |
| document_viewer     | /document_viewer     | Quill editor, save/restore, PDF             |
| evidence            | /evidence            | Evidence library (photos, reports, etc.)    |
| bill_generator      | /bill_generator      | Bill PDF (async via QStash)                 |
| billing             | /billing             | Billing summary + Excel export              |
| fbo_issue           | /fbo-issue           | FBO issue state machine                     |
| sample              | /sample              | Sample tracking CRUD                        |
| inspection          | /inspection          | Inspection CRUD + photos + OCR              |
| legal_analysis      | /legal               | Legal paragraph detection workbench         |
| audit               | /admin               | Read-only audit log viewer                  |
| settings            | /settings            | Settings dashboard, backup/restore          |
| version_control     | /api/version-control | Version history UI + API                    |
| search              | /search              | FTS5 + fuzzy search API                     |
| rag                 | /rag                 | RAG health + retrieval API (2026-08-08)     |
| tasks_webhook       | _(none)_             | QStash webhook, task status                 |
| annexure            | /annexure            | Annexure upload + metadata                  |
| timeline            | /timeline            | Phase 13 milestone timeline + Gantt UI      |
| health              | /health              | Health probe (public)                       |
| food_cell           | /food-cell           | Phase 21 DO Intimation workflow             |
| knowledge_graph     | /knowledge-graph     | Phase 14 entity/relationship graph + Neo4j  |
| ai_assistant        | /ai-assistant        | Phase 11 AI assistant (2026-08-08)          |
| workdiary           | /workdiary           | Per-FSO work diary + official report PDF (2026-08-26) |
| notepad             | /notepad             | Notes intake queue + AI evaluation (Phase 18) |
| comments            | /comments            | Case comments API (Phase 18 RBAC)           |

---

## 3. Key Patterns for Agents

### 3.1 Blueprint Registration

New blueprints are registered in `app/__init__.py::create_app()`. Add the import + `app.register_blueprint(bp, url_prefix=...)` in the alphabetical section.

### 3.2 Model Imports

All models are importable from `app.models` (re-exported). New models go in `app/models/<subcategory>.py` and are added to `app/models/__init__.py`.

### 3.3 PDF Generation (centralized)

All HTML→PDF goes through `app/utils/pdf_utils.py::generate_pdf_from_html()`. WeasyPrint is imported defensively (`import_weasyprint()`) so the app still boots on hosts without GTK.

### 3.4 Async Tasks

QStash (webhook-based, no worker required on free tier) + Celery (for heavier jobs). `celery_app.py` creates the Celery instance.

### 3.5 Security

- `app/__init__.py::create_app()` sets up Talisman CSP, ProxyFix, CSRF, Flask-Login
- Global `before_request` gate (`require_login`) — all routes require auth except listed `public_endpoints`
- Audit hooks fire on SQLAlchemy `after_flush` for Adjudication, Bill, CaseFile
- Security TODOs tracked in `task.md` (S7, S2, S9a, S6a, etc.)

### 3.6 Configuration resolution — always via `cfg` (the config seam)

All feature flags/settings resolve through **`app/shared/config.py`** (`cfg`, the
declaration table + Pattern A rule). Never hand-roll a
`try: current_app.config / except: os.environ` resolver.

- Named access: `cfg.kg_fusion`, `cfg.reranker_model`, `cfg.ensemble_ce_head`
- Dynamic keys (e.g. `{CATEGORY}_PROVIDER`): `cfg.get_str(key)`, `cfg.get_bool(key)`
- Adding a flag = one `Setting(...)` row in the table + one `.env.example` entry
  (docs parity is enforced by `tests/test_shared_config.py`)
- Resolution (**Pattern A**): Flask config wins inside an app context; env is
  read outside one; otherwise the declared default. `create_app()` calls
  `seed_config_from_env(app)` so env vars behave identically in-context.
- Boolean conventions are per-flag and declared (`opt_in`: string must be
  `"true"`; `opt_out`: anything but `"false"`). String `"false"` in config
  parses to `False` (the old `bool("false") is True` trap is fixed).

See `CONTEXT.md` for the glossary entry. Replaced ~30 hand-rolled resolvers
(~550 lines across tasks.py, agent/graph.py, api/deps.py, retrieval/*,
plugins/registry.py) on 2026-08-22.

### 3.7 Deprecation Notes

- `datetime.utcnow()` → use `datetime.now(timezone.utc)` throughout
- `Model.query.get()` → use `db.session.get(Model, id)`
- `db.get_engine()` → use `db.engines['default']` in migrations

---

## 4. How to Work Here

### Running Tests

```bash
python -m pytest tests/ -v          # full suite (~1,887 tests: 1,780 Flask + 57 ASGI + 50 other new)
python -m pytest tests/test_<x>.py  # targeted
```

### Running the App (dev)

```bash
python app.py                        # Flask WSGI (default port 8000)
python -m uvicorn asgi:app --reload  # FastAPI ASGI gateway (port 8000) — runs Flask + FastAPI
FLASK_APP=app.py flask run         # alternative Flask
```

### Linting / Formatting

```bash
ruff check .                       # lint
ruff format .                      # format
mypy .                             # type check
npm run lint                       # JS lint (ESLint + Prettier)
```

### Migrations

```bash
flask db migrate -m "description"  # generate
flask db upgrade                   # apply
```

### Environment

Copy `.env.example` → `.env`, set `DATABASE_URL`, `SECRET_KEY`, `SPREADSHEET_ID`, etc.

---

## 5. Deletion History (what was cleaned up)

The following were deleted in the 2026-08-03/04 cleanup commit (`deletion_plan.md`, `cleanup_report.md`):

| Category              | What was removed                                                                    | Reason                                                                                          |
| --------------------- | ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Root duplicates (S6a) | `suggester.py` (120 LOC, root)                                                      | Orphaned duplicate; app uses `app/utils/suggester.py` (docstring + annotations backported)      |
| Root duplicates (S6b) | `sections_data.py` (root)                                                           | Older duplicate; app uses `app/utils/sections_data.py` (pathlib, typed)                         |
| Tracked bytecode      | `__pycache__/app/suggester/sections_data.cpython-313.pyc`                           | Compiled artifacts committed despite `__pycache__/` in `.gitignore`                             |
| Broken duplicate      | `legal-paragraph-detection-engine/` (5,095 LOC)                                     | Dashed duplicate with broken imports; app uses `legal_paragraph_detection_engine/` (underscore) |
| Scratch scripts       | `_fix_*.py`, `_write_*.py`, `_test_*.py`, `_debug_*.py`                             | Never committed, local debugging                                                                |
| One-off check scripts | `check_*.py`, `search_*.py`, `filter_house_number.py`, `fuzzy_dedup_stage0.py`      | 0 importers, 0 CI references                                                                    |
| AI tool artifacts     | `.pi-subagents/artifacts/`, `piolium/`, `.opencode/plans/`, `.ai/PROJECT_MEMORY.md` | Agent harness working state                                                                     |
| Debug snapshots       | `_init_full.txt`, `_plan_output.txt`, `_roadmap_out.txt`, etc.                      | AI tooling output                                                                               |
| Runtime artifacts     | `instance/saved/*.html`, `instance/editor_images/*.png`                             | Now gitignored                                                                                  |
| CSV data files        | `*.csv` (root)                                                                      | ~70MB, untracked via `.gitignore`                                                               |

### AI Planning Docs Removed (consolidated into this file set)

- `AST_SKELETONIZATION.md` (1,964 lines) → codebase structure → this file (§2)
- `ENGINEERING_ASSESSMENT.md` (1,390 lines) → performance bottlenecks → `plan.md` §4
- `ROADMAP_ALIGNMENT_REPORT.md` (1,159 lines) → roadmap phases → `plan.md` + `task.md`
- `LEGAL_ENGINE_ANALYSIS_TODO.md` → merged into `task.md`
- `IMPLEMENTATION_PLAN.md` (141 lines) → was a status snapshot; superseded by `plan.md` + `task.md`
- `cleanup_report.md` (248 lines) → this file §5
- `deletion_plan.md` (212 lines) → this file §5
- `technical_debt_implementation_plan.md` (435 lines) → already completed, results captured here
- `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md` (328 lines) → `task.md` Cloudinary section

---

## 8. Known Shallow Modules & Architectural Friction

The following modules have low Module Depth (interface nearly as complex as implementation). They are the targets of the ongoing deepening effort in `task.md` §Deepening Tasks. Current status and refined targets:

### ✅ CONFIRMED SHALLOW MODULES (Priority Order)

1. **Cross-module case resolution** — **HIGHEST PRIORITY (D1)**
    - **Problem**: `_resolve_case()` in `document_viewer/routes.py` (lines 66-78), `_resolve_target()` + `_kind_param()` in `version_control/routes.py` (lines 39-68), and inline lookups in evidence/search/annexure all solve the same "CaseFile vs Adjudication ID" problem
    - **Target**: `CaseResolver` class in `app/shared/case_resolver.py` with `ResolvedCase` dataclass
    - **Interface**: `resolve(case_id, kind=None) -> ResolvedCase | None`
    - **Dependencies**: None (prerequisite for D2 and D5)
    - **Effort**: 1 day | **Risk**: Low | **Module Depth**: 1 → 4

2. **Document viewer inlined concerns** — **PRIORITY 2 (D2)**
    - **Problem**: Route file directly calls `VersionService`, `log_audit`, `save_saved_document` via 5 private helpers (`_resolve_case`, `_save_document_content`, `_log_audit`, `_snapshot_version`, `_actor`)
    - **Target**: `DocumentSaveCoordinator` class in `app/services/document_lifecycle.py`
    - **Interface**: `save(case_id, case_type, doc_type, html, delta, force_snapshot=False) -> SaveResult`
    - **Dependencies**: D1 (CaseResolver)
    - **Effort**: 1 day | **Risk**: Low | **Module Depth**: 2 → 4

3. **PDF utils grab-bag** — **PRIORITY 3 (D3)**
    - **Problem**: `app/utils/pdf_utils.py` mixes WeasyPrint import guard, bookmark CSS, post-processing orchestration, and image embedding
    - **Current State**: `PDFAssemblyEngine` class already exists in `app/pdf_assembly/__init__.py` (42KB) but needs consolidation
    - **Target**: Complete `PDFAssemblyEngine` in `app/pdf_assembly/engine.py` with clean interface
    - **Interface**: `assemble()`, `post_process()`, `embed_photos()`, `generate_from_html()`
    - **Dependencies**: None
    - **Effort**: 2 days | **Risk**: Medium | **Module Depth**: 3 → 4

4. **Inspection routes mechanical split** — **PRIORITY 4 (D4)**
    - **Problem**: `photo_routes.py` (15,111 bytes, ~400+ lines) mixes EXIF extraction, validation, storage, OCR dispatch, and routes
    - **Target**: `InspectionPhotoService` class in `app/inspection/photo_service.py`
    - **Interface**: `upload_evidence()`, `upload_adjudication_photo()`, `delete()`, `list_for_inspection()`, `list_adjudication()`
    - **Dependencies**: None
    - **Effort**: 2 days | **Risk**: Medium | **Module Depth**: 1 → 4

5. **Case/Adjudication route duplication** — **PRIORITY 5 (D5)**
    - **Problem**: `case_file_generator/routes.py` (697 lines) and `adjudication/routes.py` (820 lines) are near-mirrors with duplicated logic
    - **Target**: `DocumentCaseManager` class in `app/shared/document_case_manager.py`
    - **Interface**: Parameterized by `(model, template_dir, bp, case_type, sections_fn)` with methods for CRUD, rendering, and document generation
    - **Dependencies**: D1 (CaseResolver), D2 (DocumentSaveCoordinator)
    - **Effort**: 3 days | **Risk**: Medium | **Module Depth**: 2 → 4

### 📊 REFACTORING METRICS

| Module                  | Current Lines | Current Depth | Target Depth | Complexity Reduction      |
| ----------------------- | ------------- | ------------- | ------------ | ------------------------- |
| CaseResolver            | N/A           | N/A           | 4            | New abstraction           |
| DocumentSaveCoordinator | N/A           | N/A           | 4            | Encapsulates 5 helpers    |
| PDFAssemblyEngine       | ~1000+        | 3             | 4            | Consolidates PDF concerns |
| InspectionPhotoService  | ~400+         | 1             | 4            | Separates business logic  |
| DocumentCaseManager     | ~1500+        | 2             | 4            | Eliminates duplication    |

### 🎯 RECOMMENDED IMPLEMENTATION ORDER

1. **D1: CaseResolver** (Foundation - no dependencies)
2. **D2: DocumentSaveCoordinator** (Depends on D1)
3. **D3: PDFAssemblyEngine** (Independent, can run parallel)
4. **D4: InspectionPhotoService** (Independent, can run parallel)
5. **D5: DocumentCaseManager** (Depends on D1+D2)

**Parallelization**: D3 and D4 can be implemented concurrently with D1+D2

---

## 6. Environment Variables (`.env.example`)

| Variable                                                                 | Purpose                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `SECRET_KEY`                                                             | Flask session signing (required in production)                                                                                                                                                                                                                                                                                                                      |
| `DATABASE_URL`                                                           | PostgreSQL or SQLite URL                                                                                                                                                                                                                                                                                                                                            |
| `SPREADSHEET_ID`                                                         | Google Sheets sync target                                                                                                                                                                                                                                                                                                                                           |
| `GOOGLE_CREDENTIALS_JSON`                                                | Service account for Sheets API                                                                                                                                                                                                                                                                                                                                      |
| `REDIS_URL`                                                              | Celery broker + cache                                                                                                                                                                                                                                                                                                                                               |
| `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` | Cloudinary photo storage (optional)
| `API_V2_KEY`                                                              | Optional API key for `/api/v2/*` FastAPI gateway (ASGI). When unset, `/api/v2/*` is open (dev mode); set for production auth via `x-api-key` header. See `asgi.py` `ApiKeyAuthMiddleware`.                                                                                                                                                                                                                                                                                                                                 |
| `R2_*` / `B2_*`                                                          | S3-compatible storage fallback                                                                                                                                                                                                                                                                                                                                      |
| `PDF_ENABLE_HYPERLINKS`                                                  | Toggle PDF link annotation (default on)                                                                                                                                                                                                                                                                                                                             |
| `PDF_ENABLE_QR_CODES`                                                    | Toggle QR in PDFs (default off)                                                                                                                                                                                                                                                                                                                                     |
| `PDF_ENABLE_SIGNATURES`                                                  | Toggle signature placeholders                                                                                                                                                                                                                                                                                                                                       |
| `PDF_USE_DIRECT_URLS`                                                    | Embed photo URLs directly vs base64                                                                                                                                                                                                                                                                                                                                 |
| `RAG_QDRANT_URL`                                                         | Qdrant server URL (RAG)                                                                                                                                                                                                                                                                                                                                             |
| `RAG_QDRANT_API_KEY`                                                     | Qdrant Cloud API key (RAG)                                                                                                                                                                                                                                                                                                                                          |
| `RAG_VECTOR_SIZE`                                                        | Vector dimension (default 768)                                                                                                                                                                                                                                                                                                                                      |
| `RAG_QDRANT_COLLECTION`                                                  | Qdrant collection name (default `fssai_legal_768`)                                                                                                                                                                                                                                                                                                                  |
| `RAG_KG_EXPANSION`                                                       | KG graph expansion in generation (default false; chunk→graph provisions injected)                                                                                                                                                                                                                                                                                   |
| `RAG_KG_FUSION`                                                          | KG retrieval-contract fusion in generation (default false; query→graph provisions RRF-fused into context — production equivalent of eval arm G, significant Recall@10 gain, 2026-08-12)                                                                                                                                                                             |
| `RAG_KG_MAX_PROVISIONS`                                                  | Max KG provisions injected into the LLM context (default 5)                                                                                                                                                                                                                                                                                                         |
| `RAG_QDRANT_COLLECTION_<DOMAIN>`                                         | Per-domain collection override (env/commercial/animal/wb_state/criminal — multi-domain Phase 1, 2026-08-20)                                                                                                                                                                                                                                                         |
| `RAG_EMBEDDING_MODEL`                                                    | Sentence-transformers model (default `all-mpnet-base-v2`)                                                                                                                                                                                                                                                                                                           |
| `RAG_RERANKER_MODEL`                                                     | Optional cross-encoder reranker model                                                                                                                                                                                                                                                                                                                               |
| `RAG_RERANKER_ENDPOINT`                                                  | Remote CE `/rerank` URL (Modal `https://<ws>--rerank.modal.run`, TEI mode; empty = local CE). ⚠️ HF Serverless API decommissioned — never use `mode="serverless"`                                                                                                                                                                                                   |
| `RAG_RERANKER_MODE`                                                      | Remote CE backend: `tei` (default; TEI `/rerank` — Modal/Space/Inference Endpoint). `serverless` targets the decommissioned api-inference.huggingface.co — do not use                                                                                                                                                                                               |
| `RAG_RERANKER_REMOTE_FALLBACK`                                           | Lazy local-CE fallback on remote failure (default true; **false required on Render free tier** — a torch build would OOM the 512 MB instance; false degrades to sec_act features-only)                                                                                                                                                                              |
| `RAG_EMBED_ENDPOINT`                                                     | Remote dense `/embed` URL (Modal `https://<ws>--embed.modal.run`; empty = local `SentenceTransformer`). `RemoteEmbedClient` injected into `DenseRetriever.embed_query`                                                                                                                                                                                              |
| `RAG_EMBED_REMOTE_FALLBACK`                                              | Lazy local-embedder fallback on remote failure (default true; **false required on Render free tier** — false degrades to sparse-only)                                                                                                                                                                                                                               |
| `RAG_QDRANT_BM25`                                                        | Qdrant-side BM25 (server-side sparse inference, `Qdrant/bm25` — no local fastembed at query time; requires qdrant-client >= 1.12 + `text_sparse` with `modifier: idf`; default false, `true` in production)                                                                                                                                                         |
| `RAG_USE_AGENT_PIPELINE`                                                 | LangGraph agent pipeline (2026-08-16, M3+M4): when true, `POST /api/rag/query/agent` runs the self-correcting graph (classify → retrieve → generate → verify → expand-and-retry on groundedness < 0.7, max 2 retries); default false — endpoint delegates to the legacy pipeline and `/api/rag/query` is never affected. Requires `langgraph`.                      |
| `RAG_AGENT_HITL`                                                         | M5 human-in-the-loop (2026-08-16): when true, the agent graph pauses at a `review` interrupt before finalize — the route returns 202 `awaiting_review` (thread_id + review payload) and `POST /api/rag/query/agent/resume` `{thread_id, approved}` resumes (approved → finalize, rejected → expand-and-retry). Default false (end-to-end). Requires a checkpointer. |
| `RAG_AGENT_CHECKPOINTER`                                                 | M5 checkpointer (2026-08-16): `memory` (default — `MemorySaver` singleton, in-process, dev/tests) or `postgres` (`PostgresSaver` vs `DATABASE_URL`; requires `langgraph-checkpoint-postgres` + `psycopg-binary`). Enables thread resume for paused HITL runs.                                                                                                       |
| `RAG_HALLUCINATION_DETECTOR`                                             | Phase 3 claim-level HallucinationDetector on the live generation hot path (2026-08-23): merges detector verdicts into a `verification` block and escalates claim-level hallucinations the sanitizer missed; failures degrade best-effort (default true, opt-out)                                                                                                      |
| `RAG_ENABLE_INGESTION_SCHEDULE`                                          | Enable QStash daily corpus ingestion (default false) — resolved through the config seam; registered by `ScheduledJobs.register_all` at startup                                                                                                                                                       |
| `RAG_INGESTION_CRON`                                                     | Cron schedule for ingestion (default `0 3 * * *`)                                                                                                                                                                                                                                                   |
| `ENABLE_BACKUP_SCHEDULE`                                                 | Register the daily QStash multi-target backup schedule (`backup_redundant_sheets`, 02:00 UTC) at startup via `ScheduledJobs` (default false)                                                                                                                                                         |
| `RAG_CORPUS_DIR`                                                         | Directory path for corpus documents                                                                                                                                                                                                                                                                                                                                 |
| `NEO4J_URI`                                                              | Neo4j Aura Bolt URI (e.g. `neo4j+s://<id>.databases.neo4j.io`)                                                                                                                                                                                                                                                                                                      |
| `NEO4J_USERNAME`                                                         | Neo4j Aura username (always `neo4j`)                                                                                                                                                                                                                                                                                                                                |
| `NEO4J_PASSWORD`                                                         | Neo4j Aura password/API key                                                                                                                                                                                                                                                                                                                                         |
| `NEO4J_DATABASE`                                                         | Neo4j Aura database name (default `neo4j`)                                                                                                                                                                                                                                                                                                                          |
| `NEO4J_ALLOW_WRITE`                                                      | Fail-closed write guard (2026-08-12): `push_to_neo4j` / `clear_legal_kg` / `neo4j_aura_loader` refuse destructive writes unless set to `1`. Leave unset in CI/test — a test suite wiped the live legal KG twice (2026-08-11, 2026-08-12). Set `NEO4J_ALLOW_WRITE=1 python scripts/build_kg_corpus.py` for deliberate rebuilds.                                      |
| `OCR_PROVIDER`                                                           | Phase 20: Active OCR provider — `easyocr` (default), `paddleocr`, `tesseract`                                                                                                                                                                                                                                                                                       |
| `OCR_LANGUAGES`                                                          | Phase 20: Comma-separated languages for the active OCR provider (default `english,hindi`; adapter splits on `,`)                                                                                                                                                                                                                                                     |
| `OCR_USE_GPU`                                                            | Phase 20: GPU for OCR inference (default false — keep false on CPU-only hosts, e.g. Render free tier)                                                                                                                                                                                                                                                                |
| `AI_PROVIDER`                                                            | Phase 20: Active AI provider — `openrouter` (default), `openai`                                                                                                                                                                                                                                                                                                     |
| `RULES_PROVIDER`                                                         | Phase 20: Active rule provider — `fssai_default` (default)                                                                                                                                                                                                                                                                                                          |
| `PDF_PROVIDER`                                                           | Phase 20: Active PDF provider — `weasyprint` (default)                                                                                                                                                                                                                                                                                                              |

---

## 7. Test Inventory

> **Reconciliation (2026-08-20):** per-file counts below have drifted as phases landed;
> `pytest --collect-only` is authoritative — **1,780 tests total**, of which **694 are
> RAG-related** (full audit inventory in `RAG_AUDIT_REPORT.md` §2). Phase 1 multi-domain
> adds `tests/test_multidomain_phase1.py` (37).

| Test File                        | Tests | Covers                                                                                                                                                                                                      |
| -------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| test_ai_assistant.py             | 23    | Phase 11: service enabled/disabled, each action, token tracking, route 200/400/503                                                                                                                          |
| test_annexure.py                 | 22    | Annexure upload, replace, rename, reorder, delete, duplicate detection                                                                                                                                      |
| test_case_backup.py              | 14    | Phase 16: JSON/ZIP export, case import                                                                                                                                                                      |
| test_case_resolver.py            | —     | CaseResolver CaseFile/Adjudication disambiguation                                                                                                                                                           |
| test_concurrency_inspection.py   | 4     | S9a: StaleDataError → 409 on inspection PUT/DELETE                                                                                                                                                          |
| test_document_lifecycle.py       | —     | DocumentSaveCoordinator save/version/audit                                                                                                                                                                  |
| test_food_cell_do_intimation.py  | 15    | Phase 21: DO Intimation generate/forward/sync                                                                                                                                                               |
| test_workdiary.py                | 28    | Work Diary: explicit `visit_purpose` derivation + legacy heuristic, per-FSO/date/purpose filters, index/preview/PDF routes (official template), create-route validation, auth gate                            |
| test_rbac.py                     | 44    | Phase 18 RBAC: role gate matrix, login landing, CaseFile/Adjudication scoping+stamping, child inheritance, Inspection/Work Diary lock, Notepad access, Comments API, provisioning uniqueness + seed   |
| test_backup_monitoring.py        | 12    | S10c `/health/backups` dead-man's-switch (never/ok/degraded/stale) + S2 `POST /csp-report` collector (formats, CSRF-exempt, public)                                                                          |
| test_inspection_photo_service.py | —     | InspectionPhotoService upload/verify/stamp                                                                                                                                                                  |
| test_ocr_extraction.py           | 14    | Phase A: OCR field extraction + task persistence                                                                                                                                                            |
| test_auth_*.py                   | 9+9   | Auth: login, password change                                                                                                                                                                                |
| test_bill_generator.py           | 11    | Bill PDF template vars                                                                                                                                                                                      |
| test_cross_reference.py          | 27    | Reference extraction/linking/renumbering                                                                                                                                                                    |
| test_document_cleaner.py         | 45    | Text cleaning pipeline                                                                                                                                                                                      |
| test_document_loader.py          | 35    | PDF/DOCX/TXT loading                                                                                                                                                                                        |
| test_document_viewer.py          | 24+27 | Editor save/retrieve, Markdown export, TOC                                                                                                                                                                  |
| test_legal_suggest.py            | 4     | Section suggestions                                                                                                                                                                                         |
| test_neo4j_kg_sync.py            | 15    | Phase 14 Neo4j: config detection, real connection, APOC push, sync task, route async/sync                                                                                                                   |
| test_metadata_extractor.py       | 31    | Regex + NER extraction                                                                                                                                                                                      |
| test_ocr_pipeline.py             | 24    | OCR pipeline                                                                                                                                                                                                |
| test_pdf_photo_embedding.py      | 11    | Photo embedding in PDFs                                                                                                                                                                                     |
| test_phase1.py                   | —     | Validation errors, Facts/Grounds/Prayer                                                                                                                                                                     |
| test_phase3_models.py            | —     | Settings, Annexure, Evidence, Version models                                                                                                                                                                |
| test_phase5_evidence.py          | 16    | Unified Evidence model                                                                                                                                                                                      |
| test_phase7_toc_generator.py     | 37    | TOC extraction/numbering/bookmarks                                                                                                                                                                          |
| test_phase8_pdf_assembly.py      | 40    | PDF assembly, hyperlinks, QR, signatures                                                                                                                                                                    |
| test_route_collisions.py         | 2     | URL collision regression + anonymous lookup reachability (seeds its own `FssaiLicense` row)                                                                                                                  |
| test_search.py                   | 56    | FTS5 search, fuzzy fallback, API, auto-index hooks                                                                                                                                                          |
| test_step1-5_integration.py      | 74    | End-to-end integration                                                                                                                                                                                      |
| test_storage.py                  | —     | Storage backend selection                                                                                                                                                                                   |
| test_timeline.py                 | 21    | Phase 13: engine, routes, picker, entry points                                                                                                                                                              |
| test_version_control.py          | 23    | Version compare, restore, branching                                                                                                                                                                         |
| test_toc_generator.py            | 37    | TOC generator engine                                                                                                                                                                                        |
| test_qdrant_client.py            | 25    | QdrantStore: connect, collection, upsert, search, delete, health                                                                                                                                            |
| test_embedding_service.py        | 17    | EmbeddingService: embed_text, batch, dim validation                                                                                                                                                         |
| test_chunker.py                  | 20    | Chunker: LegalParagraphEngine → Chunk, §5.1 payload schema                                                                                                                                                  |
| test_qdrant_indexer.py           | 16    | QdrantIndexer: after_flush hook, retry-once upsert, ChunkIngestion                                                                                                                                          |
| test_dedup.py                    | 12    | ChunkDeduper: SHA-256 normalized hashing, document/chunk dedup                                                                                                                                              |
| test_ingestion_pipeline.py       | 16    | IngestionPipeline: full e2e, real-loader, corpus batch, fault isol                                                                                                                                          |
| test_metadata_adapter.py         | 19    | MetadataAdapter: LegalMetadataEngine → §5.1 payload (enum, dates)                                                                                                                                           |
| test_citation_adapter.py         | 18    | CitationAdapter: §2.3-fixed extractor → §5.1/§5.2 citations                                                                                                                                                 |
| test_crossref_adapter.py         | 14    | CrossRefAdapter: full-Act sections → §5.1/§5.2 references                                                                                                                                                   |
| test_chunk_quality.py            | 12    | ChunkQualityValidator: A-F grading, score_field + Validator                                                                                                                                                 |
| test_legal_document_model.py     | 7     | LegalDocument/LegalChunk models, UNIQUE, indexes, hook registration                                                                                                                                         |
| test_rag_tasks.py                | 7     | embed_and_index_task, ingest_corpus_task wiring, graceful degradation                                                                                                                                       |
| test_query_classifier.py         | ~12   | QueryType classification, section/authority/case-law parsing                                                                                                                                                |
| test_dense_retriever.py          | ~15   | Qdrant search, score threshold, top-k, filters                                                                                                                                                              |
| test_sparse_retriever.py         | ~10   | rapidfuzz fuzzy matching, query preprocessing                                                                                                                                                               |
| test_hybrid_retriever.py         | ~20   | Dense + sparse RRF fusion, score interpolation, ranking                                                                                                                                                     |
| test_reranker.py                 | ~8    | Cross-encoder reranking, top-k reorder                                                                                                                                                                      |
| test_retrieval_logger.py         | 8     | Query log persistence, hash chain, token/latency tracking                                                                                                                                                   |
| test_query_log_model.py          | 11    | RAGQueryLog model, indexes, queries                                                                                                                                                                         |
| test_rag_e2e.py                  | 9     | Query → retrieve → log, hash chain, audit chain                                                                                                                                                             |
| test_rag_generation.py           | 40    | Phase 2: ContextBuilder, PromptTemplate, LLM client, CitationTracker,                                                                                                                                       |
|                                  |       | ResponseSanitizer, GroundedGenerationService, GenerationLogger,                                                                                                                                             |
|                                  |       | run_generation_pipeline, /api/rag/generate route (stub LLM mode)                                                                                                                                            |
| test_rag_smoke.py                | 9     | RAG module smoke: ping, embed, vector size, chunks, search, classify                                                                                                                                        |
| test_corpus_ingestion_e2e.py     | 8     | Agent A §6.2: raw-doc → Qdrant round-trip (search verify, §5.1 payload, batch, enrichment)                                                                                                                  |
| test_batch_ingestion.py          | 5     | Agent A §6.2: QStash ingest_corpus schedule wiring + batch progress tracking                                                                                                                                |
| test_reindexing.py               | 3     | Agent A §6.2: delete + re-index after content changes                                                                                                                                                       |
| test_rag_benchmarks.py           | 11    | Agent A Day 13: benchmark harness (chunking/embedding/store throughput)                                                                                                                                     |
| test_hallucination_detector.py   | 28    | Phase 3: ClaimExtractor, EvidenceVerifier, CitationValidator,                                                                                                                                               |
|                                  |       | GroundednessScorer, HallucinationDetector (grounded/ungrounded detection)                                                                                                                                   |
| test_citation_validator.py       | 6     | Phase 3: CitationValidator standalone (valid/invalid/section-mismatch)                                                                                                                                      |
| test_eval_framework.py           | 39    | Phase 4: All 6 metrics + EvalRunner + EvalStorage + EvalReport                                                                                                                                              |
| test_rag_e2e_verification.py     | 6     | Phase 3+4 integration: generation -> verification -> evaluation                                                                                                                                             |
| test_token_counter.py            | 10    | Phase 3: tiktoken + fallback estimation, RAGQueryLog.context_length                                                                                                                                         |
| test_resilient_pipeline.py       | 10    | Phase 5: Circuit breaker, fallback, state machine                                                                                                                                                           |
| test_hybrid_vs_dense.py          | 7     | Phase 5: Hybrid RRF vs dense-only retrieval quality comparison                                                                                                                                              |
| test_eval_batch.py               | 10    | Phase 4: Batch evaluation, MRR, error isolation, summary aggregation                                                                                                                                        |
| test_enrichment_audit.py         | 10    | Enrichment: audit trail, field-level tracking                                                                                                                                                               |
| test_enrichment_deterministic.py | 23    | Enrichment: deterministic extraction + Phase 12 validation                                                                                                                                                  |
| test_enrichment_eval.py          | 21    | Enrichment: retrieval eval + ablation (Phase 14/15)                                                                                                                                                         |
| test_multidomain_phase1.py       | 37    | Multi-domain Phase 1: legal_sections registry, collections, act_name payload, act-aware crossrefs, domain prompts, generic claims, collection threading                                                     |     | test_reingest_fssai.py | 15  | P1-4: reingest script load_corpus, build_payload identity, FSS-scope/backup guards, CLI exit semantics (offline, fakes) |
| test_remote_reranker.py          | 24    | Remote CE client: TEI + serverless modes, auth, URL normalization (incl. `.modal.run` root), lazy local fallback, `_build_reranker` RAG_RERANKER_ENDPOINT wiring                                            |
| test_remote_embedder.py          | 18    | RemoteEmbedClient: batched /embed, auth, URL normalization, fallback + DenseRetriever RAG_EMBED_ENDPOINT wiring                                                                                             |
| test_qdrant_bm25.py              | 13    | Qdrant-side BM25: search_sparse_text/hybrid_search_text (Qdrant/bm25), SparseRetriever server_bm25, HybridRetriever text fusion, RAG_QDRANT_BM25 flag                                                       |
| test_rag_agent_state.py          | 5     | M3: RAGState TypedDict schema, initial_state defaults/custom, JSON-serializability (M5 checkpointing-ready)                                                                                                 |
| test_rag_agent_nodes.py          | 17    | M3: classify (fallback→general), retrieve (expanded-query, query_type retention), evidence (flag on/off/error), generate, verify, expand_query (retry count, stub-LLM, failure keeps query), finalize merge |
| test_rag_agent_graph.py          | 12    | M3+M4: compile, node set, evidence-node flag, route_after_verify (threshold/retry budget), e2e grounded/retry/exhaust-retries flows                                                                         |
| test_rag_agent_routes.py         | 7     | M3: /api/rag/query/agent 400 validation, 503 RAG-disabled, flag-off delegation to legacy, flag-on agent path, collection/filters forwarding                                                                 |
| test_rag_agent_m5.py             | 15    | M5: review interrupt payload/resume value, checkpointer selection (memory/none/postgres-degrades), approved→finalize, rejected→retry→finalize, route 202→resume 200, resume validation/flag-off 400s        |
| test_plugins.py                  | 23    | Phase 20: PluginRegistry singleton, default registration, provider delegation (OCR/AI/Rules/PDF), lazy imports, backward compat                                                                             |

---

_End of agents.md_
