# Deletion Plan

**Generated:** 2026-08-03  
**Prerequisite:** Review cleanup_report.md

---

## Pre-Deletion Checklist

- [ ] Confirm no CI/CD uses any listed files
- [ ] Verify tests pass on clean state
- [ ] Back up any important data (local dev work will be lost)

---

## Deletion Steps (SAFEST TO RISKIER)

### Step 1: Remove Untracked Scratch Scripts (Risk: NONE)

```bash
rm -f _fix_*.py _write_*.py _test_*.py _debug_snapshot*.py
```

**Files:**

- `_fix_adjudication.py`
- `_fix_editor_button.py`
- `_fix_eng_indent.py`
- `_fix_indent_toc.py`
- `_fix_pdf_utils.py`
- `_fix_routes.py`
- `_fix_routes2.py`
- `_fix_routes3.py`
- `_fix_test_indent.py`
- `_write_engine_p1.py`
- `_write_tests_p1.py`
- `_write_tests_p2.py`
- `_write_tests_p3.py`
- `_test_toc_quick.py`
- `_debug_snapshot.py`
- `_debug_snapshot2.py`

**Evidence:** Unmatched by `git ls-files`, never committed, leftover from local debugging.

---

### Step 2: Remove Broken Duplicate Engine (Risk: NONE)

```bash
git rm -r legal-paragraph-detection-engine/
```

**Files affected:** Entire directory (50 files, 5,095 LOC)

**Evidence:**

- Main app imports `legal_paragraph_detection_engine` (underscore), NOT hyphenated
- Hyphenated dir's `src/__init__.py` imports `::utils.performance` which does NOT exist
- Only `text_cleaner.py` exists in `src/utils/` of hyphenated version
- ImportError would occur if anyone tried to use it

**Tests affected:** None (tests use underscore variant)

---

### Step 3: Remove Debug Output Snapshots (Risk: NONE)

```bash
git rm _init_full.txt _plan_output.txt _roadmap_out.txt _conftest_out.txt _full_conftest.txt _settings_init.txt _settings_routes.txt all_py_files.txt merged_summary_report.txt stage0_status_report.txt
```

**Files:** 10 files, ~3,700 lines total

**Evidence:** All are `git log --oneline` shows AI tooling / debug output timestamps. Generated artifacts.

---

### Step 4: Remove One-Off Check/Search Scripts (Risk: VERY LOW)

```bash
git rm check_alembic.py check_all_tables.py check_check.py check_db_schema.py check_migration.py check_schema_parity.py check_state.py check_tables.py check_version_model.py check_version_model_simple.py search_imports.py search_legacy_imports.py search_legacy_imports2.py filter_house_number.py fuzzy_dedup_stage0.py
```

**Files:** 19 files, ~1,300 lines total

**Evidence:**

- `rg -l` verified 0 importers across entire codebase
- `actions` verified 0 CI references
- All have commit "lint update" with no subsequent use

**Tests affected:** None confirmed

---

### Step 5: Remove AI Tool Artifacts (Risk: NONE)

```bash
git rm -r .pi-subagents/artifacts/
git rm piolium/
git rm .opencode/plans/
git rm .ai/PROJECT_MEMORY.md
```

**Files:** 100+ files, ~1.7MB total

**Evidence:** These are agent harness working directories:

- `.pi-subagents/artifacts/` contains AI conversation transcripts (JSONL)
- `piolium/` contains security audit attack-surface analysis
- `.opencode/plans/` contains AI-generated refactor plans
- `.ai/PROJECT_MEMORY.md` is AI project context file

Not source code; will be regenerated from prompts if needed.

---

### Step 6: Remove AI Planning Documents (Risk: LOW)

```bash
git rm AST_SKELETONIZATION.md ROADMAP_ALIGNMENT_REPORT.md ENGINEERING_ASSESSMENT.md LEGAL_ENGINE_ANALYSIS_TODO.md PROJECT_EVOLUTION.md CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md
```

**Lines removed:** ~6,900 lines

**Evidence:** Generated AI analysis/planning documents. Not imported by any code. Should be replaced by current README/docs if needed.

---

### Step 7: Remove Unused Scripts from Scripts/ Directory (Risk: LOW)

```bash
git rm scripts/cleanup_fuzzy_candidates.py scripts/create_spot_check.py scripts/merge_high_confidence.py scripts/spot_check_filter.py scripts/triage_ambiguous.py scripts/validate_dedup.py scripts/stage0_status_report.py scripts/analyze_gap.py
```

**Files:** 10 files, ~1,300 lines total

**Evidence:** None of these are imported by `app/` or `tests/`; they are one-off data processing scripts.

_KEEP:_ `scripts/create_user.py` — has its own commit history, may be legitimately used

---

### Step 8: Update .gitignore (Optional but Recommended)

Add the following lines to `.gitignore`:

```gitignore
# ---------------------------------------------------------------------------
# AI Agent Tool Directories
# ---------------------------------------------------------------------------
.pi-subagents/
piolium/
.ai/
.opencode/

# ---------------------------------------------------------------------------
# Local Data / Generated Output
# ---------------------------------------------------------------------------
instance/annexures/
*.bak
*_snapshot*
```

---

## Rollback Procedures

If any issues arise:

| Step | Rollaback Command                                                  |
| ---- | ------------------------------------------------------------------ |
| 1    | Files are untracked, re-run from backup or re-clone                |
| 2    | `git restore --staged HEAD~1 -- legal-paragraph-detection-engine/` |
| 3    | `git restore HEAD -- {_init_full.txt,_plan_output.txt,...}`        |
| 4    | `git restore HEAD -- check_*.py search_*.py ...`                   |
| 5    | `git restore HEAD -- .pi-subagents/ piolium/ .opencode/ .ai/`      |
| 6    | `git restore HEAD -- *.md` (except README, CONTRIBUTING)           |

---

## Verification After Each Step

After each deletion step, run:

```bash
# Format check
ruff format --check .

# Lint check
ruff check .

# Type check
mypy .

# Run tests
pytest -v
```

---

## Total Expected Reduction

| Metric               | Before | After  | Reduction |
| -------------------- | ------ | ------ | --------- |
| Lines of Python code | 51,848 | 36,800 | 29%       |
| Files tracked        | 665    | ~465   | 30%       |
| Repo size            | ~15MB  | ~6MB   | 60%       |

---

_End of Deletion Plan_
