# 🏛️ NSA Legal Intelligence Platform

[![Repository Validation](https://github.com/sumanksaha/NSA_webservice/actions/workflows/validation.yml/badge.svg)](https://github.com/sumanksaha/NSA_webservice/actions/workflows/validation.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/badge/linter-ruff-purple.svg)](https://github.com/astral-sh/ruff)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](CODE_OF_CONDUCT.md)

A comprehensive **Food Safety Legal Intelligence Platform** designed to streamline inspection workflows, sample management, adjudication processing, and case file generation for food regulatory authorities.

---

## 📋 Table of Contents

- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [Security Policy](#security-policy)
- [Roadmap](#roadmap)
- [License](#license)

---

## ✨ Features

### Core Modules

- **Inspection Management** — Schedule, track, and document food safety inspections with photo evidence and geo-verification
- **Sample Management** — Handle sample collection, lab submission, and results tracking
- **Adjudication Engine** — Process adjudication cases with state machine workflow and audit trails
- **Case File Generator** — Automatically generate structured case files from inspection and adjudication data
- **Bill Generator** — Generate billing documents for inspections and services
- **FBO Issue Management** — Track and manage Food Business Operator issues through a complete lifecycle

### Technical Highlights

- **🔒 Security-first design** — CSRF protection, CSP headers, session hardening, TLS verification, and audit logging
- **🔄 Async task processing** — Celery-based OCR pipeline for document processing
- **📊 Google Sheets integration** — Seamless data exchange with spreadsheet workflows
- **🗄️ Multi-database support** — PostgreSQL with Alembic migrations
- **📸 Photo evidence system** — Image capture with metadata and geo-location verification
- **🔍 OCR pipeline** — Automated OCR processing with Celery task queue

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    Flask Application                  │
├────────────┬──────────┬──────────┬──────────────────┤
│ Inspection │  Sample  │Adjudication│  Case File Gen  │
│  Module    │  Module  │  Module   │    Module        │
├────────────┴──────────┴──────────┴──────────────────┤
│                   Celery Workers                      │
│              (OCR Pipeline, Async Tasks)              │
├──────────────────────────────────────────────────────┤
│         Database Layer (PostgreSQL + SQLAlchemy)       │
├──────────────────────────────────────────────────────┤
│    Services: Redis | Google Sheets | R2 Storage       │
└──────────────────────────────────────────────────────┘
```

### Key Design Decisions

- **Flask Blueprint Architecture** — Modular organization with independent, testable modules
- **SQLAlchemy ORM** — Type-safe database interactions with migration support via Alembic
- **Celery + Redis** — Distributed task queue for OCR and background processing
- **Flask-Talisman** — Comprehensive security headers (CSP, HSTS, X-Frame-Options)
- **Optimistic Locking** — Versioned rows prevent concurrent modification conflicts

---

## 🚀 Installation

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- Redis 7+ (or Memurai on Windows)

### Quick Start

```bash
# Clone the repository
git clone https://github.com/sumanksaha/NSA_webservice.git
cd NSA_webservice

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Development dependencies

# Configure environment
cp .env.example .env
# Edit .env with your configuration

# Run database migrations
flask db upgrade

# Start the development server
flask run
```

---

## ⚙️ Configuration

Configuration is managed via environment variables. Copy `.env.example` to `.env` and adjust:

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_APP` | Application entry point | `app:create_app` |
| `SECRET_KEY` | Flask secret key for sessions | *(required)* |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://...` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL | `redis://localhost:6379/0` |
| `GOOGLE_SHEETS_CREDENTIALS` | Service account JSON path | *(optional)* |
| `R2_ENDPOINT_URL` | Cloudflare R2 endpoint | *(optional)* |
| `DISABLE_PDF_GENERATION` | Disable PDF in dev/test | `false` |

---

## 📖 Usage

### Running the Application

```bash
# Development server
flask run

# Production with Gunicorn
gunicorn --bind 0.0.0.0:8000 --workers 4 app:create_app

# Start Celery worker
celery -A celery_worker.celery worker --loglevel=info
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| POST | `/auth/login` | User authentication |
| GET | `/inspections/` | List inspections |
| POST | `/samples/` | Submit sample |
| POST | `/adjudication/` | Process adjudication |
| GET | `/admin/audit` | Audit log viewer |

---

## 💻 Development Setup

### Prerequisites

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install

# Verify pre-commit runs cleanly
pre-commit run --all-files
```

### Code Quality

This project enforces code quality through automated tools:

- **Black** — Code formatting (line length: 120, target: Python 3.12)
- **Ruff** — Fast linting with auto-fix
- **Mypy** — Static type checking
- **Bandit** — Security vulnerability scanning
- **pip-audit** — Dependency vulnerability audit

### Database Migrations

```bash
# Create a new migration
flask db migrate -m "Description of changes"

# Apply migrations
flask db upgrade

# Rollback
flask db downgrade
```

---

## 🧪 Running Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=app --cov-report=term-missing

# Run specific test file
pytest tests/test_step1.py

# Run tests matching a keyword
pytest -k "fso_model"

# Generate HTML coverage report
pytest --cov=app --cov-report=html
```

---

## 📁 Project Structure

```
NSA_webservice/
├── app/                          # Application package
│   ├── __init__.py               # Flask application factory
│   ├── extensions.py             # SQLAlchemy instance
│   ├── models.py                 # Database models
│   ├── adjudication/             # Adjudication blueprint
│   ├── audit/                    # Audit logging
│   ├── auth/                     # Authentication system
│   ├── bill_generator/           # Bill generation
│   ├── billing/                  # Billing module
│   ├── case_file_generator/      # Case file generation
│   ├── fbo_issue/                # FBO issue tracker
│   ├── inspection/               # Inspection module
│   ├── sample/                   # Sample management
│   ├── services/                 # Shared services
│   ├── settings/                 # Configuration blueprints
│   ├── shared/                   # Shared utilities
│   ├── static/                   # Static assets
│   ├── templates/                # Jinja2 templates
│   └── utils/                    # Utility functions
├── db/                           # Database files
├── docs/                         # Documentation
├── migrations/                   # Alembic migrations
│   ├── versions/                 # Migration versions
│   └── env.py                    # Migration environment
├── scripts/                      # Utility scripts
├── tests/                        # Test suite
│   ├── test_bill_generator.py
│   ├── test_pdf_photo_embedding.py
│   ├── test_route_collisions.py
│   ├── test_step1.py
│   ├── test_step2.py
│   ├── test_step3.py
│   ├── test_step4.py
│   └── test_step5_integration.py
├── .github/                      # GitHub configuration
│   ├── workflows/                # CI/CD workflows
│   └── ISSUE_TEMPLATE/           # Issue templates
├── requirements.txt              # Production dependencies
├── requirements-dev.txt          # Development dependencies
├── pyproject.toml                # Project configuration
├── .pre-commit-config.yaml       # Pre-commit hooks
└── render.yaml                   # Render deployment config
```

---

## 👥 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details on:

- Code of Conduct
- Development setup
- Coding standards (Black, Ruff, Mypy)
- Pull request process
- Testing requirements

---

## 🔒 Security Policy

Security is a top priority. See our [Security Policy](SECURITY.md) for:

- Reporting vulnerabilities
- Supported versions
- Security best practices
- Disclosure policy

**Key security features implemented:**
- ✅ CSRF protection (Flask-WTF)
- ✅ Security headers (CSP, HSTS, X-Frame-Options)
- ✅ Session hardening (Secure, HttpOnly, SameSite)
- ✅ Audit logging
- ✅ TLS certificate verification
- ✅ Rate limiting

---

## 🗺️ Roadmap

### Recent (v1.0.1)
- ✅ Authentication system (Flask-Login)
- ✅ CSRF protection and security headers
- ✅ Audit logging system
- ✅ TLS certificate verification fix
- ✅ Dependency security scanning

### In Progress
- [ ] Role-based access control (RBAC)
- [ ] API key management
- [ ] Multi-factor authentication

### Future
- [ ] Advanced analytics dashboard
- [ ] Real-time notification system
- [ ] Mobile-optimized interface
- [ ] Bulk data import/export
- [ ] Automated regulatory compliance checks

---

## 📄 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

---

<p align="center">
  <sub>Built with ❤️ for food safety regulatory compliance</sub>
</p>