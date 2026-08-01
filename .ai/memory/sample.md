# Module Memory: Sample Management

## Purpose
Track food-sample collection, lab submission, analyst reports, and unique code
generation (race-safe via `CodeSequence`).

## Responsibilities
- Create samples with auto-generated, unique `sample_code`.
- Track retailer FSSAI/name, collection → submission → lab chain.
- Code generation uses `CodeSequence.next_value` (PG advisory locks / SQLite retry)
  for concurrency safety across workers.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/sample/routes.py` | 13 KB | Main blueprint |
| `app/sample/sample_utils.py` | 3 KB | code-gen / helpers |
| `app/sample/__init__.py` | — | `sample_bp` Blueprint |

## Public Interfaces
- `sample_bp` (prefix `/sample`): CRUD + `lookup_retailer` (PUBLIC).
- `Sample` model (`sample` table): `sample_code` (unique), `fso_name`,
  `collection_date`, `submission_date`, `retailer_*`, `billed`.

## Dependencies
Flask, SQLAlchemy, `app.models.CodeSequence`.

## Configuration Files
- `instance/app.db` (SQLite dev); `DATABASE_URL` (PG prod).

## Known Issues
- `sample_utils.py` uses `random` (ruff `S311` ignore).
- `billed` flag drives billing dashboard filtering.

## Future Improvements
- Sample→CaseFile linking UI polish.

## Current TODOs
- Sample-code sequence migration to PostgreSQL advisory-lock path (Level 5).
