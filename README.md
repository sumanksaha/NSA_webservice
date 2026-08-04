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

| Module | Purpose |
|--------|---------|
| **Inspection Management** | Record food business inspections, capture geo-tagged photo evidence, calculate compliance deadlines |
| **Sample Management** | Track food sample collection, lab submission, analyst reports with unique code generation |
| **Case File Generation** | Generate legal case files for sample-based violations (misbranded, substandard food) |
| **Adjudication** | Manage non-sample adjudication cases, section selection, legal document generation |
| **FBO Issue Tracking** | Unified state machine for Food Business Operator issues with audit trail |
| **Billing** | Summary dashboards and Excel export for sample billing |
| **Document Generation** | PDF generation for permission letters, petitions, and legal notices |
| **Audit Trail** | Tamper-evident hash-chained audit logging for all records and photo evidence |
| **Google Sheets Sync** | Optional data synchronization with Google Sheets for external reporting |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                   PRESENTATION LAYER                             │
│     Jinja2 Templates · CSS · JavaScript (Vanilla JS)            │
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

| Decision | Rationale |
|----------|-----------|
| **Blueprint-per-domain** | Separate Flask blueprints for each functional domain enable independent development, testing, and future migration to microservices |
| **Canonical Key Contract** | `app/shared/case_keys.py` defines uniform field names across modules, preventing field-name drift as the platform evolves |
| **Hash-Chained Audit** | Tamper-evident `AuditLog` using SHA-256 prevents retroactive modification of records |
| **Race-Safe Sequences** | `CodeSequence` table with atomic increments and PostgreSQL advisory locks ensures unique codes across concurrent workers |
| **Optimistic Concurrency** | `version_id` columns with `StaleDataError` handling prevent lost updates during concurrent edits |
| **Storage Abstraction** | S3-compatible interface (R2/B2) for photo evidence decouples storage from application logic |

---

## Technology Stack

### Current Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **Runtime** | Python | 3.12+ | Application runtime |
| **Web Framework** | Flask | 2.x | HTTP server and routing |
| **ORM** | SQLAlchemy | 2.x | Database abstraction |
| **Migrations** | Alembic | 1.13+ | Schema version control |
| **Database** | PostgreSQL (primary) / SQLite (dev) | 16 / 3.x | Data persistence |
| **Task Queue** | Celery | 5.4+ | Async background jobs |
| **Message Broker** | Redis | 5.x | Celery broker + cache |
| **PDF Generation** | WeasyPrint | — | HTML-to-PDF rendering |
| **Excel Export** | openpyxl | — | Billing reports |
| **Object Storage** | Cloudflare R2 / Backblaze B2 | — | Photo evidence storage |
| **Auth** | Flask-Login | 0.6+ | Session-based authentication |
| **Security** | Flask-Talisman | 1.1+ | CSP, HSTS, secure headers |
| **OCR** | Tesseract (pytesseract) | — | Text extraction from images |
| **Templates** | Jinja2 | — | Server-side HTML rendering |

### Target Stack (Levels 5–10)

| Layer | Target Technology |
|-------|-------------------|
| **Web Framework** | FastAPI |
| **Graph Database** | Neo4j |
| **Vector Store** | Qdrant |
| **Orchestration** | LangGraph |
| **LLM Gateway** | OpenRouter |
| **Containerization** | Docker + Docker Compose |
| **Monitoring** | Prometheus + Grafana |

---

## Current Status

**Version:** 0.8.0 (Pre-Production)

| Area | Status | Notes |
|------|--------|-------|
| Inspection CRUD | ✅ Complete | With photo verification pipeline |
| Sample Management | ✅ Complete | Code generation, lab tracking |
| Case File Generation | ✅ Complete | PDF generation, Celery async |
| Adjudication | ✅ Complete | Section suggestion, document generation |
| FBO Issue State Machine | ✅ Complete | With audit trail |
| Billing Dashboard | ✅ Complete | Excel export, filtering |
| Authentication | ✅ Complete | Flask-Login, global gate |
| Audit Trail | ✅ Complete | Hash-chained + RecordAudit |
| Security Hardening | ✅ Complete | CSP, HSTS, CSRF, session hardening |
| CI/CD | ⚠️ Partial | pip-audit + Dependabot configured |
| RBAC / Roles | ❌ Not Started | All users have full access |
| PostgreSQL Migration | ⚠️ In Progress | Schema ready, production pending |
| Tests | ⚠️ Partial | Module-specific, no end-to-end |

