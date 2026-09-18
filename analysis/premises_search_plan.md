# Note Section / Premises Search Plan — Lazy Implementation

Question: Investigate premises "XXXXXX" from Supabase license/registration table; present checklist; FSO selects; system prepares items to check.

Source (primary): `app/sync/supabase_sync.py` (client access), `app/inspection/routes/inspection_routes.py` (checklist fields), `app/shared/context_derivers.py` (checklist rules: clean_premise, license_display, etc.). No secondary sources.

Plan (lazy ladder):

1. **Query Supabase** (actual source): Tables `fssai_licenses` / `fssai_registrations`. Use `get_sync_service().get_client()` (from `app/sync/supabase_sync.py`). Search `company_name` with `ilike('%XXXX%')` on both tables. Merge results; dedupe by `company_name` + `full_address`; allow multi-select if matches >1.
2. **Present**: Checkbox list (reuse inspection template). Auto-select if 1 match.
3. **Select**: POST selected IDs (store `license_no` or `registration_no`); load checklist rules from `context_derivers.py`; output checklist.

Skipped: custom search index, async search, new SQL table, secondary docs. Add when query is slow (>500ms) or when multi-user concurrency requires it.
