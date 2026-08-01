# Module Memory: Case File Generator

## Purpose
Generate legal case files for sample-based violations (misbranded/substandard
food) via WeasyPrint PDF rendering, with Celery async task dispatch.

## Responsibilities
- Present the sample-based case-file form (manufacturer/retailer parties,
  sample details, lab results, applicable sections).
- Derive `applicable_sections` (51 substandard / 52 misbranded) and
  `same_entity` (manufacturer == retailer by FSSAI) via
  `app/shared/context_derivers.py`.
- Render PDF with embedded sample photo (base64 or direct URL) via
  `app/utils/pdf_utils.py` + `app/utils/storage.py`.
- Dispatch PDF generation as Celery task; track `pdf_task_id` / `pdf_generated_at`.
- Emit `case_file_generator.lookup_sample` and `list_samples_for_datalist`
  (both PUBLIC) for form autofill.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/case_file_generator/routes.py` | 20 KB | Form + rendering |
| `app/case_file_generator/tasks.py` | 4 KB | Celery PDF generation |
| `app/case_file_generator/__init__.py` | — | `case_file_generator_bp` |

## Public Interfaces
- `case_file_generator_bp` (prefix `/case_file_generator`): `index`,
  `lookup_sample` (PUBLIC), `list_samples_for_datalist` (PUBLIC).

## Dependencies
Flask, WeasyPrint, Celery, SQLAlchemy, `app.shared.context_derivers`,
`app.utils.pdf_utils`, `app.utils.storage`.

## Configuration Files
- `DISABLE_PDF_GENERATION=true` (no-GTK systems); `PDF_USE_DIRECT_URLS`.
- GTK libs required by WeasyPrint (see render.yaml buildCommand).

## Known Issues
- PDF generation depends on system GTK/Pango libs; Render build installs them
  in the build command.
- `pdf_task_id`/`pdf_generated_at` columns excluded from audit diffs
  (`audit_hooks._EXCLUDED_COLUMNS`).

## Future Improvements
- Async rendering with progress feedback to UI.

## Current TODOs
- Integration tests for PDF embedding edge cases (see `test_pdf_photo_embedding.py`).
