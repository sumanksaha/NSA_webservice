# Security Advisory Summary - Phase L1 Intel

---

## Repository Identity

| Attribute | Value |
|-----------|-------|
| Repository | sumanksaha/NSA_webservice |
| Resolved via | Git remote origin |
| Primary Language | Python |
| Framework | Flask (Pallets projects) |
| Git History Available | Yes |
| Default Branch | main |
| Description | Non sample adjudication - Food safety adjudication web service |

---

## Recent Advisories (last 24 months)

### Project-Level Advisories
| ID | Severity | Published | Summary | Affected Component |
|----|----------|-----------|---------|-------------------|
| (none) | - | - | No GitHub Security Advisories published for this repository | - |

### Dependency Advisories

#### Flask (Core Framework)
| ID | Severity | CVSS | Published | Summary | Affected Versions |
|----|----------|------|-----------|---------|-------------------|
| GHSA-4grg-w6v8-c28g | LOW | 4.0 | 2025-05-13 | Session signing fallback key issue | 3.1.0 |
| GHSA-68rp-wp8r-4726 | LOW | 4.0 | 2026-02-19 | Missing Vary: Cookie header | < 3.1.3 |
| GHSA-m2qf-hxjv-5gpq | HIGH | 3.1 | 2023-05-01 | Session cookie disclosure | 2.3.0, 2.3.1 |

#### Celery (Task Queue)
| ID | Severity | CVSS | Published | Summary | Affected Versions |
|----|----------|------|-----------|---------|-------------------|
| GHSA-q4xr-rc97-m4xx | HIGH | 3.1 | 2022-01-06 | OS Command Injection via task metadata | < 5.2.2 |

#### Redis (Cache/Broker)
| ID | Severity | CVSS | Published | Summary | Affected Versions |
|----|----------|------|-----------|---------|-------------------|
| GHSA-24wv-mv5m-xv4h | MODERATE | 3.1 | 2023-03-26 | Race condition in async command cancellation | 4.2.0-4.5.2 |
| GHSA-8fww-64cx-x8p5 | HIGH | 3.1 | 2023-03-26 | Race condition due to incomplete fix | 4.5.0-4.5.3 |

#### SQLAlchemy (ORM)
| ID | Severity | CVSS | Published | Summary | Affected Versions |
|----|----------|------|-----------|---------|-------------------|
| GHSA-38fc-9xqv-7f7q | CRITICAL | 3.0 | 2019-04-16 | SQL Injection via group_by | < 1.2.19 |
| GHSA-887w-45rq-vxgf | CRITICAL | 3.0 | 2019-04-16 | SQL Injection via order_by | < 1.3.0b3 |

#### httpx (HTTP Client)
| ID | Severity | CVSS | Published | Summary | Affected Versions |
|----|----------|------|-----------|---------|-------------------|
| GHSA-h8pj-cxx2-jfg2 | CRITICAL | 3.1 | 2022-04-28 | Improper URL input validation | < 0.23.0 |

#### WeasyPrint (PDF Generation)
| ID | Severity | CVSS | Published | Summary | Affected Versions | CWE |
|----|----------|------|-----------|---------|-------------------|-----|
| GHSA-35jj-wx47-4w8r | HIGH | 3.1 | 2024-03-08 | Arbitrary file/URL attachment to PDF | 61.0, 61.1 | CWE-829 |
| GHSA-983w-rhvv-gwmv | HIGH | 3.1 | 2026-01-20 | SSRF bypass via HTTP redirect | < 68.0 | CWE-601, CWE-918 |
| GHSA-jhhc-3hcp-qhm5 | MODERATE | 3.1 | 2026-07-06 | CSS injection via presentational hints | < 68.1 | CWE-74 |

#### openpyxl (Excel Processing)
| ID | Severity | CVSS | Published | Summary | Affected Versions | CWE |
|----|----------|------|-----------|---------|-------------------|-----|
| GHSA-chqf-hx79-gxc6 | MODERATE | 3.0 | 2022-05-17 | XXE via crafted .xlsx documents | < 2.4.2 | CWE-611 |

