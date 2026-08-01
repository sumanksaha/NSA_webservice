# Bootstrap Report — Project Memory & Context Optimisation

**Repository:** `NSA_webservice`  
**Date:** 2026-08-01  
**Scope:** Phase 1 (Analysis) → Phase 5 (Report) of the **Bootstrap Project Memory
and Context Optimisation** task. No application code was modified; only
documentation was generated under `.ai/`.

---

## Repository Size

| Metric | Value |
|--------|-------|
| On-disk size (working tree, incl. caches/DBs) | **~300 MB** |
| Git-tracked files total | **258** |
| Git-tracked Python source files (`.py`, excl. caches) | **167** |
| Working-tree Python source files (`.py`, excl. caches) | **181** |
| `.pyc` cache files (excluded) | ~thousands |
| Large binary/DB assets (excluded) | `instance/app.db`, `db/*.db`, `*.zip`, `*.pdf`, `*.csv` |

> The working tree contains generated/untracked artefacts (~53 untracked entries
> per `git status --short`) that are excluded from AI indexing via
> `.ai/ignore.txt`.

## Approximate Number of Source Files

- **Application package (`app/`):** 50+ `.py` files across 16 sub-packages.
- **Legal Paragraph Detection Engine (`legal_paragraph_detection_engine/`):**
  ~20 `.py` files (src + tests) — standalone library.
- **Root scripts:** ~40 ad-hoc `.py` (check/verify/fuzzy-dedup/etc.).
- **Migrations:** 14 Alembic version scripts.
- **Tests:** 11 canonical (`tests/`) + 9 legal-engine unit tests + root
  `test_*.py` helpers.
- **Total distinct Python source modules:** ~167 (git-tracked).

## Major Modules

1. **Backend / App Core** — `app/__init__.py`, `extensions.py`, `models.py`,
   `audit_hooks.py`.
2. **Domain Blueprints** — auth, inspection, sample, case_file_generator,
   adjudication, bill_generator, billing, fbo_issue, settings, audit.
3. **Data Pipeline Subsystems** — document_loader, document_cleaner,
   ocr_pipeline, metadata_extractor.
4. **Services / Shared** — services (sheets_sync, audit), shared (case_keys,
   context_derivers), utils (storage, fso_data, sync, lookup, suggester,
   pdf_utils, filters).
5. **Legal Paragraph Detection Engine** — standalone legal-document parser.
6. **Celery** — `celery_app.py` + per-module `tasks.py`.
7. **Tests / Scripts / Migrations** — `tests/`, `scripts/`, `migrations/`.

## Memory Files Created

```
.ai/
├── PROJECT_MEMORY.md            # project-wide architecture summary
├── BOOTSTRAP_REPORT.md          # this file
├── ignore.txt                   # AI-index exclusion patterns
└── memory/
    ├── backend.md
    ├── api.md
    ├── database.md
    ├── authentication.md
    ├── inspection.md
    ├── sample.md
    ├── case_file.md
    ├── adjudication.md
    ├── fbo_issue.md
    ├── billing.md
    ├── document_loader.md
    ├── document_cleaner.md
    ├── ocr.md
    ├── metadata_extractor.md
    ├── legal_engine.md
    ├── services.md
    ├── shared.md
    ├── audit.md
    ├── deployment.md
    └── testing.md
```

**Total memory documents:** 20 (1 project-level + 19 module-level) +
`ignore.txt` + this report.

## Folders Excluded from AI Indexing

Per `.ai/ignore.txt`, the following are never indexed:

| Category | Excluded paths |
|----------|----------------|
| Version control & CI | `.git`, `.github` |
| Python caches | `__pycache__/`, `*.pyc`, `venv/`, `.venv/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/` |
| Build / packaging | `build/`, `dist/`, `*.egg-info/` |
| Test / coverage | `.coverage*`, `htmlcov/`, `coverage.xml`, `.tox/` |
| Runtime / logs | `instance/` (SQLite DB + `credentials.json`), `logs/`, `*.log`, `tmp/`, `.cache/` |
| Generated data | `output/`, `*.csv`, `*.json`, `generated/` |
| Binary assets | `*.jpg/jpeg/png/gif/webp/heic/pdf/docx/doc/zip/tar/gz` |
| ML model artifacts | `models/`, `*.pt/bin/gguf/safetensors/h5/onnx` |
| Legal engine specifics | `legal_paragraph_detection_engine/output/`, `.mypy_cache/`, `benchmarks/`, `config/`, `examples/` |
| Datasets / references | `datasets/`, `data/`, `large_pdfs/` |
| Root stray artefacts | `nul`, `c`, `merged_summary_report.txt`, `stage0_status_report.txt` |

## Estimated Token Savings

Excluding the excluded folders (caches, binaries, DBs, generated data, large
PDFs/CSVs) removes roughly **~290 MB** of non-source content (97% of on-disk
weight) from the AI context window. The ~167 Python source modules plus
templates/docs are the indexable surface (~10–12 MB of text), a **~25× reduction**
in scan surface area versus scanning the raw tree.

## Recommended Future Workflow

1. **Incremental updates** — update the relevant `.ai/memory/<module>.md` file
   whenever a module's public interface changes; never regenerate the whole tree.
2. **Before editing** — read the module memory doc to understand ownership,
   dependencies, and known constraints (e.g. `context_derivers.py` `# type: ignore`,
   audit hook column exclusions).
3. **Ignore hygiene** — keep `.ai/ignore.txt` in sync with `.gitignore` for any
   new generated/cache folders (e.g. `legal_paragraph_detection_engine/output/`).
4. **New subsystem** — add a `memory/<name>.md` and a section in
   `PROJECT_MEMORY.md`; update folder-structure + navigation-guide entries.
5. **Bootstrapping new sessions** — a coding agent should read
   `.ai/PROJECT_MEMORY.md` first, then the targeted `memory/<module>.md` before
   making changes.

*End of report.*