---

## Roadmap

### Phase 1: Hardening (Q3 2026)
- [ ] PostgreSQL production migration
- [ ] Persistent Celery worker deployment
- [ ] RBAC implementation (FSO, Admin, Auditor roles)
- [ ] TLS fix for KMC scraper
- [ ] End-to-end test suite
- [ ] Docker containerization

### Phase 2: Platform Upgrade (Q4 2026)
- [ ] FastAPI migration
- [ ] OpenAPI / Swagger documentation
- [ ] Structured logging (structlog)
- [ ] Monitoring (Sentry + Prometheus)
- [ ] Redis caching layer
- [ ] Health check endpoints

### Phase 3: Intelligence (Q1 2027)
- [ ] Neo4j graph database integration
- [ ] Entity relationship queries
- [ ] Qdrant vector store for semantic search
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
├── scripts/                    # Utility scripts
├── docs/                       # Documentation
├── celery_app.py               # Celery application factory
├── render.yaml                 # Render deployment blueprint
├── requirements.txt            # Python dependencies
├── fso_list.md                 # FSO master data
├── fss_sections.md             # FSS Act legal sections
└── app.py                      # WSGI entry point
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

| Test File | Coverage |
|-----------|----------|
| `test_step1.py` | FSO model, markdown sync, Sample model, code generation |
| `test_step2.py` | Billing utilities, Excel export, filtering |
| `test_step3.py` | Inspection model, code generation, deadline calculation |
| `test_step4.py` | Derived-state queries, dismiss action, adjudication linkage |
| `test_step5_integration.py` | Cross-module integration scenarios |
| `test_route_collisions.py` | Regression: duplicate route detection |
| `test_bill_generator.py` | Bill generation logic |
| `test_pdf_photo_embedding.py` | PDF photo embedding edge cases |

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

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `SECRET_KEY` | Yes | Flask secret key (min 32 chars) |
| `REDIS_URL` | For Celery | Redis connection string |
| `GOOGLE_CREDENTIALS_JSON` | For Sheets | Google service account JSON |
| `SPREADSHEET_ID` | For Sheets | Google Sheets document ID |
| `R2_ACCESS_KEY` | For Storage | R2/B2 access key |
| `R2_SECRET_KEY` | For Storage | R2/B2 secret key |
| `R2_BUCKET` | For Storage | Storage bucket name |
| `R2_ENDPOINT` | For Storage | Storage endpoint URL |
| `SKIP_FSO_STARTUP_SYNC` | No | Skip FSO sync on startup |

---

## API Reference

> **Note:** API documentation is auto-generated from code. Endpoints follow a RESTful convention.

### Blueprint Prefixes

| Blueprint | Prefix | Description |
|-----------|--------|-------------|
| Auth | `/auth` | Login/logout |
| Inspection | `/inspection` | Inspection CRUD + photo evidence |
| Sample | `/sample` | Sample management |
| Case File | `/case_file_generator` | Case file generation |
| Adjudication | `/adjudication` | Adjudication management |
| Billing | `/billing` | Billing summary + export |
| FBO Issue | `/fbo-issue` | FBO issue state machine |
| Audit | `/admin` | Audit log viewer |
| Settings | `/settings` | Admin settings |

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

| Element | Convention | Example |
|---------|-----------|---------|
| Modules | `snake_case` | `inspection_utils.py` |
| Classes | `PascalCase` | `class InspectionPhoto` |
| Functions | `snake_case` | `def generate_inspection_code()` |
| Variables | `snake_case` | `compliance_deadline` |
| Constants | `UPPER_CASE` | `MAX_FILE_SIZE` |
| DB Columns | `snake_case` | `food_safety_officer_name` |
| Blueprints | `snake_case` | `inspection_bp` |

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
- Optimistic concurrency control

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

### Level 9: Intelligence ⬜ Planned
- Qdrant vector store integration
- Semantic search over legal corpus
- AI-powered section suggestion
- Document embedding pipeline

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
