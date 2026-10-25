# Offline Capture Design — NSA Webservice

**Status:** Proposed design note / spec (no code yet)
**Scope:** Phase 1 first version — offline inspection forms, photo + GPS queue, sync on reconnect.
**Out of scope:** offline adjudication, document generation, Background Sync API.

## 1. Context

The repo today is server-rendered Jinja with Flask-Login session auth and no offline data capability:

- **PWA exists** (`docs/PWA_IMPLEMENTATION.md`) but is installable only — `ENABLE_PWA=false`, service worker caches only the anonymous app shell and never caches API/auth. No IndexedDB/local store, no Background Sync.
- **Session:** `session.permanent = True` at login (`app/auth/routes.py:126`). Flask's default `PERMANENT_SESSION_LIFETIME` is 31 days (no `PERMANENT_SESSION_LIFETIME` configured anywhere in the repo — NOT 30 minutes). Real expiry trigger is the `APP_GENERATION` mismatch check in `app/shared/rbac.py` which invalidates sessions on every server restart/redeploy. So a worker returning from a site visit can lose their session on redeploy, with no re-auth path that preserves unsynced captures.
- **RBAC (~30% per README, actually further):** `app/shared/rbac.py` `enforce_rbac` before_request hook, `ROLE_BLUEPRINTS` (admin bypass / fso), `scoped_officer_name`, `case_visible_to_user`, `ensure_roles`, `backfill_admin_role`. Role/UserRole tables + `users.fso_name` migrations exist. Admin Add-User form has Role/FSO dropdowns. `tests/test_rbac.py` — 44/44 pass. What does NOT exist: a `@role_required` per-route decorator (the gate is a global before_request hook), comment API/UI (`app/comments/` routes exist but `@role_required` is missing), and an explicit role-assignment UI beyond the Add-User form.
- **Identifiers:** `INSP-YYYY-####` allocated server-side by `InspectionCodeGenerator` (`app/inspection/code_generation.py`) via an atomic `CodeSequence` increment. No client-side/provisional ID mechanism.
- **Conflict handling:** SQLAlchemy `version_id` optimistic locking on `Inspection`/`Adjudication`/`Bill` → `StaleDataError → 409` exists across routes. Phase 17 Supabase sync (`app/sync/`) has its own separate `SyncState.sync_version` counter + `SyncConflict` + `resolve_conflict(winner=local|remote)`. The two concurrency mechanisms (business `version_id` vs sync `sync_version`) do not currently interop.
- **Photos/GPS:** `app/inspection/photo_service.py` EXIF GPS extraction (`_extract_exif_gps`) + form/EXIF/0.0 coordinate fallback + geo-verification pipeline (`verification_service.py`, `geo_verification.py`, `ip_verification.py`, `distance_verification.py`). `PhotoEvidenceScorer` runs at upload (`app/inspection/photo_scoring.py`, ADR-0011, 44 tests). `captured_at` is an upload param but no separate device-capture-time vs sync-time distinction is persisted.
- **Phase 17 sync tables** exist: `add_phase17_sync_tables.py` migration → `sync_state`, `sync_conflicts`, `sync_log`. Sync blueprint registered (`/sync`), dashboard template exists. Tests exist for orchestrator/fallback but **no test for `supabase_sync.py` itself**.

**Session-reauth constraint:** A re-auth path that doesn't lose unsynced captures means queued captures must live outside the Flask session (device local store) and be re-associated after login. `APP_GENERATION` invalidation on redeploy must not purge the device queue.

## 2. Non-goals (out of scope for v1)

- Offline adjudication and document generation.
- Background Sync API (Safari does not support it — sync runs foreground-on-reconnect).
- Multi-user shared-device data isolation beyond per-FSO scoping (handled by RBAC `scoped_officer_name`).
- `/api/v2` FastAPI gateway offline path.

## 3. ID Reconciliation Rule (provisional → server-allocated)

**Problem:** `INSP-YYYY-####` is allocated server-side inside a transaction against the `code_sequence` table. A device cannot reserve a code while offline without risking collisions with concurrently created inspections elsewhere.

**Rule:**
1. A locally-captured inspection is given a **provisional ID** `PEND-<deviceId>-<utcNowMs>-<random4>` at capture time, stored in the device local store, and written to the `Inspection` row as a new `provisional_id` column (nullable, indexed).
2. `inspection_code` (`INSP-YYYY-####`) remains `NULL` locally until sync. Only the server allocates it via `InspectionCodeGenerator` at push time.
3. On push, the server maps `provisional_id → real inspection id`, allocates `inspection_code`, persists the row, and returns the real id + code to the device in the sync response.
4. The device updates its local `provisional_id` row to point at the real `id`/`inspection_code` and marks it synced.
5. Pull direction: any server-created records the device doesn't have get pulled normally (they already have `inspection_code`).

**Invariant:** `inspection_code` is unique and server-issued; `provisional_id` is device-issued and unique per device. A record must never have `inspection_code` populated locally before the server confirms it.

**Files to touch (no code yet):** `app/models/inspection.py` (add `provisional_id` column + migration), `app/inspection/code_generation.py` (expose allocation-only entrypoint that does NOT require a full inspection), `app/sync/supabase_sync.py` (reconcile provisional ids on push/pull), device-side local store schema.

