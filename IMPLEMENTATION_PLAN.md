# Implementation Plan — Remaining Roadmap Features

> **Generated:** 2026-08-04
> **Last status update:** 2026-08-04 — Phase 8 PDF hyperlinks closed (link-annotation pass + regression tests); Phase 9 version control verified complete. **Phases 1–9 are now 100% implemented.**
> **Basis:** Code-verified gap analysis of `ROADMAP_ALIGNMENT_REPORT.md` (2026-08-03) against the live working tree and git history (`git log`).
> **Scope:** Determine what Phases 1–9 actually shipped, what is missing, and how to implement the missing features.

---

## 1. Verified Implementation Status — Phases 0–9

Implementation followed the roadmap's recommended order exactly (verified via `git log`):

| Phase | Feature | Status | Verification |
|---|---|---|---|
| 0 | Architecture decision (keep Flask) + JS linting (ESLint+Prettier) | ✅ Complete | `package.json`, `eslint.config.js`, commits `3d42b53`, `62b2422` |
| 1 | Core petition engine — auto-save, Delta storage, validation error display, Facts/Grounds/Prayer sections | ✅ Complete | commits `ee3db9a`, `a993aba`, `tests/test_phase1.py` |
| 2 | Rich editor — Quill 2.x, image upload, Markdown export | ✅ Complete | commit `1b97c6a`, `tests/test_document_viewer_phase2.py` |
| 3 | Local DB — Settings/Annexure/Evidence/Version models, FTS5 search, backup/restore | ✅ Complete | commits `e9e3a0e`, `00db98e`, `86263d8` |
| 4 | Annexure management — upload, A/B/C letters, metadata, duplicate detection | ✅ Complete | commit `77d3619`, `tests/test_annexure.py` |
| 5 | Evidence management — unified model, drag-drop, compression, thumbnails, search | ✅ Complete | commit `d2f279f`, `app/evidence/`, `tests/test_phase5_evidence.py` |
| 6 | Cross-reference engine — extraction/linking/renumbering/enclosures | ✅ Complete | commit `174438e`, `app/cross_reference/`, `tests/test_cross_reference.py` |
| 7 | Dynamic TOC — extraction, numbering, bookmarks, live editor panel | ✅ Complete | `app/toc_generator/`, `tests/test_toc_generator.py` |
| 8 | PDF assembly engine | ✅ **Complete** | `app/pdf_assembly/`, `tests/test_phase8_pdf_assembly.py` |
| 9 | Version control | ✅ **Complete** | `app/services/version_control.py`, `app/version_control/routes.py`, `app/version_control/templates/`, `tests/test_version_control.py` |

**Implementation order observed:** Phase 0 → 1 → 2 → 3 → 4 → 5 → 6/7 → 8 → 9, matching the recommended order in `ROADMAP_ALIGNMENT_REPORT.md` §5. Phases 8 and 9 are **currently uncommitted** in the working tree.

---

## 2. Gap Analysis — Features NOT Implemented

### 2.1 Gaps inside Phases 1–9 — **NONE REMAINING (as of 2026-08-04)**

All gaps identified in the 2026-08-04 analysis are now closed:

**Phase 8 — PDF Assembly Engine (2 items):**
1. **PDF hyperlinks** — ✅ **CLOSED 2026-08-04**: `PDFAssemblyEngine` gained a link-annotation pass (`_apply_hyperlinks` in `app/pdf_assembly/__init__.py`) that (a) injects link-styling CSS so `a[href]` anchors render visibly clickable in the compiled PDF, (b) verifies every internal `#anchor` href has a matching heading `id` (re-running the Phase 7 heading-annotation pass when any are missing, so TOC/reference anchors always survive post-processing), and (c) wraps bare `http(s)://` annexure/evidence URLs in `<a>` tags so they become external PDF links. Gated by `PDF_ENABLE_HYPERLINKS` (default on). 9 regression tests added to `tests/test_phase8_pdf_assembly.py` (40 total).
2. QR code — implemented behind `PDF_ENABLE_QR_CODES`; **`qrcode>=8.0` is already declared in `pyproject.toml`** (report was stale on this point). ✅ No action needed.

**Phase 9 — Version Control (5 items):** ✅ All closed (see Step 1 below):
1. `restore_version()` writes the snapshot HTML back to the live document / `instance/saved/` via `save_saved_document()` — **done**.
2. `create_branch()` persists `branch_name` + `branch_of` (migration `add_version_branch_columns`) with branch numbering restarted at 1 — **done**.
3. `_diff_html()` uses `difflib.SequenceMatcher` opcodes + a tag-stripped `unified` preview — **done**.
4. Version-control UI (`history.html` + `version_control.js`) linked from the editor page — **done**.
5. Case-vs-adjudication disambiguation via `?kind=case_file|adjudication` in `_resolve_target()` — **done**.

### 2.2 Phases 10–20 — Not implemented (whole modules)

