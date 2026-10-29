# Repository Cleanup Report

**Generated:** 2026-08-03  
**Repository:** NSA Webservice (v0.8.0)  
**Audit Method:** ponytail-audit + automated reference verification

---

## Executive Summary

| Metric                             | Value                                                                                   |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| **Repository Statistics**          |                                                                                         |
| Total Python files (tracked)       | ~274                                                                                    |
| Lines of Python code (tracked)     | 51,848                                                                                  |
| Total tracked files                | 665                                                                                     |
| **Estimated Removable LOC**        | ~15,000 lines (29% of codebase)                                                         |
| **Estimated Dependency Reduction** | 4-6 unused packages (qrcode, cloudinary [conditionally], gspread if sheets sync unused) |
| **Risk Score**                     | LOW – all candidates have 0 importers, no CI references                                 |

---

## Removal Candidates

### Tier 1 – Safe Delete (Confidence ≥ 99%)

Files with zero importers, zero CI references, and verified dead status.

| File Path                                                 | LOC      | Why Removable                                                                                                                                                                                                                           | Last Known Usage                                                                 | Confidence |
| --------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ---------- |
| `legal-paragraph-detection-engine/`                       | 5,095    | Broken duplicate of `legal_paragraph_detection_engine/`. Its `src/__init__.py` imports `utils.performance` which **does not exist** in that directory (only `text_cleaner.py` is present). The main app imports the underscore variant. | Never used by app or tests. Present since commit 2042f12.                        | 99%        |
| `_fix_adjudication.py`                                    | 38       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_editor_button.py`                                   | 31       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_eng_indent.py`                                      | 9        | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_indent_toc.py`                                      | 21       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_pdf_utils.py`                                       | 74       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_routes.py`                                          | 41       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_routes2.py`                                         | 24       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_routes3.py`                                         | 32       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_fix_test_indent.py`                                     | 63       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_write_engine_p1.py`                                     | 105      | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_write_tests_p1.py`                                      | 85       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_write_tests_p2.py`                                      | 80       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_write_tests_p3.py`                                      | 157      | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_test_toc_quick.py`                                      | 53       | Diagnostic scratch script (untracked)                                                                                                                                                                                                   | Local modification, never committed                                              | 100%       |
| `_debug_snapshot.py`                                      | 29       | Debug snapshot (untracked)                                                                                                                                                                                                              | Local tooling output                                                             | 100%       |
| `_debug_snapshot2.py`                                     | 9        | Debug snapshot (untracked)                                                                                                                                                                                                              | Local tooling output                                                             | 100%       |
| `_init_full.txt`                                          | 424      | Debug snapshot (tracked)                                                                                                                                                                                                                | AI tooling `init` output                                                         | 100%       |
| `_plan_output.txt`                                        | 1,266    | Debug snapshot (tracked)                                                                                                                                                                                                                | AI planning output                                                               | 100%       |
| `_roadmap_out.txt`                                        | 715      | Debug snapshot (tracked)                                                                                                                                                                                                                | AI planning output                                                               | 100%       |
| `_conftest_out.txt`                                       | 85       | Debug snapshot (tracked)                                                                                                                                                                                                                | AI conftest debug                                                                | 100%       |
| `_full_conftest.txt`                                      | 1,152    | Debug snapshot (tracked)                                                                                                                                                                                                                | AI conftest debug                                                                | 100%       |
| `_settings_init.txt`                                      | 342      | Debug snapshot (tracked)                                                                                                                                                                                                                | AI settings debug                                                                | 100%       |
| `_settings_routes.txt`                                    | 909      | Debug snapshot (tracked)                                                                                                                                                                                                                | AI routes debug                                                                  | 100%       |
| `all_py_files.txt`                                        | 0        | Generated file                                                                                                                                                                                                                          | Auto-generated search output                                                     | 100%       |
| `merged_summary_report.txt`                               | 356      | Generated artifact                                                                                                                                                                                                                      | AI merge output                                                                  | 100%       |
| `check_alembic.py`                                        | 68       | One-off migration checker (tracked)                                                                                                                                                                                                     | `git ls-files` refs only; **0 importers found**                                  | 98%        |
| `check_all_tables.py`                                     | 17       | One-off migration checker (tracked)                                                                                                                                                                                                     | **0 importers found**                                                            | 98%        |
| `check_check.py`                                          | 24       | One-off migration checker (tracked)                                                                                                                                                                                                     | **0 importers found**                                                            | 98%        |
| `check_db_schema.py`                                      | 43       | One-off migration checker (tracked)                                                                                                                                                                                                     | **0 importers found**                                                            | 98%        |
| `check_migration.py`                                      | 27       | One-off migration checker (tracked)                                                                                                                                                                                                     | **0 importers found**                                                            | 98%        |
| `check_schema_parity.py`                                  | 308      | Schema parity checker (tracked)                                                                                                                                                                                                         | **0 importers found**; 308 lines of dead code                                    | 98%        |
| `check_state.py`                                          | 30       | One-off state checker (tracked)                                                                                                                                                                                                         | **0 importers found**                                                            | 98%        |
| `check_tables.py`                                         | 32       | One-off table checker (tracked)                                                                                                                                                                                                         | **0 importers found**                                                            | 98%        |
| `filter_house_number.py`                                  | 204      | Data processor (tracked)                                                                                                                                                                                                                | **0 importers**; only self-refers in docstring                                   | 98%        |
| `fuzzy_dedup_stage0.py`                                   | 215      | Dedup stage0 processor (tracked)                                                                                                                                                                                                        | Only referenced in docstring of `filter_house_number.py`; **0 actual importers** | 98%        |
| `stage0_status_report.py`                                 | 29       | Status reporter (tracked)                                                                                                                                                                                                               | **0 importers found**                                                            | 98%        |
| `.pi-subagents/artifacts/`                                | 82 files | AI agent conversation transcripts                                                                                                                                                                                                       | Committed agent working state, not app code                                      | 100%       |
| `piolium/`                                                | 14 files | AI security audit artifacts                                                                                                                                                                                                             | Committed attack-surface analysis tool output                                    | 100%       |
| `.opencode/plans/`                                        | 3 files  | AI planning output                                                                                                                                                                                                                      | Generated refactor plans                                                         | 100%       |
| `.ai/PROJECT_MEMORY.md`                                   | 253      | AI project memory                                                                                                                                                                                                                       | Generated project context                                                        | 100%       |
| `AST_SKELETONIZATION.md`                                  | 1,853    | AI analysis output                                                                                                                                                                                                                      | Generated 9000+ line analysis doc                                                | 100%       |
| `ROADMAP_ALIGNMENT_REPORT.md`                             | 1,160    | AI planning output                                                                                                                                                                                                                      | Generated roadmap doc                                                            | 100%       |
| `ENGINEERING_ASSESSMENT.md`                               | 1,236    | AI analysis output                                                                                                                                                                                                                      | Generated engineering analysis                                                   | 100%       |
| `LEGAL_ENGINE_ANALYSIS_TODO.md`                           | 1,061    | AI planning output                                                                                                                                                                                                                      | Generated TODO doc                                                               | 100%       |
| `PROJECT_EVOLUTION.md`                                    | 295      | AI planning output                                                                                                                                                                                                                      | Generated evolution doc                                                          | 100%       |
| `CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md`          | 328      | AI planning output                                                                                                                                                                                                                      | Generated plan                                                                   | 100%       |
| `legal-paragraph-detection-engine/tests/unit/__init__.py` | 1,593    | Test init from broken duplicate                                                                                                                                                                                                         | Part of broken duplicate package                                                 | 100%       |
| `legal-paragraph-detection-engine/tests/unit/*.py`        | 1,500+   | Full test suite from broken duplicate                                                                                                                                                                                                   | Part of broken duplicate; underscores tests exist in canonical package           | 100%       |

**Tier 1 Total:** ~15,000 lines, 100+ files

---

### Tier 2 – Probably Removable (90–98%)

May have legitimate use or require human verification.

| File Path                        | Why Probable                                                           | Verification Needed                                               |
| -------------------------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `instance/annexures/*.txt`       | Test fixtures (small sample text), in version control but shouldn't be | Verify tests actually need these files, then move to `.gitignore` |
| `scripts/`                       | Contains data processing scripts, none imported by app                 | Verify no CI uses these                                           |
| `check_version_model.py`         | Similar pattern to other check scripts                                 | Verify unused                                                     |
| `check_version_model_simple.py`  | Similar pattern                                                        | Verify unused                                                     |
| `stage0_status_report.py` (root) | Similar pattern                                                        | Verify unused                                                     |
| `suggester.py`                   | 120 lines, search script                                               | Verify no imports                                                 |
| `commit_all_fixes.py`            | 113 lines, fix commit script                                           | Verify no imports                                                 |

---

### Tier 3 – Refactor Before Delete

Files whose responsibilities should merge or simplify.

| File Path                           | Issue                                                                                                    | Suggested Action                                                   |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `app/services/legal_engine.py`      | Thin wrapper around `LegalParagraphEngine` with unused `LegalEngineUnavailable` exception (never caught) | Collapse into direct engine usage or remove unused exception class |
| `app/inspection/routes.py`          | 41KB of code, could be split                                                                             | Consider splitting into smaller modules                            |
| `app/adjudication/routes.py`        | 36KB of code                                                                                             | Consider splitting into smaller modules                            |
| `app/case_file_generator/routes.py` | 29KB of code                                                                                             | Consider splitting                                                 |
| `app/models.py`                     | 26KB, many models                                                                                        | Consider splitting into model subpackages                          |

---

### Tier 4 – Keep

Essential app code and legitimate documentation.

| File Path                             | Reason                                                    |
| ------------------------------------- | --------------------------------------------------------- |
| `app/`                                | Main application source code (blueprints, models, routes) |
| `tests/`                              | Test suite                                                |
| `migrations/`                         | Alembic database migrations                               |
| `reports/`                            | User-generated reports (gitignored)                       |
| `docs/`                               | Legitimate project documentation (specs, LINE_ENDINGS)    |
| `README.md`                           | Project README                                            |
| `CONTRIBUTING.md`                     | Contribution guidelines                                   |
| `CHANGELOG.md`                        | Version history                                           |
| `LICENSE`                             | MIT License                                               |
| `pyproject.toml`                      | Build and dependency configuration                        |
| `requirements.txt`                    | Runtime dependencies list                                 |
| `requirements-dev.txt`                | Development dependencies                                  |
| `app/static/vendor/quill/`            | Vendored third-party (required for UI editor)             |
| `Legal_PARAGRAPH_DETECTION_ENGINE.md` | Legitimate module documentation                           |
| `line_endings_readme.md`              | Documentation                                             |

---

## Phase 2 – Dependency Analysis

| Dependency       | Usage Status                              | Reduction Path                                                              |
| ---------------- | ----------------------------------------- | --------------------------------------------------------------------------- |
| `qrcode`         | Imported by `app/pdf_assembly/`           | Verify QR codes actively used; if only for PDF samples, can be removed      |
| `cloudinary`     | Imported by evidence blueprint            | Only active when `CLOUDINARY_*` env vars set; can be conditionally imported |
| `gspread`        | Imported by `app/services/sheets_sync.py` | Verify Google Sheets sync is required; replace with direct API if possible  |
| `celery`         | Used for async tasks                      | Core to architecture, keep                                                  |
| `flask-talisman` | Security headers                          | Core security, keep                                                         |
| `flask-login`    | Session management                        | Core auth, keep                                                             |

**Estimated Dependency Reduction:** 2-4 packages (qrcode, cloudinary conditional, gspread if not strictly required)

---

## Phase 3 – Deletion Plan

### Order of Operations (safest to riskiest)

#### Step 1: Remove broken duplicate engine

```bash
git rm -r legal-paragraph-detection-engine/
```

- Impact: None (duplicate with broken imports)
- Tests affected: None
- Rollback: `git restore --staged HEAD~1 -- legal-paragraph-detection-engine/`

#### Step 2: Remove local scratch scripts

```bash
rm _fix_*.py _write_*.py _test_*.py _debug_*.py
```

- Impact: None (never committed, local only)
- Rollback: N/A (untracked files)

#### Step 3: Remove debug snapshots

```bash
git rm _init_full.txt _plan_output.txt _roadmap_out.txt _conftest_out.txt _full_conftest.txt _settings_init.txt _settings_routes.txt all_py_files.txt merged_summary_report.txt
```

- Impact: None (generated output)
- Tests affected: None

#### Step 4: Remove one-off check/search scripts

```bash
git rm check_*.py search_*.py filter_house_number.py fuzzy_dedup_stage0.py stage0_status_report.py
```

- Impact: None (no importers, no CI references)
- Tests affected: None

#### Step 5: Remove AI agent artifacts

```bash
git rm -r .pi-subagents/artifacts/
git rm piolium/
git rm .opencode/plans/
git rm .ai/PROJECT_MEMORY.md
```

- Impact: None (tool output, not source code)
- Tests affected: None

#### Step 6: Remove AI planning documents

```bash
git rm AST_SKELETONIZATION.md ROADMAP_ALIGNMENT_REPORT.md ENGINEERING_ASSESSMENT.md LEGAL_ENGINE_ANALYSIS_TODO.md PROJECT_EVOLUTION.md CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md
```

- Impact: None (outdated planning artifacts)
- Tests affected: None

#### Step 7: Remove duplicate test files from hyphenated engine

```bash
git rm legal-paragraph-detection-engine/tests/unit/__init__.py legal-paragraph-detection-engine/tests/unit/test_*.py
```

- Actually include this in Step 1 since the whole directory is being removed.

---

## Risk Assessment

| Category                  | Risk | Mitigation                                                      |
| ------------------------- | ---- | --------------------------------------------------------------- |
| Removing duplicate engine | LOW  | The hyphenated engine is broken; app imports underscore variant |
| Removing scratch scripts  | NONE | Never tracked, local only                                       |
| Removing AI artifacts     | NONE | Tool working directory, regenerated from prompts                |
| Removing planning docs    | LOW  | Not consumed by app; any important info should be in README     |
| Removing check scripts    | LOW  | Verified 0 importers, no CI references                          |

---

## Recommended Actions

1. **Immediate:** Execute Steps 1-3 to remove the most egregious bloat
2. **Verify:** Run test suite after each step
3. **Refactor:** Address Tier 3 items (legal_engine.py wrapper, large route files)
4. **Audit dependencies:** Run `pip-autoremove` or analyze imports for unused packages
5. **Update .gitignore:** Add `instance/annexures/` and tool directories

---

_End of Report_