## 4. Sync-Conflict Policy (spec for Phase 17 conflict work)

**Policy (single source of truth):**

### 4.1 Two version counters, one rule
- Business `version_id` (SQLAlchemy optimistic locking) guards **within-session** concurrent edits → `StaleDataError → 409`.
- Sync `sync_version` (in `SyncState`) guards **push/pull** divergence against the remote → recorded as a `SyncConflict`, never silently overwritten.

### 4.2 Push (local → remote)
- A record is dirty when `SyncState` is missing or `synced_at` is older than the model's `updated_at`.
- For each dirty record: compare local `sync_version` vs remote `sync_version`.
  - Equal / remote absent → push, bump `sync_version`, set `synced_at`.
  - Mismatch → do NOT push; record a `SyncConflict` (table_name, local_id, local_version, remote_version, direction="push", remote_snapshot) and surface it in the resolve dialog.
- Batch upsert (chunked at 500) per table; conflicts excluded from the batch.

### 4.3 Pull (remote → local)
- For each remote row: if local version < remote version → apply remote row locally, bump local `sync_version`.
- If local version > remote version → record `SyncConflict` (direction="pull").
- Missing locally → insert remote row.

### 4.4 Resolution (`POST /sync/resolve-conflict/<id>`)
- `winner=local`: re-push local row with `sync_version = conflict.local_version + 1`; update local state; delete conflict row.
- `winner=remote`: apply `remote_snapshot` locally, set `sync_version = remote_version`, delete conflict row.
- Any other winner → 400. Conflict not found → 404.

### 4.5 Idempotency & ordering
- Push/pull must be idempotent: re-running after partial failure must not double-count or duplicate. Use `sync_version` comparisons as the guard.
- Push and pull are separate endpoints; clients should pull before push to pull remote changes first, then push local dirty records. Document this ordering in `PWA_IMPLEMENTATION.md`.

### 4.6 Conflict UI
- Sync dashboard (`app/sync/templates/sync/index.html`) already lists pending conflicts with Keep Local / Use Remote buttons — confirmed present. No new UI needed; the spec is the existing resolve flow.

## 5. Device-Data Purge Rules

**Security principle:** Government inspection data on a phone must be encrypted at rest and purged on logout so lost/shared devices do not become disclosure risks.

### 5.1 Encryption at rest
- All queued captures (inspection forms, photos, GPS points, evidence payloads) stored on the device must be encrypted at rest using a key derived from the user's session/login credential, never stored plaintext on disk.
- The encryption key must NOT survive logout: derive it from the authenticated session so logout destroys it (key = KDF(session_secret + user-supplied passphrase or device-bound secret)).

### 5.2 Purge on logout
- On explicit logout (`app/auth/routes.py` logout) AND on session expiry/invalidation (`APP_GENERATION` mismatch in `app/shared/rbac.py:57-75`):
  1. Clear the device local store (IndexedDB / localStorage / file store) of all queued captures and the derived encryption key.
  2. Invalidate any pending-sync tokens tied to that session.
  3. Do NOT purge server-side records — only device-local copies.

### 5.3 "Unsynced" state visibility
- Because Safari may evict storage under pressure, the UI must show a visible "unsynced" count/badge (reusing the existing `dirty_counts` concept from `/sync` dashboard) so a worker knows captures are at risk before logout.

### 5.4 Re-auth path
- After logout/expiry, re-login must be possible without network (local credential check against a device-stored hash), and the device queue must be re-associated to the new session via the user id — **but only if the same device key is still valid**. If the device store was purged, the queue is gone (accepted loss for security); if it survived, captures re-attach on next sync.

## 6. Photos and GPS (device capture time vs sync time)

- Preserve device `captured_at` (when the photo/GPS was actually taken) as a column distinct from `created_at`/`synced_at` (server sync time). Do not overwrite `captured_at` on push/pull.
- `photo_scoring` runs at upload server-side; queued photos scored after sync. UI must show a "pending scoring" advisory on queued photos until `PHOTO_SCORING_ENABLED` assessment completes post-sync.

## 7. First Version Scope (narrow)

- Offline inspection form (fields only, no adjudication).
- Photo + GPS queue with `captured_at` + `provisional_id`.
- Foreground sync on reconnect (pull → push → resolve conflicts).
- Visible "unsynced" badge.
- Logout purge rule.

**Deferred:** offline adjudication, document generation, Background Sync API, multi-device conflict merging beyond local-wins/remote-wins.

## 8. Open questions (to resolve before build)

- Device crypto library / KDF choice (Web Crypto API in browser, or SQLite with SQLCipher).
- Whether `provisional_id` lives on `Inspection` or a separate device-queue table.
- Session-lifetime correction: confirm intended expiry (default 31 days vs. the user's 30-minute claim) and set `PERMANENT_SESSION_LIFETIME` explicitly if 30 minutes is intended.

## 9. Acceptance (for Phase 17 conflict work)

- `tests/test_supabase_sync.py` added (currently missing) covering push/pull/conflict/resolve.
- `provisional_id` column + migration on `Inspection`.
- `POST /sync/resolve-conflict/<id>` unchanged (already implemented) but exercised against provisional-id rows.
- Push preserves `captured_at` and does not allocate `inspection_code` client-side.
- Logout/APP_GENERATION restart clears device local store (device-side test).