# Module Memory: Services & Shared Utils

## Purpose
Cross-cutting services consumed by multiple blueprints: Google Sheets sync,
audit-log writing, FSO master-data sync, S3-compatible storage, lookup helpers,
filter helpers, and the canonical field-name contract.

## Responsibilities
- `app/services/sheets_sync.py` (`services/audit.py`) — Google Sheets sync
  service; audit-service wrappers.
- `app/utils/fso_data.py` — sync FSO names from `fso_list.md` into `fso` table
  (thread-locked at startup; `sync_fso_from_markdown()`).
- `app/utils/sync.py` — generic sync helpers.
- `app/utils/storage.py` — lazy boto3 S3 client (R2/B2) for photo upload/delete.
- `app/utils/lookup.py` — FSO/FSSAI/CE-lookup endpoints helpers.
- `app/utils/suggester.py` — section-suggestion heuristics from `fss_sections.md`.
- `app/utils/filters.py` — Jinja filters (`to_words`, `format_date`).
- `app/utils/pdf_utils.py` — PDF helpers (photo embedding).
- `app/utils/fso_data.py` — FSO sync.
- `app/utils/sections_data.py` — parse `fss_sections.md` → `SECTIONS` dict.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/services/sheets_sync.py` | 7 KB | Google Sheets sync |
| `app/services/audit.py` | 4 KB | Audit service |
| `app/utils/storage.py` | 10 KB | R2/B2 upload/delete (lazy client) |
| `app/utils/fso_data.py` | 7 KB | FSO markdown → DB sync |
| `app/utils/sync.py` | 4 KB | Generic sync |
| `app/utils/lookup.py` | 5 KB | Lookup helpers |
| `app/utils/suggester.py` | 4 KB | Section suggester |
| `app/utils/filters.py` | 3 KB | Jinja filters |
| `app/utils/pdf_utils.py` | 4 KB | PDF utilities |
| `app/utils/sections_data.py` | 1 KB | Parse fss_sections.md |
| `app/utils/sections_data.py` (root) | 2 KB | Root copy (legacy) |

## Public Interfaces
- `sync_fso_from_markdown()` → SyncResult.
- `upload_photo(...)`, `delete_photo(...)` (storage).
- `to_words`, `format_date` (Jinja filters).
- `SECTIONS`, `VALID_SECTION_IDS` (sections_data).

## Dependencies
boto3, gspread, google-auth, Pillow, num2words, SQLAlchemy.

## Configuration Files
- `R2_ACCESS_KEY/SECRET_KEY/BUCKET/ENDPOINT/PUBLIC_BASE_URL/REGION`.
- `GOOGLE_CREDENTIALS_JSON`, `SPREADSHEET_ID`.
- `fso_list.md`, `fss_sections.md`.
- `SKIP_FSO_STARTUP_SYNC` (skip startup sync).

## Known Issues
- `_client_cache`/`_ws_cache` singletons are module-level (no TTL/invalidation).
- `storage.py` raises `RuntimeError` only at call-time if creds missing — good.
- `sync.py` / `lookup.py` have `S110` (swallow-except) ruff ignores.

## Future Improvements
- Cache TTL + invalidation on sheets_sync.
- Storage client connection pooling.

## Current TODOs
- None explicitly tracked.
