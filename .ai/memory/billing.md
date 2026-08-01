# Module Memory: Billing & Bill Generator

## Purpose
Billing dashboard + per-officer bill generation (WeasyPrint PDF) with Celery
async rendering and Excel export.

## Responsibilities
- **billing** (`app/billing/`): summary dashboard with filtering + Excel export
  via openpyxl (`billing_utils.py`).
- **bill_generator** (`app/bill_generator/`): generate a `Bill` PDF (per-FSO
  enforcement/surveillance counts + prices) dispatched as a Celery task; track
  `pdf_task_id`/`pdf_generated_at`.
- Public lookup `bill_generator.lookup_fbo_issues` for form autofill.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/billing/routes.py` | 3 KB | Dashboard |
| `app/billing/billing_utils.py` | 8 KB | Excel export + summaries |
| `app/billing/__init__.py` | — | `billing_bp` |
| `app/bill_generator/routes.py` | 9 KB | Form + task dispatch |
| `app/bill_generator/tasks.py` | 3 KB | Celery PDF |
| `app/bill_generator/utils.py` | 3 KB | bill-row helpers |
| `app/bill_generator/__init__.py` | — | `bill_generator_bp` |

## Public Interfaces
- `billing_bp` (`/billing`): summary, export.
- `bill_generator_bp` (`/bill_generator`): form, generate,
  `lookup_fbo_issues` (PUBLIC).

## Dependencies
Flask, WeasyPrint, Celery, openpyxl, SQLAlchemy, `app.models.Bill`.

## Configuration Files
- `instance/app.db` (SQLite dev).

## Known Issues
- `bills` uses non-PEP8 Capitalised column names (`Name`, `Enf_samp_No`,
  `TR_Value`) — kept intentionally for legacy data parity.
- Bill↔sample via `bill_sample` join table.

## Future Improvements
- Bill template localisation.

## Current TODOs
- Bill-generation regression tests (`test_bill_generator.py`).