| Phase | Feature | Current state | Missing |
|---|---|---|---|
| 10 | Search engine | FTS5 keyword + entity filters ✅ | **Fuzzy search** (rapidfuzz) — `rapidfuzz` is imported by `app/document_cleaner/normalizers.py` but **not declared** in `pyproject.toml` |
| 11 | AI assistant | Rule-based `suggester.py` only | LLM service (`app/ai_assistant/`), grammar/language/summarize/draft features, UI wiring |
| 12 | Legal rule engine | Partial section suggestions | `ValidationEngine` (`app/validation/`): score, warnings, missing signatures, numbering, statutory refs, duplicate evidence, timeline consistency, completeness |
| 13 | Timeline engine | — | `TimelineEvent` model, auto-generation from dates, Gantt-style UI, doc links |
| 14 | Knowledge graph | — | Entity/Relationship models, extraction, traversal API |
| 15 | Analytics dashboard | Billing summary + inspection/sample lists | Dashboard route + template, aggregate queries, charts, geo map, violation trends |
| 16 | Backup & export | PDF/ZIP export ✅, manual backup/restore ✅ | **JSON export**, **case import**, **scheduled backups** (Celery beat) |
| 17 | Cloud sync | Sheets sync + Cloudinary photos ✅ | Supabase bridge, annexure sync, conflict resolution, sync-status UI |
| 18 | Multi-user workflow | Auth only | Role model, `@role_required`, comments, approval workflow, user-management UI |
| 19 | AI case intelligence | — | `app/case_intelligence/`: evidence-strength analysis, traceability, date conflicts, readiness score |
| 20 | Plugin architecture | OCR/rules hardcoded | Plugin registry (`app/plugins/`), OCR/AI/rule/PDF provider interfaces |

---

## 3. Recommended Implementation Order

### Step 1 — Finish Phase 9 (Version Control) — ✅ **COMPLETED 2026-08-04**
Target files: `app/services/version_control.py`, `app/version_control/routes.py`, `app/document_viewer/`, `app/static/js/document_viewer/editor.js`, `tests/test_version_control.py`, `migrations/`

1. **Real restore** — ✅ `restore_version()` appends a "Restored to version N" snapshot and writes the HTML back to `instance/saved/` via the shared `save_saved_document()` helper (rolls the version back if the disk write fails).
2. **Branch support** — ✅ `branch_name` + `branch_of` columns on `Version` (migration `add_version_branch_columns`, chained off head `unify_photo_evidence`); `create_branch()` persists a branch root with numbering restarted at 1; `_get_next_version_number()` scopes per (case, doc_type, branch). Mainline uniqueness preserved via *partial unique indexes* (`WHERE branch_name IS NULL`) so branches don't defeat the DB constraint.
3. **Real diff** — ✅ `_diff_html()` uses `difflib.SequenceMatcher` opcodes (ordered insertions/deletions) + a tag-stripped line-level `unified` preview.
4. **Case vs adjudication** — ✅ `_resolve_target(id, kind)` disambiguates via an explicit `?kind=case_file|adjudication` param (IDs collide across the two tables); all four routes + the editor History button pass it.
5. **Version history UI** — ✅ `app/version_control/templates/version_control/history.html` + `app/static/js/version_control/version_control.js` (version list per doc-type, compare panel, restore with confirm, branch modal) + "History" button on the editor page (`template_folder="templates"` added to the blueprint).
6. **Tests** — ✅ 23 tests in `tests/test_version_control.py` (restore round-trip, branch isolation, difflib content, case/adjudication disambiguation, UI page render). Also fixed a pre-existing bug where `_snapshot_version` never snapshotted (missing `adjudication_id` kwarg → silently swallowed `TypeError`).

### Step 2 — Phase 8 PDF hyperlinks — ✅ **COMPLETED 2026-08-04**
Target files: `app/pdf_assembly/__init__.py`, `tests/test_phase8_pdf_assembly.py`

1. **Link-annotation pass** — ✅ `_apply_hyperlinks()` in `app/pdf_assembly/__init__.py`: link-styling CSS injection, internal-anchor-target verification (re-runs the idempotent Phase 7 heading annotation when a `#anchor` target is missing), and bare-URL → `<a>` linkification for annexure/evidence URLs. Wired into `_apply_complete_post_processing` after the page-number pass; toggle `PDF_ENABLE_HYPERLINKS` (default on).
2. **Regression tests** — ✅ 9 tests in `tests/test_phase8_pdf_assembly.py` (styling injected with/without existing `<style>`, TOC anchors + targets survive the pass, missing targets re-annotated, bare URLs wrapped, attribute URLs untouched, defensive on empty input, config toggle, wired into the complete chain). WeasyPrint renders the surviving `<a href="#anchor">` links as clickable PDF link annotations natively.

### Step 3 — Quick wins
1. **Phase 10 fuzzy search** — add `rapidfuzz` to `pyproject.toml`; in `app/search/indexer.py::search()`, add a fuzzy fallback (`fuzz.ratio`/`partial_ratio` against title/content) when FTS5/LIKE returns nothing; expose a `fuzzy=true` query param in `app/search/routes.py` + UI toggle. (Also fixes the latent undeclared-dependency bug in `document_cleaner/normalizers.py`.)
2. **Phase 16 JSON export + scheduled backups** — add `GET /api/cases/<id>/export.json` (full case: CaseFile + annexures + evidence + versions); extend the ZIP export to bundle them; add a Celery beat schedule (`celery_app.py`) for periodic backup snapshots reusing `app/utils/backup.py`.