### Severity Distribution (Recent 2 Years)
- CRITICAL: 3 (SQLAlchemy x2, httpx)
- HIGH: 6 (Celery, Redis x2, WeasyPrint x3)
- MODERATE: 3 (Redis, openpyxl, WeasyPrint)
- LOW: 3 (Flask x3)

---

## Dependency Intelligence

### Direct Dependencies (from requirements.txt)

| Package | Version | Security-Related | Notes |
|---------|---------|-------------------|-------|
| flask | latest | Yes | Session signing vulnerabilities, XSS potential |
| flask-sqlalchemy | latest | Yes | SQLAlchemy ORM dependency |
| flask-migrate | latest | Yes | Alembic migrations |
| alembic | >=1.13.0 | Yes | Database migrations |
| gspread | ==6.2.1 | Yes | Google Sheets API client |
| google-auth | ==2.56.0 | Yes | Google API authentication |
| jinja2 | latest | Yes | Template engine (XSS risk) |
| psycopg2-binary | >=2.9.9 | Yes | PostgreSQL adapter |
| weasyprint | latest | Yes | PDF generation (SSRF, file attachment risks) |
| python-multipart | latest | Yes | Form data handling |
| num2words | latest | No | Number-to-words conversion |
| httpx | latest | Yes | HTTP client (URL validation issues) |
| gunicorn | latest | No | WSGI server |
| openpyxl | latest | Yes | Excel processing (XXE risk) |
| boto3 | latest | Yes | AWS SDK (credential exposure risk) |
| python-dotenv | >=1.0.0 | Yes | Environment variable loading |
| celery | latest | Yes | Task queue (command injection risk) |
| redis | latest | Yes | Cache/broker (race conditions) |

---

## Architecture Hints

### Component Map
- NSA Webservice (Flask Application)
  - Auth (routes)
  - Flask App Factory (app/__init__.py)
    - Extensions: SQLAlchemy, Talisman, CSRF, LoginManager
  - DB (PostgreSQL)
  - Redis (Broker/Backend)
  - Tasks (Celery)
    - Celery workers (PDF generation)
    - Sheets sync tasks

### Key Modules
| Module | Purpose | Security Surface |
|--------|---------|------------------|
| app/__init__.py | Flask app factory | SECRET_KEY, DATABASE_URL, REDIS_URL |
| app/models.py | SQLAlchemy models | SQL injection, data validation |
| app/extensions.py | Flask extensions | CSRF, Talisman |
| app/auth/ | Authentication | Login bypass, session management |
| app/inspection/ | Inspection workflows | Input validation |
| app/adjudication/ | Case adjudication | Data handling |
| app/bill_generator/ | Bill PDF generation | WeasyPrint SSRF, XSS |
| app/case_file_generator/ | Case file PDF | WeasyPrint SSRF, XSS |
| app/services/sheets_sync.py | Google Sheets sync | Credential exposure |
| celery_app.py | Celery configuration | Task injection |

### Trust Boundaries
1. Internet-facing: HTTP endpoints (Flask)
2. Internal: Redis, PostgreSQL
3. External: Google Sheets API, AWS S3/R2

### High-Risk Flows
1. PDF Generation: User HTML -> WeasyPrint -> PDF (SSRF, file attachment, CSS injection)
2. Sheets Sync: CSV data -> Google Sheets API (credential exposure)
3. Task Processing: Task metadata -> Celery worker -> Redis (command injection)

---

## Coverage Gaps

| Gap Type | Description | Impact |
|----------|-------------|--------|
| No project-level GHSA | No published GitHub Security Advisories | Cannot track project-specific vulnerability history |
| No changelog files | No CHANGELOG, HISTORY, or RELEASES files | Cannot trace security fixes in commit history |
| No dependency lockfile | No requirements-lock.txt or poetry.lock | Cannot verify exact dependency versions |
| No Dockerfile | No containerization definition | Cannot verify secure image usage |
| No CI/CD workflows | No .github/workflows directory | Cannot verify automated security scanning |
| No setup.py/pyproject.toml | Project uses bare requirements.txt | Limited metadata for ecosystem analysis |

---

Report generated: 2026-07-26
Source: OSV API, GitHub API, local repository analysis
