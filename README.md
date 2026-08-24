# NSA Webservice — Legal Intelligence Platform

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://postgresql.org)
[![Alembic](https://img.shields.io/badge/Alembic-Migrations-7B1FA2)](https://alembic.sqlalchemy.org)
[![Celery](https://img.shields.io/badge/Celery-5.x-37814A?logo=celery&logoColor=white)](https://celeryproject.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![pip-audit](https://github.com/sumanksaha/NSA_webservice/actions/workflows/pip-audit.yml/badge.svg)](https://github.com/sumanksaha/NSA_webservice/actions/workflows/pip-audit.yml)
[![Code style: black](https://img.shields.io/badge/Code%20Style-Black-000000)](https://github.com/psf/black)

**A government-grade workflow automation system for Food Safety Officer adjudication, inspection tracking, sample management, and legal document generation.**

</div>

---

## Table of Contents

- [Project Overview](#project-overview)
- [Architecture](#architecture)
- [Technology Stack](#technology-stack)
- [Current Status](#current-status)
- [Roadmap](#roadmap)
- [Installation](#installation)
- [Development](#development)
- [Testing](#testing)
- [Deployment](#deployment)
- [CI/CD](#cicd)
- [API Reference](#api-reference)
- [Contribution](#contribution)
- [Coding Standards](#coding-standards)
- [Security](#security)
- [Future Levels 1–10](#future-levels-1-10)
- [License](#license)

---

## Project Overview

NSA Webservice digitizes and automates the complete lifecycle of food safety legal proceedings under the **Food Safety and Standards Act, 2006 (FSS Act)**. It replaces paper-based workflows with a secure, auditable, and efficient digital platform used by Food Safety Officers (FSOs), adjudication officers, and administrators.

### Core Capabilities

| Module                            | Purpose                                                                                                                                                          |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Inspection Management**         | Record food business inspections, capture geo-tagged photo evidence, calculate compliance deadlines                                                              |
| **Sample Management**             | Track food sample collection, lab submission, analyst reports with unique code generation                                                                        |
| **Case File Generation**          | Generate legal case files for sample-based violations (misbranded, substandard food)                                                                             |
| **Adjudication**                  | Manage non-sample adjudication cases, section selection, legal document generation                                                                               |
| **FBO Issue Tracking**            | Unified state machine for Food Business Operator issues with audit trail                                                                                         |
| **Billing**                       | Summary dashboards and Excel export for sample billing                                                                                                           |
| **Document Generation**           | PDF generation for permission letters, petitions, and legal notices                                                                                              |
| **Timeline & Case Visualization** | Auto-generated milestone timelines + Gantt charts per case, with chronological-validity warnings; reachable from a global case picker and every case-linked page |
| **Food Cell (DO Intimation)**     | Designated-Officer intimation forwarding for samples — PDF/HTML view, regenerate, sync to Sheets/Airtable/Excel (Phase 21)                                       |
| **Legal RAG (Vector Search)**     | ✅ Phases 1-5 complete                                                                                                                                           | Full RAG pipeline: corpus/embedding, dense+sparse+hybrid retrieval, reranking, grounded generation, hallucination detection, evaluation (437 tests + 28 Agent A)                                                                                                                          |
| **Knowledge Graph + Neo4j**       | ✅ Phase 14 complete                                                                                                                                             | Full legal KG: corpus ingestion (58 instruments, 1,861 provisions, 27,343 chunks), semantic enrichment (751 edges), hybrid expansion (RRF k=60), Neo4j Aura sync with APOC/NEO4J_ALLOW_WRITE guard, interactive Cytoscape.js visualization (17+15 tests)                                  |
| **Evaluation Framework**          | ✅ Complete                                                                                                                                                      | RAG evaluation: retrieval arms A–G, metrics, ceiling analysis, batch orchestration (28 modules)                                                                                                                                                                                           |
| **Benchmark v1.0**                | ✅ Frozen                                                                                                                                                        | 150-question multi-domain golden benchmark with gold provisions, sources, rubric, review-conflict report                                                                                                                                                                                  |
| **Rust PyO3 Normalizers**         | ✅ Complete                                                                                                                                                      | Deterministic legal-text normalizers compiled via PyO3 for performance (4 modules)                                                                                                                                                                                                        |
| **FastAPI Gateway (ASGI)**        | ✅ Phases 1–5 complete                                                                                                                                           | Coexistence gateway: `asgi.py` hosts FastAPI (uvicorn) + Flask (WSGIMiddleware) in one process; `/api/v2/*` JSON APIs on FastAPI, Jinja2 UI stays on Flask. Security headers + API-key auth middleware. Deployed on Render. 57 tests. Phase 6 (full rewrite) deferred per AGENTS.md §1.2. |
| **Google Sheets Sync**            | Optional data synchronization with Google Sheets for external reporting                                                                                          |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                   PRESENTATION LAYER                             │
│     Jinja2 Templates · CSS · JavaScript (Vanilla JS)            │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                   GATEWAY LAYER (ASGI)                            │
│                  FastAPI + uvicorn (asgi.py)                      │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ /api/v2/health · /api/v2/rag/* · /api/v2/search ·          │ │
│  │ /api/v2/bill/lookup · /api/v2/validation/validate         │ │
│  └────────────────────────────────────────────────────────────┘ │
│                      a2wsgi.WSGIMiddleware                      │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                   APPLICATION LAYER (Flask)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │Inspection│ │  Sample  │ │Case File │ │Adjudicat.│ │ Billing│ │
│  │ Blueprint│ │ Blueprint│ │ Blueprint│ │ Blueprint│ │Blueprint│ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │
│  │FBO Issue │ │Settings  │ │  Auth    │ │  Audit (Hash-Chain)  │ │
│  │ Blueprint│ │ Blueprint│ │ Blueprint│ │  Event Listeners     │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘ │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                   SERVICE LAYER                                   │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────────────────┐ │
│  │ Shared Utils │ │ Google Sheets│ │  S3 Storage (R2/B2)      │ │
│  │ (Keys/Models)│ │ Sync Service │ │  Photo Upload/Delete     │ │
│  └──────────────┘ └──────────────┘ └───────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────────────────┐ │
│  │ PDF Generator│ │  Verification│ │  Code Sequence Generator │ │
│  │ (WeasyPrint) │ │  Services    │ │  (Atomic, Race-Safe)     │ │
│  └──────────────┘ └──────────────┘ └───────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                   DATA LAYER                                      │
│  ┌────────────────┐ ┌────────────────┐ ┌───────────────────────┐ │
│  │  PostgreSQL     │ │  SQLAlchemy    │ │  Redis (Celery       │ │
│  │  (Primary)      │ │  ORM + Alembic │ │  Message Broker)     │ │
│  └────────────────┘ └────────────────┘ └───────────────────────┘ │
│  ┌────────────────┐ ┌────────────────┐                            │
│  │  SQLite         │ │  Local DB      │                            │
│  │  (Dev Fallback) │ │  (license.db)  │                            │
│  └────────────────┘ └────────────────┘                            │
└───────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision                   | Rationale                                                                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **Blueprint-per-domain**   | Separate Flask blueprints for each functional domain enable independent development, testing, and future migration to microservices |
| **Canonical Key Contract** | `app/shared/case_keys.py` defines uniform field names across modules, preventing field-name drift as the platform evolves           |
| **Hash-Chained Audit**     | Tamper-evident `AuditLog` using SHA-256 prevents retroactive modification of records                                                |
| **Race-Safe Sequences**    | `CodeSequence` table with atomic increments and PostgreSQL advisory locks ensures unique codes across concurrent workers            |
| **Optimistic Concurrency** | `version_id` columns with `StaleDataError` handling prevent lost updates during concurrent edits                                    |
| **Storage Abstraction**    | S3-compatible interface (R2/B2) for photo evidence decouples storage from application logic                                         |

### RAG Remote Inference (Render free tier = zero local models)

Render's free tier (512 MB RAM / 0.1 CPU) cannot hold any torch model
(`all-mpnet-base-v2` alone is ~420 MB). Since 2026-08-16 the RAG query path
runs **no local models in production** — every model is hosted elsewhere
(details + deploy task in `task.md` ENV-10):

| Component                                       | Where it runs                                                                      | Config                                            |
| ----------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------- |
| Dense embeddings (`all-mpnet-base-v2`, 768-dim) | **Modal** serverless — `modal_deploy/app.py` → `https://<ws>--embed.modal.run`     | `RAG_EMBED_ENDPOINT`                              |
| CE reranker (`sumanksaha/Foodmultidomain`)      | **Modal** serverless — `https://<ws>--rerank.modal.run` (TEI-compatible `/rerank`) | `RAG_RERANKER_ENDPOINT` + `RAG_RERANKER_MODE=tei` |
| BM25 sparse                                     | **In-cluster by Qdrant** (`Qdrant/bm25` — no local fastembed)                      | `RAG_QDRANT_BM25=true`                            |
| sec_act rerank features                         | Local (pure Python, no torch)                                                      | `RAG_ENSEMBLE_RERANK=true`                        |

Client wiring: `app/rag/retrieval/remote_embedder.py` (`RemoteEmbedClient`,
injected into `DenseRetriever`), `app/rag/retrieval/remote_reranker.py`
(`RemoteRerankClient`, injected as the ensemble's CE encoder), and
`QdrantStore.search_sparse_text`/`hybrid_search_text` for server-side BM25.
Both remote clients lazily fall back to the local model when the endpoint
fails — set `RAG_*_REMOTE_FALLBACK=false` on Render so a failure degrades to
features-only/sparse-only instead of building torch (OOM).

> ⚠️ **HF Serverless Inference API is decommissioned** (410/404 since late
> 2025; Inference Providers serve an allowlisted catalog only) — the
> `mode="serverless"` path and `scripts/test_hf_inference.py` are dead ends.

Deploy the Modal app with `modal deploy app.py` from `modal_deploy/` (see
`modal_deploy/README.md`); live URLs as of 2026-08-16:
`https://sumanksaha--embed.modal.run`, `https://sumanksaha--rerank.modal.run`.

### RAG Agent Pipeline (LangGraph, opt-in)

Since 2026-08-16 the RAG query path also offers a **self-correcting
LangGraph agent** (M3+M4 — plan: `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md`
Part C; task: `task.md` ENV-11):

```
classify ──► retrieve ──► generate ──► verify ──► finalize ──► END
                  ▲                            │
                  └──── expand_query ◄─────────┘   (groundedness < 0.7, retries < 2)
```

- `POST /api/rag/query/agent` runs the graph; the node set is a thin
  adapter layer over the **same** pipeline services as the legacy route
  (retrieval with remote CE + Qdrant-side BM25, KG fusion, generation,
  hallucination detection), so the agent path cannot drift from the
  production baseline.
- When the response's groundedness is below **0.7** and the retry budget
  (default **2**) is not exhausted, the graph rewrites the query
  (`expand_query_node`, reusing `GroundedLLMClient`) and re-retrieves.
- Opt-in via `RAG_USE_AGENT_PIPELINE=true` — default `false`, in which
  case the endpoint delegates to the legacy pipeline and `/api/rag/query`
  is never affected. Requires `langgraph` (lazy import — the legacy path
  never touches it).
- **M5 — human-in-the-loop + checkpointing (2026-08-16):** with
  `RAG_AGENT_HITL=true` the graph pauses at a `review` interrupt before
  finalize — the route returns `202 awaiting_review` with a `thread_id`,
  and `POST /api/rag/query/agent/resume` (`{thread_id, approved}`)
  resumes it (approved → finalize; rejected → re-generate via
  expand-and-retry). Thread state lives in a checkpointer
  (`RAG_AGENT_CHECKPOINTER=memory` default / `postgres` for prod).
- **A/B (rollout §8):** `RAGQueryLog.pipeline` stamps every query
  `legacy`/`agent`; `scripts/ab_agent_vs_legacy.py` runs the frozen
  benchmark through both paths against the live stack.
- 56 tests across `tests/test_rag_agent_{state,nodes,graph,routes,m5}.py`
  - pipeline-field tests, all stub-LLM / no network.

---

## Technology Stack

### Current Stack

| Layer              | Technology                          | Version  | Purpose                                       |
| ------------------ | ----------------------------------- | -------- | --------------------------------------------- |
| **Runtime**        | Python                              | 3.12+    | Application runtime                           |
| **Web Framework**  | Flask                               | 2.x      | HTTP server and routing                       |
| **ORM**            | SQLAlchemy                          | 2.x      | Database abstraction                          |
| **Migrations**     | Alembic                             | 1.13+    | Schema version control                        |
| **Database**       | PostgreSQL (primary) / SQLite (dev) | 16 / 3.x | Data persistence                              |
| **Task Queue**     | Celery                              | 5.4+     | Async background jobs                         |
| **Message Broker** | Redis                               | 5.x      | Celery broker + cache                         |
| **PDF Generation** | WeasyPrint                          | —        | HTML-to-PDF rendering                         |
| **Excel Export**   | openpyxl                            | —        | Billing reports                               |
| **Object Storage** | Cloudflare R2 / Backblaze B2        | —        | Photo evidence storage                        |
| **Auth**           | Flask-Login                         | 0.6+     | Session-based authentication                  |
| **Security**       | Flask-Talisman                      | 1.1+     | CSP, HSTS, secure headers                     |
| **OCR**            | Tesseract (pytesseract)             | —        | Text extraction from images                   |
| **Vector Store**   | Qdrant                              | latest   | Semantic search over legal corpus (768-dim)   |
| **Embeddings**     | sentence-transformers               | latest   | `all-mpnet-base-v2` (768-dim)                 |
| **Fuzzy Matching** | rapidfuzz                           | —        | Sparse retrieval + fuzzy fallback             |
| **Templates**      | Jinja2                              | —        | Server-side HTML rendering                    |
| **Rust (PyO3)**    | Rust 1.75+ / PyO3                   | —        | Native legal-text normalizers for performance |
| **Graph Database** | Neo4j Aura                          | v5.27    | Legal KG (provisions, instruments, domains)   |

### Target Stack (Levels 5–10)

| Layer                | Target Technology       |
| -------------------- | ----------------------- |
| **Web Framework**    | FastAPI                 |
| **Graph Database**   | Neo4j                   |
| **Vector Store**     | Qdrant                  | ✅ Phase 1 (Agent A) |
| **Orchestration**    | LangGraph               |
| **LLM Gateway**      | OpenRouter              |
| **Containerization** | Docker + Docker Compose |
| **Monitoring**       | Prometheus + Grafana    |

## Project Status & Capabilities

**Version:** 0.8.0 (Pre‑Production)

The NSA Webservice now offers a comprehensive, end‑to‑end solution for food safety inspections, sample management, adjudication, and reporting. Key capabilities include:

- **Inspection Management** with photo verification and geo‑tagging.
- **Sample Tracking** with unique code generation, lab submission, and analyst reporting.
- **Case File Generation** delivering PDF documents via WeasyPrint and async processing with Celery.
- **Adjudication Engine** that suggests legal sections and generates adjudication documents.
- **FBO Issue State Machine** with full audit‑trail logging.
- **Billing Dashboard** exporting Excel reports.
- **Robust Authentication** (Flask‑Login) and **Security Hardening** (CSP, HSTS, CSRF, session hardening, TLS verification, CI/CD security scanning).
- **Hash‑Chained Audit Log** for tamper‑evident record keeping.
- **Timeline Engine + Gantt** visualizing each case's milestones with warnings for chronologically invalid sequences (Phase 13).
- **Full‑text + fuzzy search** across case files, adjudications, annexures, and evidence (SQLite FTS5 + RapidFuzz).
- **Version history, branching, cross‑reference & TOC reports** for edited documents, and **backup / export / import** of complete cases.
- **OCR extraction pipeline foundation** (models + services + Celery task) toward lab‑report autopopulation.
- **Food Cell DO Intimation workflow** (Phase 21) forwarding samples to the Designated Officer.
- **Legal RAG vector search** (694 tests) — full RAG pipeline: corpus/embedding, dense+sparse+hybrid retrieval, reranking, grounded generation, hallucination detection, evaluation, LangGraph self-correcting agent with M5 checkpointing + human-in-the-loop.
- **Knowledge graph with Neo4j Aura** — entity/relationship extraction from case files with interactive Cytoscape.js visualization and optional Neo4j sync using APOC dynamic labels, uniqueness constraints, and property indexes (Phase 14 complete — 17+15 tests).

| Area                      | Status         | Notes                                                                                                                                                                                                                                                                              |
| ------------------------- | -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Inspection CRUD           | ✅ Complete    | With photo verification pipeline                                                                                                                                                                                                                                                   |
| Sample Management         | ✅ Complete    | Code generation, lab tracking                                                                                                                                                                                                                                                      |
| Case File Generation      | ✅ Complete    | PDF generation, Celery async                                                                                                                                                                                                                                                       |
| Adjudication              | ✅ Complete    | Section suggestion, document generation                                                                                                                                                                                                                                            |
| FBO Issue State Machine   | ✅ Complete    | With audit trail                                                                                                                                                                                                                                                                   |
| Billing Dashboard         | ✅ Complete    | Excel export, filtering                                                                                                                                                                                                                                                            |
| Authentication            | ✅ Complete    | Flask‑Login, global gate                                                                                                                                                                                                                                                           |
| Audit Trail               | ✅ Complete    | Hash‑chained + RecordAudit                                                                                                                                                                                                                                                         |
| Security Hardening        | ✅ Complete    | CSP, HSTS, CSRF, session hardening                                                                                                                                                                                                                                                 |
| Timeline Engine + Gantt   | ✅ Complete    | Phase 13 — 21 tests, global picker + entry points                                                                                                                                                                                                                                  |
| Search (FTS5 + fuzzy)     | ✅ Complete    | Phase 10 — 56 tests                                                                                                                                                                                                                                                                |
| Version Control           | ✅ Complete    | Compare/restore/branch, history UI                                                                                                                                                                                                                                                 |
| Backup / Export / Import  | ✅ Complete    | Phase 16 — JSON/ZIP export, case import                                                                                                                                                                                                                                            |
| OCR Pipeline              | ✅ Complete    | Phases A–E (extraction → review → autopopulation → feedback → bulk upload — 45 tests)                                                                                                                                                                                              |
| Food Cell (DO Intimation) | ✅ Complete    | Phase 21 – 15 tests                                                                                                                                                                                                                                                                |
| Legal RAG (Phases 1-5)    | ✅ Complete    | 437 tests — full pipeline incl. generation, verification, eval                                                                                                                                                                                                                     |
| Knowledge Graph           | ✅ Complete    | Full KG: corpus ingestion, semantic, hybrid, Neo4j Aura (17+15 tests)                                                                                                                                                                                                              |
| Evaluation Framework      | ✅ Complete    | 28 modules — retrieval arms, metrics, reports                                                                                                                                                                                                                                      |
| Benchmark v1.0            | ✅ Frozen      | 150-question multi-domain golden benchmark                                                                                                                                                                                                                                         |
| Rust PyO3 Normalizers     | ✅ Complete    | PyO3 legal-text normalizers (4 modules)                                                                                                                                                                                                                                            |
| CI/CD                     | ✅ Complete    | 14 gates (G1–G14): deploy gating, staging env, pre-deploy migrations, health check, full security blocking (Bandit+Safety+pip-audit), coverage gate, Docker path, release automation, Dependabot, workflow hygiene, ce-v2 gate, env parity, deploy serialization, dev-dep scanning |
| RBAC / Roles              | ⚠️ Partial     | Role/UserRole/Comment models + migration + `is_admin` admin UI done; `@role_required` + comment API/UI + role assignment pending (~30%)                                                                                                                                            |
| PostgreSQL Migration      | ⚠️ In Progress | Schema ready; Supabase migration prepped — pooler-safe engine options + `scripts/migrate_render_to_supabase.sh` (Render → Supabase) |                                                                                                                                                                                                                                                   |
| Tests                     | ✅ 90+ modules | ~1,900 test cases (694 RAG + 57 ASGI + 46 CI/CD gates + other), all passing                                                                                                                                                                                                        |
| Plugin Architecture       | ✅ Complete    | Registry-based provider plugins (OCR/AI/Rules/PDF) with lazy imports, config-driven selection, all 6 callers refactored (23 tests)                                                                                                                                                 |

---

## Future Scope & Roadmap

### Phase 1 – Hardening (Q3 2026)

- ✅ PostgreSQL production migration (targeted for Q3 2026)
- ✅ Persistent Celery worker deployment
- ✅ RBAC implementation (FSO, Admin, Auditor roles) — model scaffolding + migration + `is_admin` admin UI; `@role_required` decorator + comments + role assignment pending (~30% complete)

### Phase 2 – Platform Upgrade (Q4 2026)

- FastAPI migration for async APIs
- OpenAPI/Swagger documentation
- Structured logging with `structlog`
- Monitoring (Prometheus + Grafana) and Sentry error tracking

### Phase 3 – Intelligence (Q1 2027)

- Neo4j graph database integration for relationship queries
- ✅ Qdrant vector store for semantic search over legal corpus (RAG Phases 1-5 complete — 694 tests)
- LangGraph workflow orchestration
- OpenRouter multi‑LLM gateway for AI‑assisted section suggestion and document drafting

### Phase 4 – Enterprise (Q2 2027)

- Bulk operations and multi‑tenancy support
- Advanced pattern detection across cases
- Automated document drafting and continuous learning from adjudication outcomes

---

**Note:** The MyPy configuration now excludes the `build/` directory and stray `nul` file to avoid duplicate module errors (`exclude = "^(build/|nul)$"`).

## Roadmap

### Phase 1: Hardening (Q3 2026)

- [ ] PostgreSQL production migration
- [ ] Persistent Celery worker deployment
- [x] RBAC implementation (FSO, Admin, Auditor roles) — partial (models + migration + admin UI done)
- [ ] TLS fix for KMC scraper
- [ ] End-to-end test suite
- [x] Docker containerization

### Phase 2: Platform Upgrade (Q4 2026)

- [x] FastAPI migration (ASGI coexistence gateway — `asgi.py`, `/api/v2/*`; Phase 6 full rewrite deferred)
- [x] OpenAPI / Swagger documentation (flasgger `/apidocs/`)
- [x] Structured logging (structlog)
- [ ] Monitoring (Sentry + Prometheus)
- [ ] Redis caching layer
- [x] Health check endpoints (`GET /health`)

### Phase 3: Intelligence (Q1 2027)

- [x] Neo4j graph database integration (Phase 14 — APOC dynamic labels, constraints, indexes, QStash async sync, 15 tests)
- [x] Entity relationship queries (Phase 14 — entity/relationship extraction + sync to Aura)
- [x] Qdrant vector store for semantic search (Phase 1 complete — 282 tests)
- [ ] LangGraph workflow orchestration
- [ ] OpenRouter multi-LLM gateway

### Phase 4: Enterprise (Q2 2027)

- [ ] AI-powered section suggestion
- [ ] Document drafting assistance
- [ ] Pattern detection across cases
- [ ] Bulk operations
- [ ] Multi-tenancy

---

## Installation

### Prerequisites

- Python 3.12+
- PostgreSQL 16+ (or SQLite for development)
- Redis 5.0+ (for Celery)
- GTK libraries (for WeasyPrint — see [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation))

### Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/sumanksaha/NSA_webservice.git
cd NSA_webservice

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Chromium for Playwright (if using browser features)
playwright install chromium

# 5. Configure environment
cp .env.example .env   # Create .env file
# Edit .env with your settings (DATABASE_URL, SECRET_KEY, etc.)

# 6. Initialize the database
flask db upgrade

# 7. Run the application
flask run
```

### Docker (Planned)

> **Note:** Docker Compose configuration is not yet available. This section is a placeholder for the planned containerization effort (see [Roadmap](#roadmap)).

```bash
# (Coming in Phase 1 — Docker containerization)
# Build and run
docker compose up -d

# Run migrations
docker compose exec web flask db upgrade
```

---

## Development

### Project Structure

```
NSA_webservice/
├── app/                        # Application package
│   ├── __init__.py             # App factory
│   ├── extensions.py           # Flask extension initialization
│   ├── models/                 # SQLAlchemy models (modular: auth, document, inspection, billing, config, issue)
│   ├── audit_hooks.py          # SQLAlchemy event listeners
│   ├── adjudication/           # Adjudication blueprint
│   ├── audit/                  # Audit log viewer blueprint
│   ├── auth/                   # Authentication blueprint
│   ├── billing/                # Billing blueprint
│   ├── bill_generator/         # Bill generation blueprint
│   ├── case_file_generator/    # Case file blueprint
│   ├── fbo_issue/              # FBO issue tracking blueprint
│   ├── plugins/                # Phase 20: plugin architecture (base, registry, ocr/ai/rules/pdf plugins)
│   ├── sync/                   # Phase 17: Supabase sync blueprint (models, routes, supabase_sync service)
│   ├── inspection/             # Inspection blueprint
│   ├── sample/                 # Sample management blueprint
│   ├── services/               # Shared services
│   ├── settings/               # Settings blueprint
│   ├── shared/                 # Shared contracts and helpers
│   ├── static/                 # Static assets (CSS, JS)
│   ├── templates/              # Base templates
│   └── utils/                  # Utility modules
├── migrations/                 # Alembic database migrations
├── tests/                      # Test suite
├── kg/                         # Legal Knowledge Graph (schema, ingestion, enrichment, hybrid)
├── evaluation/                 # RAG evaluation framework (retrieval arms, metrics, reports)
├── benchmark/                  # Frozen v1.0 benchmark (150-question multi-domain JSONL)
├── rust/                       # Rust PyO3 legal-text normalizers
├── scripts/                    # Utility scripts (KG, FSSAI re-ingest, etc.)
├── docs/                       # Documentation (DEEPENING, MULTIDOMAIN, etc.)
├── celery_app.py               # Celery application factory
├── render.yaml                 # Render deployment blueprint
├── asgi.py                     # ASGI entry point (FastAPI + Flask coexistence gateway)
├── requirements.txt            # Python dependencies
├── fso_list.md                 # FSO master data
├── fss_sections.md             # FSS Act legal sections
└── app.py                      # WSGI entry point (Flask)
```

### Workflow

1. **Create a feature branch** from `upgradation`
2. **Make changes** following [coding standards](#coding-standards)
3. **Write tests** for new functionality
4. **Run tests** locally: `pytest`
5. **Run linter**: `black --check . && ruff check .`
6. **Commit** with conventional commits
7. **Push** and create a pull request

---

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific module tests
pytest tests/test_step1.py -v
pytest tests/test_step2.py -v

# Run with coverage report
pytest --cov=app --cov-report=term-missing

# Run route collision regression
pytest tests/test_route_collisions.py -v
```

### Test Structure

| Test File                         | Coverage                                                                          |
| --------------------------------- | --------------------------------------------------------------------------------- |
| `test_step1.py`                   | FSO model, markdown sync, Sample model, code generation                           |
| `test_step2.py`                   | Billing utilities, Excel export, filtering                                        |
| `test_step3.py`                   | Inspection model, code generation, deadline calculation                           |
| `test_step4.py`                   | Derived-state queries, dismiss action, adjudication linkage                       |
| `test_step5_integration.py`       | Cross-module integration scenarios                                                |
| `test_route_collisions.py`        | Regression: duplicate route detection                                             |
| `test_bill_generator.py`          | Bill generation logic                                                             |
| `test_pdf_photo_embedding.py`     | PDF photo embedding edge cases                                                    |
| `test_timeline.py`                | Phase 13: timeline engine, routes, picker, entry points (21)                      |
| `test_case_backup.py`             | Phase 16: JSON/ZIP export, case import (14)                                       |
| `test_ocr_extraction.py`          | Phase A: OCR extraction + task persistence (14)                                   |
| RAG corpus/embedding tests        | 20 files, 254 tests                                                               | Qdrant, embeddings, chunker, indexer, dedup, pipeline, adapters, quality  | ✅ All pass |
| RAG retrieval tests               | 8 files, 102 tests                                                                | Dense, sparse, hybrid, reranker, query classifier, logger, e2e            | ✅ All pass |
| `test_food_cell_do_intimation.py` | Phase 21: DO intimation generate/forward/sync (15)                                |
| `test_plugins.py`                 | Phase 20: PluginRegistry, provider delegation, lazy imports, backward compat (23) |
| RAG Phase 1 tests                 | 20 files, 254 tests                                                               | Qdrant, embeddings, chunker, indexer, dedup, pipeline, adapters, quality  | ✅ All pass |
| RAG Phase 2–5 tests               | 15 files, 156 tests                                                               | Generation, verification, hallucination, eval, resilient, hybrid-vs-dense | ✅ All pass |
| RAG Agent A tests                 | 4 files, 27 tests                                                                 | Corpus E2E, batch ingestion, reindexing, benchmarks                       | ✅ All pass |
| Multi-domain tests                | 2 files, 37 tests                                                                 | legal_sections, collections, act_name, domain prompts                     | ✅ All pass |
| KG tests                          | 8 files, 49 tests                                                                 | Corpus, enricher, expander, provisions, payload, fusion RRF               | ✅ All pass |
| FSSAI re-ingest tests             | 1 file, 15 tests                                                                  | load_corpus, identity, FSS-scope/backup guards, CLI                       | ✅ All pass |
| Rust normalizer tests             | 1 file                                                                            | PyO3 legal-text normalizers                                               | ✅ All pass |

---

## Deployment

### Render (Current)

The project includes a `render.yaml` blueprint for one-click deployment on Render.

```bash
# 1. Push to GitHub
git push origin main

# 2. Deploy via Render Blueprint
# Render Dashboard → New → Blueprint → Select repository
```

See [POSTGRES_MIGRATION.md](POSTGRES_MIGRATION.md) for detailed deployment instructions.

### Manual Deployment

```bash
# Build steps
pip install -r requirements.txt
flask db upgrade

# Run with Gunicorn (production)
gunicorn --bind 0.0.0.0:10000 app:app

# Run with Celery worker (background tasks)
celery -A celery_app.celery worker --loglevel=info
```

### Environment Variables

| Variable                  | Required            | Description                                   |
| ------------------------- | ------------------- | --------------------------------------------- |
| `DATABASE_URL`            | Yes                 | PostgreSQL connection string                  |
| `SECRET_KEY`              | Yes                 | Flask secret key (min 32 chars)               |
| `REDIS_URL`               | For Celery          | Redis connection string                       |
| `GOOGLE_CREDENTIALS_JSON` | For Sheets          | Google service account JSON                   |
| `SPREADSHEET_ID`          | For Sheets          | Google Sheets document ID                     |
| `R2_ACCESS_KEY`           | For Storage         | R2/B2 access key                              |
| `R2_SECRET_KEY`           | For Storage         | R2/B2 secret key                              |
| `R2_BUCKET`               | For Storage         | Storage bucket name                           |
| `R2_ENDPOINT`             | For Storage         | Storage endpoint URL                          |
| `SKIP_FSO_STARTUP_SYNC`   | No                  | Skip FSO sync on startup                      |
| `AIRTABLE_API_KEY`        | For Airtable backup | Airtable API key                              |
| `AIRTABLE_BASE_ID`        | For Airtable backup | Airtable base ID (auto-rotates when full)     |
| `MS_TENANT_ID`            | For Excel backup    | Azure AD tenant ID                            |
| `MS_CLIENT_ID`            | For Excel backup    | Azure AD app registration ID                  |
| `MS_CLIENT_SECRET`        | For Excel backup    | Azure AD client secret                        |
| `MS_DRIVE_ID`             | For Excel backup    | OneDrive/SharePoint drive ID                  |
| `MS_SPREADSHEET_ID`       | For Excel backup    | Excel file ID in OneDrive                     |
| `OCR_PROVIDER`            | Phase 20            | Active OCR provider (default: easyocr)        |
| `AI_PROVIDER`             | Phase 20            | Active AI provider (default: openrouter)      |
| `RULES_PROVIDER`          | Phase 20            | Active rule provider (default: fssai_default) |
| `PDF_PROVIDER`            | Phase 20            | Active PDF provider (default: weasyprint)     |

---

## CI/CD

The project implements **14 CI/CD gates (G1–G14)** — all complete and verified
by `tests/test_cicd_gates.py` (46 structural tests). The full gate inventory
lives in [`docs/CI_CD_RESEARCH.md`](docs/CI_CD_RESEARCH.md); the test file is
the regression shield.

| Gate | Name                   | What it does                                                                | Verified by                         |
| ---- | ---------------------- | --------------------------------------------------------------------------- | ----------------------------------- |
| G1   | Deploy gating          | `deploy.yml` triggers only after a green "Repository Validation" run        | `TestDeployGating` (4)              |
| G2   | Staging environment    | `deploy_staging` → staging GitHub env + Render staging service on `main`    | `TestStagingEnvironment` (11)       |
| G3   | Migrations             | `preDeployCommand: flask db upgrade` on web + staging services              | `TestRenderHealthAndMigrations` (5) |
| G4   | Health check           | `healthCheckPath: /health` on web + staging                                 | `TestRenderHealthAndMigrations` (5) |
| G5   | Full security blocking | Bandit (HIGH/HIGH), Safety, pip-audit — all blocking in `validation.yml`    | `TestSecurityGates` (3)             |
| G6   | Coverage gate          | `fail_under = 60` (slow shard only)                                         | `TestCoverageGate` (2)              |
| G7   | Docker path            | `ENTRYPOINT` + `CMD → uvicorn asgi:app` (ASGI)                              | `TestDockerConsistency` (4)         |
| G8   | Release automation     | `release.yml` — `push: tags` + `workflow_dispatch` → `gh-release@v2`        | `TestReleaseWorkflow` (4)           |
| G9   | Dependabot             | `pip` + `github-actions` + `npm` ecosystems, `rebase-strategy: all`         | `TestDependabot` (1)                |
| G10  | Workflow hygiene       | checkout@v7, setup-python@v7, ruff≥0.16.3, ubuntu-24.04, concurrency groups | `TestWorkflowHygiene` (4)           |
| G11  | ce-v2 gate             | `real-gate` job only runs on `workflow_dispatch`                            | `TestCeV2Gate` (1)                  |
| G12  | Env parity             | `shared-secrets` envVarGroup (single `SECRET_KEY`), worker parity verified  | `TestEnvParity` (3)                 |
| G13  | Deploy serialization   | `concurrency: { group: render-deploy }` in `deploy.yml`                     | `TestDeployGating` (4)              |
| G14  | Dev dep scanning       | pip-audit scans `requirements-dev.txt` in validation + weekly pip-audit.yml | `TestSecurityGates` (3)             |

### Deploy flow

```
1. PR → CI: lint + ruff + test-fast + Bandit + Safety + pip-audit
2. Merge to main → CI: test-slow (with coverage, fail_under=60)
3. On green "Repository Validation" → deploy.yml workflow_run:
   a. deploy_staging → staging environment (Render staging service, `main`)
   b. deploy → production (only if staging succeeds)
4. Tags (`v*.*.*`) → release.yml → GitHub Release (auto-notes)
```

> **Setup required for first deploy:** render.yaml `autoDeploy: false` means
> Render won't auto-deploy on push — the `deploy.yml` workflow curls the deploy
> hook pinned to the validated SHA. Create the Render Deploy Hook and store as
> `RENDER_DEPLOY_HOOK_URL` (production) and `RENDER_STAGING_DEPLOY_HOOK_URL`
> (staging) repo secrets.

### Security scanning

| Scanner       | Config file         | Scope              | Threshold | Blocking? |
| ------------- | ------------------- | ------------------ | --------- | --------- |
| **Bandit**    | `pyproject.toml`    | `app/`             | HIGH/HIGH | ✅ Yes    |
| **Safety**    | —                   | `requirements.txt` | any       | ✅ Yes    |
| **pip-audit** | `requirements*.txt` | All dependencies   | any       | ✅ Yes    |

Bandit skips `B101` (assert), `B311` (random), `B324` (hashlib) as known false
positives. SARIF results upload to GitHub Code Scanning but remain
`continue-on-error` so reporting never masks scan failures.

---

## API Reference

> **Note:** API documentation is auto-generated from code. Endpoints follow a RESTful convention.

### Blueprint Prefixes

| Blueprint           | Prefix                 | Description                                                                |
| ------------------- | ---------------------- | -------------------------------------------------------------------------- |
| Auth                | `/auth`                | Login/logout, first-setup admin bootstrap, admin user management (create/toggle-admin/reset-password/delete), self-service password change |
| Inspection          | `/inspection`          | Inspection CRUD + photo evidence                                           |
| Sample              | `/sample`              | Sample management                                                          |
| Case File           | `/case_file_generator` | Case file generation                                                       |
| Adjudication        | `/adjudication`        | Adjudication management                                                    |
| Billing             | `/billing`             | Billing summary + export                                                   |
| Bill Generator      | `/bill_generator`      | Bill PDF (async via QStash)                                                |
| FBO Issue           | `/fbo-issue`           | FBO issue state machine                                                    |
| Annexure            | `/annexure`            | Annexure upload + metadata                                                 |
| Evidence            | `/evidence`            | Evidence library (photos, reports, etc.)                                   |
| Document Viewer     | `/document_viewer`     | Quill editor, save/restore, PDF                                            |
| Legal Analysis      | `/legal`               | Legal paragraph detection workbench                                        |
| Search              | `/search`              | FTS5 + fuzzy search API                                                    |
| Version Control     | `/api/version-control` | Version history UI + API                                                   |
| Timeline            | `/timeline`            | Case milestone timeline + Gantt                                            |
| Food Cell           | `/food-cell`           | DO Intimation workflow (Phase 21)                                          |
| **RAG**             | `/rag`                 | RAG health + retrieval + generation + evaluation API (Phases 1-5 complete) |
| **Knowledge Graph** | `/knowledge-graph`     | Entity/relationship graph + Neo4j sync (Phase 14 complete)                 |
| Audit               | `/admin`               | Audit log viewer                                                           |
| Settings            | `/settings`            | Admin settings                                                             |
| Health              | `/health`              | Health probe (public)                                                      |
| Sync (Phase 17)     | `/sync`                | Supabase PostgreSQL sync dashboard (pooler-safe engine, keepalive)         |

### Response Format

All API endpoints return JSON with consistent status codes:

- `200` — Success
- `201` — Created
- `204` — Deleted (no content)
- `400` — Bad request
- `404` — Not found
- `409` — Conflict (optimistic locking)
- `500` — Server error

---

## Coding Standards

### Python

- **Style**: [Black](https://github.com/psf/black) with 120-character line length
- **Linting**: [Ruff](https://github.com/astral-sh/ruff) — strict ruleset
- **Type Hints**: Required for all function signatures (PEP 484)
- **Docstrings**: Google style docstrings for all modules, classes, and functions
- **Imports**: Grouped (standard library → third-party → local), alphabetically sorted

### Naming Conventions

| Element    | Convention   | Example                          |
| ---------- | ------------ | -------------------------------- |
| Modules    | `snake_case` | `inspection_utils.py`            |
| Classes    | `PascalCase` | `class InspectionPhoto`          |
| Functions  | `snake_case` | `def generate_inspection_code()` |
| Variables  | `snake_case` | `compliance_deadline`            |
| Constants  | `UPPER_CASE` | `MAX_FILE_SIZE`                  |
| DB Columns | `snake_case` | `food_safety_officer_name`       |
| Blueprints | `snake_case` | `inspection_bp`                  |

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add inspection photo verification pipeline
fix: handle StaleDataError in inspection update route
docs: update deployment guide for PostgreSQL
test: add boundary tests for compliance deadline calculation
refactor: extract date parsing into shared utility
chore: update ruff configuration in pyproject.toml
```

---

## Contribution

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

---

## Security

Please read [SECURITY.md](SECURITY.md) for security vulnerability reporting and our responsible disclosure policy.

---

## Future Levels 1–10

### Level 1: Foundation ✅

- Flask web framework with blueprints
- SQLAlchemy ORM with Alembic migrations
- Basic authentication (Flask-Login)
- Core inspection, sample, adjudication CRUD

### Level 2: Integration ✅

- Google Sheets sync
- PDF document generation (WeasyPrint)
- Celery background tasks
- S3-compatible object storage (R2/B2)

### Level 3: Security ✅

- Flask-Talisman (CSP, HSTS, secure cookies)
- CSRF protection (flask-wtf)
- Session hardening (30min TTL, HttpOnly, SameSite)
- Hash-chained audit logging
- Optimistic concurrency control (`StaleDataError → 409`)
- TLS certificate verification on all external lookups (S7)
- CI/CD security scanning: Bandit (HIGH/HIGH), Safety, pip-audit — all blocking (G5)
- Dependency scanning: Dependabot (pip + github-actions + npm)

### Level 4: Testing & Validation ✅

- Module-specific pytest suite
- Route collision regression guard
- Code generation with race-safe sequences
- Photo evidence verification pipeline (EXIF, IP geo, distance check)

### Level 5: Database & Scale 🔄 In Progress

- PostgreSQL production migration
- Connection pooling
- Database indexes optimization
- Query performance tuning (N+1 fixes)

### Level 6: API & Architecture ⬜ Planned

- FastAPI migration
- OpenAPI/Swagger documentation
- Dependency injection
- Async request handling

### Level 7: Observability ⬜ Planned

- Structured logging (structlog)
- Monitoring (Prometheus + Grafana)
- Error tracking (Sentry)
- Health check endpoints
- Distributed tracing

### Level 8: Graph & Knowledge ⬜ Planned

- Neo4j graph database
- Entity relationship mapping (FSO→FBO→Case→Section)
- Graph-based pattern detection
- Case similarity queries

### Level 9: Intelligence ✅ Complete

- ✅ Qdrant vector store integration (`app/rag/qdrant_client.py`)
- ✅ Semantic search over legal corpus (Dense + Sparse + Hybrid + Reranker)
- ✅ Grounded LLM generation with citation tracking (`/api/rag/generate`)
- ✅ Hallucination detection (ClaimExtractor, EvidenceVerifier, GroundednessScorer)
- ✅ Full evaluation framework (6 metrics, batch orchestration, `/api/rag/eval`)
- ✅ Resilient integration with circuit breaker + fallback (`/api/rag/query`)
- ✅ Multi-domain corpus (5 domains; env, commercial, animal, wb_state, criminal)
- ✅ Knowledge graph with Neo4j Aura (corpus ingestion, semantic enrichment, hybrid expansion)
- ✅ Rust PyO3 normalizers for performance-critical text processing
- ✅ Benchmark v1.0 frozen (150-question multi-domain golden benchmark)
- 🔄 LLM-powered section suggestion & document drafting (requires OpenRouter gateway)

### Level 10: Autonomy ⬜ Planned

- LangGraph workflow orchestration
- OpenRouter multi-LLM gateway
- Agentic adjudication pipeline
- Automated document drafting
- Continuous learning from adjudication outcomes

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

**Why MIT?** As a public-sector digital platform for food safety adjudication, MIT was chosen because:

- **Maximum adoption**: Other government bodies and jurisdictions can reuse and adapt the software without legal friction
- **Transparency**: Public sector technology benefits from permissive licensing
- **Compatibility**: Fully compatible with all project dependencies (MIT, BSD, Apache-2.0)
- **Simplicity**: MIT is one of the simplest, most widely understood licenses
- **No restrictions**: Allows commercial use, modification, distribution, and private use

---

<div align="center">
  <sub>Built for the Food Safety & Standards Authority of India | FSS Act, 2006</sub>
</div>