### Step 4 — New modules (each is a mini-project; implement in this order)
1. **Phase 12 Legal ValidationEngine** — `app/validation/` (`engine.py`, `rules.py`, `routes.py`): reuse `suggester.py` + `legal_paragraph_detection_engine`; produce `{score, warnings, errors, suggestions}`; wire into `app/legal_analysis/routes.py`.
2. **Phase 13 Timeline engine** — `TimelineEvent` model + migration, `app/timeline/engine.py` derives events from case dates (complaint → inspection → sampling → dispatch → lab → notice → reply → petition → order), `routes.py` + Gantt-style template.
3. **Phase 15 Analytics dashboard** — `app/analytics/` with aggregate queries over CaseFile/Inspection/Sample/Evidence/Adjudication + Chart.js template; nav link in `app/templates/base.html`.
4. **Phase 18 RBAC** — `Role`/`UserRole` models + `@role_required` decorator (`app/decorators.py`), admin user-management UI, optional `Comment` model + approval status field.
5. **Phase 11 AI assistant** — needs an API-key decision (OpenRouter/OpenAI); `app/ai_assistant/service.py` with prompt templates for grammar/legal language/summarize/contradictions/missing-annexures/draft prayers-facts-grounds; editor sidebar UI.
6. **Phase 19 Case intelligence** — `app/case_intelligence/engine.py` composing Phase 12 checks + evidence-strength (OCR quality, verification status) + traceability + readiness score.
7. **Phase 14 Knowledge graph** — `Entity`/`Relationship` models, extraction from case data, `app/knowledge_graph/` traversal API + visualizer.
8. **Phase 20 Plugin architecture** — `app/plugins/registry.py` + provider interfaces (OCR/AI/rules/PDF), refactor `ocr_pipeline` and `suggester.py` behind them.
9. **Phase 17 Cloud sync** — Supabase bridge (`app/sync/`), annexure/evidence sync, `version_id` conflict resolution, sync-status UI.

---

## 4. File-Level Edit Guide

### 4.0 Phases 1–9 — closed (no edits pending)

| Phase | Status | Key files |
|---|---|---|
| 8 | ✅ Complete | `app/pdf_assembly/__init__.py` (hyperlink pass `_apply_hyperlinks`), `tests/test_phase8_pdf_assembly.py` (40 tests) |
| 9 | ✅ Complete | `app/services/version_control.py`, `app/version_control/routes.py` + `templates/` + `static/js/version_control/`, migration `add_version_branch_columns`, `tests/test_version_control.py` (23 tests) |

Remaining work is entirely **Phases 10–20** (below).

| Blueprint to create | New files |
|---|---|
| `app/validation/` | `__init__.py`, `engine.py`, `rules.py`, `routes.py`, `templates/` |
| `app/timeline/` | `__init__.py`, `engine.py`, `routes.py`, `templates/` |
| `app/analytics/` | `__init__.py`, `routes.py`, `templates/` |
| `app/ai_assistant/` | `__init__.py`, `service.py`, `routes.py`, `templates/`, `app/static/js/ai_assistant.js` |
| `app/case_intelligence/` | `__init__.py`, `engine.py` |
| `app/knowledge_graph/` | `__init__.py`, `models.py`, `engine.py`, `routes.py` |
| `app/sync/` | `__init__.py`, `supabase_sync.py`, `routes.py` |
| `app/plugins/` | `__init__.py`, `registry.py`, `base.py` |

| Existing file | Extension |
|---|---|
| `app/models.py` | `TimelineEvent`, `Role`, `UserRole`, `Comment`, `Entity`, `Relationship` models (`Version` branch columns — ✅ done) |
| `migrations/` | New alembic revisions for each model group |
| `app/version_control/` | — ✅ complete (templates + disambiguated routes) |
| `app/services/version_control.py` | — ✅ complete (real restore, branches, difflib diff) |
| `app/search/indexer.py` | Fuzzy fallback |
| `app/search/routes.py` | `fuzzy` param |
| `app/case_file_generator/routes.py` | JSON export |
| `app/case_file_generator/tasks.py` | Export annexures/evidence/versions in ZIP |
| `celery_app.py` | Beat schedule for backups |
| `app/__init__.py` | Register new blueprints |
| `app/templates/base.html` | Nav links (analytics, version history) |
| `pyproject.toml` | `rapidfuzz`, `openai`/`httpx` (Phase 11) |

## 5. Dependencies to add
- `rapidfuzz` (Phase 10 — also fixes undeclared import in `document_cleaner/normalizers.py`)
- `openai` or `httpx` + env var for API key (Phase 11 — needs a provider decision)
- `qrcode` — **already declared** (`pyproject.toml`, `qrcode>=8.0`)
