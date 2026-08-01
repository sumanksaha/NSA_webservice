# Module Memory: Testing

## Purpose
Pytest-based test suite covering model behaviour, code generation, billing
export, inspection code/deadline logic, inspection model, derived-state queries,
adjudication linkage, and cross-module integration. Includes a route-collision
regression guard.

## Responsibilities
- 11 canonical test files in `tests/` (step-based functional + module-specific).
- Legal engine has its own `legal_paragraph_detection_engine/tests/unit/`
  (9 pytest modules, pytest-9.1.1) — separate suite.
- Route-collision guard ensures no duplicate Flask routes.

## Main Source Files
| File | Size | Coverage |
|------|------|----------|
| `tests/test_step1.py` | 9.9 KB | FSO model, markdown sync, Sample model, code gen |
| `tests/test_step2.py` | 17.5 KB | Billing utils, Excel export, filtering |
| `tests/test_step3.py` | 14 KB | Inspection model, code gen, deadline calc |
| `tests/test_step4.py` | 28.7 KB | Derived-state queries, dismiss, adjudication |
| `tests/test_step5_integration.py` | 23.7 KB | Cross-module integration |
| `tests/test_route_collisions.py` | 1 KB | Duplicate-route regression |
| `tests/test_bill_generator.py` | 14 KB | Bill generation |
| `tests/test_metadata_extractor.py` | 14.5 KB | Metadata extraction |
| `tests/test_document_loader.py` | 14.3 KB | Document loading |
| `tests/test_ocr_pipeline.py` | 12 KB | OCR |
| `tests/test_document_cleaner.py` | 14 KB | Cleaning |
| `tests/test_pdf_photo_embedding.py` | 13.5 KB | PDF edge cases |
| root `test_*.py` | — | ad-hoc (test_*, run_fixes_test) |
| `legal_paragraph_detection_engine/tests/unit/test_*.py` | 5–10 KB each | 9 unit tests |

## Public Interfaces
- `pytest` (config in `pyproject.toml` → `[tool.pytest.ini_options]`).
- `testpaths = ["tests"]`.

## Dependencies
pytest (>=7.4), pytest-cov, pytest-xdist, pytest-flask; black/ruff/mypy/bandit
for lint; vulture (dead-code), py-spy (profiling).

## Configuration Files
- `pyproject.toml` `[tool.pytest.ini_options]` (addopts, filterwarnings).
- `pyproject.toml` `[tool.coverage]` (source=app, omit patterns, html/xml).
- `.pre-commit-config.yaml` (hooks).

## Known Issues
- No formal end-to-end test suite yet (only module + integration tests).
- Root `test_fbo_issue.py` (38 KB) is ad-hoc and large.
- Many root-level `check_*.py` / `verify_*.py` scripts are manual validation
  helpers, not part of the pytest suite.

## Future Improvements
- Full end-to-end flow test (FSO → Inspection → Sample → CaseFile → Adjudication
  → Bill → FBO-issue).
- CI gating on pytest + coverage threshold.

## Current TODOs
- End-to-end test suite (Phase 1 hardening).
