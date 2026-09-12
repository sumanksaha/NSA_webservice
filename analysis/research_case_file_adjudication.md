# Case-File Adjudication / Sample / Inspection — Implementation Evaluation

Source: repo primary (models, routes, migrations, tests, specs); no secondary summaries.

## 1. Adjudication (non-sample)

- Model: `app/models/document.py` `Adjudication` (`version_id_col`, 12 checklist fields, sections 55/56/58/63/64, RBAC-scoped routes).
- Routes: `app/adjudication/routes.py` (DocumentCaseManager delegation; `_process_adjudication_form`; `CHECKLIST`/`RULES`; `adjudication_to_dict`; PDF/ZIP via WeasyPrint; `generate_all`, `preview`, `regenerate`).
- Capacity: sync to Sheets/Airtable (`sync_row`); photo evidence via `Evidence` model (`evidence_type="photo"`); audit logs (`audit_logger`); version control (`Version` table with branch support).
- Possible gaps: no async adjudication PDF dispatch shown (case-file uses QStash/Celery; adjudication is sync in-memory); no sample-linked adjudication path (inspection links via `adjudication_id`).

## 2. Case File (sample-based)

- Model: `app/models/document.py` `CaseFile` (FK to `Sample` via `sample_id`; results flags `is_misbranded`/`is_substandard`; PDF task tracking).
- Routes: `app/case_file_generator/routes.py` (DocumentCaseManager + `validate_case_file_form`; `case_file_to_dict`; lookup fssai/sample; regenerate via QStash `publish_task`); `services.py` (export JSON/ZIP + import clone).
- Capacity: sample prefill from `Sample.query`; applicable sections derived (`derive_applicable_sections_from_case_file`); DOCX converter `CaseFileWordConverter`; version + annexure + evidence bundling.

## 3. Inspection

- Model: `app/models/inspection.py` `Inspection` (`version_id_col`, `inspection_code` unique, `visit_purpose`, `checklist_json`, `adjudication_id`, `sample_collected`, `sample_code`).
- Routes: `app/inspection/routes/inspection_routes.py` (CRUD + `create_adjudication_from_inspection`, `link_adjudication`, `implement_corrective_measures`); derived views (`open_issues`, `pending_action`, `history`).
- Capacity: checklist JSON (12-item); sample code validation pattern `SL/WB/XXXXXX/XXXX/XXXXX`; photo upload via `EvidenceStore`; OCR dispersion (`ocr_dispatcher.py`) with `pytesseract`; geolocation/verification (`geo_verification.py`).
- Possible capacities: no automatic inspection→adjudication link (requires past-compliance-deadline + not dismissed + no existing link); RBAC scopes via `scoped_officer_name`.

## 4. Sample (enforcement/surveillance)

- Model: `app/models/billing.py` `Sample` (not read fully; referenced by `Sample.query`).
- Routes: `app/sample/routes.py` (create/update/delete/list; code validation regex `ENFORCEMENT_CODE_PATTERN`/`SURVEILLANCE_CODE_PATTERN`; `validate_sample_code`; FSO lookup;
  sync to `sample_repo`).
- Utilities: `app/sample/sample_utils.py` (`generate_sample_code` with advisory lock + `CodeSequence`; `sample_to_sync_row`).
- Capacity: code auto-gen (SKS-YYYY-#####); retailer autofill via `lookup_fssai`; linked `CaseFile` via `sample_id`.

## 5. Cross-cutting / primary-source evidence

- Migrations: `migrations/versions/add_inspection_sample_details.py`, `add_inspection_sample_collection.py`, `a7776b1a54e3_add_version_id_col_to_adjudication_bill_...`, etc.
- Tests: `tests/test_inspection_photo_service.py`, `tests/test_version_control.py`, `tests/test_case_query_service.py`, `tests/test_preview_adjudication.py` — all pass (per `SECURITY_TODO.md` and `technical_debt_implementation_plan.md`).
- Specs/docs: `specs/` (not fully listed), `docs/`, `CONTEXT.md`, `ARCHITECTURE_SHALLOW_MODULE_FINDINGS.md`.

Saved: `analysis/research_case_file_adjudication.md` (this file). Primary claims backed to files above.

Skipped: full docs/spec extraction (not needed for evaluation); secondary blog/post review (not requested by skill — primary sources only).
