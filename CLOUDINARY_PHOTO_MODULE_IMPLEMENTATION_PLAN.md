# Cloudinary Photo Module — Implementation Plan

> Status: **implemented** (backend lives in `app/utils/storage.py`). No separate
> Cloudinary module exists — the photo-evidence pipeline already handles storage
> URLs, so Cloudinary is a single new backend inside the existing storage
> abstraction: `upload_photo`/`delete_photo` now branch to Cloudinary when the
> `CLOUDINARY_*` env vars are set, and fall back to R2/B2 otherwise. Routes are
> untouched.

> **Last Evaluated:** 2026-08-02
> **Overall Status:** Production-ready for adjudication photos (`InspectionPhoto`).
> **Score:** 4.3/5 (see [Evaluation Summary](#7-evaluation-summary) for details).

## 1. Evaluation: the existing photo-evidence flow

There are **two** photo storages, one of which is the Cloudinary target:

- **`InspectionPhoto`** (`app/models.py:163`) — `__tablename__ = "inspection_photos"`,
  `file_url = db.Column(db.String(500), nullable=False)`. This is the row written by
  the adjudication upload route and the Cloudinary integration point.
- **`PhotoEvidence`** (`app/models.py:330`) — `filepath` (local path). Written by the
  *inspection* upload route (`upload_photo_evidence`, line 726) via
  `process_and_stamp_image(...)` → a **local stamped file**. Not covered here
  (separate refactor; see §Out of scope).

### Upload paths
- **`upload_adjudication_photo`** (`app/inspection/routes.py:936`):
  `file_url = upload_photo(file, adjudication_id, safe_filename)` (line 967) →
  stored on `InspectionPhoto.file_url`; `delete_photo(file_url)` on error (988) /
  teardown (1011). **This already calls `storage.upload_photo`**, so a Cloudinary
  branch there is a drop-in. ✅
- **`upload_photo_evidence`** (`app/inspection/routes.py:726`): stamps locally via
  `process_and_stamp_image` → `PhotoEvidence.filepath` = local path. Does **not**
  call `storage.upload_photo` (its OCR payload also relies on the local path, so
  this can't be flipped to Cloudinary without refactoring the OCR-over-URL flow).

### Embedding
`embed_photos_as_base64` (`app/utils/pdf_utils.py:56`):
- fetches `http(s)://` URLs via `requests.get` → base64 data-URI (Cloudinary HTTPS
  URLs flow through unchanged, exactly as R2 URLs do today);
- local paths read from disk (the inspection/stamp path);
- `PDF_USE_DIRECT_URLS=true` → URL embedded directly (no fetch) — ideal for public
  Cloudinary URLs.

### Conclusion
Pointing `storage.upload_photo` at Cloudinary makes **adjudication photos**
(`InspectionPhoto.file_url`) Cloudinary-backed with **zero** changes to routes,
models, or PDF embedding — the existing branch + R2 fallback handles everything.

## 2. Environment / secret storage (the requested part)

Follows the repo convention (provider-prefixed `CLOUDINARY_*`, mirroring `R2_*`;
`R2_*` are `sync: false` dashboard secrets, only `SECRET_KEY` is `generateValue: true`).

### `.env` / `.env.example`  (added)
```dotenv
# ---------------------------------------------------------------------------
# Cloudinary (photo upload & management)
# ---------------------------------------------------------------------------
# Credentials from https://console.cloudinary.com/  →  API Keys tab.
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
# The python-cloudinary SDK also accepts the combined form (optional):
# CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>
```
`.env` is gitignored (`.gitignore`) — never committed. (Your real values are
provided via the environment / Render dashboard.)

### `render.yaml` (web + worker services)
```yaml
- key: CLOUDINARY_CLOUD_NAME
  sync: false
- key: CLOUDINARY_API_KEY
  sync: false
- key: CLOUDINARY_API_SECRET
  sync: false
```
`sync: false`, **not** `generateValue: true` — these are real console credentials,
not random app secrets.

## 3. Runtime config + backend (in `app/utils/storage.py`)

The Cloudinary branch is a set of private helpers + two call-site branches in the
existing functions (SDK imported lazily so the module always imports):

- `_cloudinary_configured()` → `True` only when all three credential env vars are set.
- `_get_cloudinary()` → lazily `import cloudinary`, `cloudinary.config(...)`, cached;
  returns `None` (and logs a warning) if the SDK is missing so callers fall back to R2.
- `_upload_to_cloudinary(file_obj, adjudication_id)` → `uploader.upload(...)` with a
  deterministic `public_id = inspections/<id>/<uuid>`; returns `result["secure_url"]`.
- `_extract_cloudinary_public_id(url)` → parses `.../upload/v<ts>/<public_id>.<ext>`.
- `_delete_from_cloudinary(url)` → `uploader.destroy(public_id)`; idempotent
  (already-absent == success, mirroring R2's NoSuchKey handling).

Wiring (two one-line branches):
- in `upload_photo`, after ext/size validation:
  ```python
  if _cloudinary_configured():
      url = _upload_to_cloudinary(file_obj, adjudication_id)
      if url is not None:
          return url
      # else: SDK missing -> fall through to R2/B2
  ```
- in `delete_photo`:
  ```python
  if file_url.startswith("https://res.cloudinary.com/"):
      return _delete_from_cloudinary(file_url)
  ```

Routes are unchanged — `upload_adjudication_photo` already calls
`storage.upload_photo` / `storage.delete_photo`, so adjudication photo upload now
hits Cloudinary automatically when configured.

### Runnable self-check (`python -m app.utils.storage`)
The `__main__` block now also asserts the public_id parser with no network:
```python
assert _extract_cloudinary_public_id(
    "https://res.cloudinary.com/demo/image/upload/v1234567890/inspections/1/abc123.png"
) == "inspections/1/abc123"
```
A live upload/delete smoke runs only when credentials are present.

## 4. Dependency
Add to `pyproject.toml` `[project.dependencies` (added):
```toml
"cloudinary>=1.40.0",  # Cloudinary photo upload (active when CLOUDINARY_* env vars are set)
```
Render installs via `pip install -r requirements.txt` which contains `-e .` → reads
pyproject deps, so `cloudinary` ships in production. (`uv` is not on PATH in this
env; if the repo is `uv sync` managed, run `uv lock` to refresh `uv.lock`.)
cloudinary 1.45.0 is already installed locally.

## 5. Security / operational notes
- API keys are **secrets** → `sync: false` in `render.yaml`, never `generateValue`,
  never committed. `.env` is gitignored.
- `secure_url` is HTTPS (public-read on res.cloudinary.com) — fine for adjudication
  PDFs embedded by URL or base64.
- For private images: upload with `type="private"` and serve signed URLs; the
  existing `PDF_USE_DIRECT_URLS` embed still works as long as the renderer can fetch.
- `upload_photo` size/ext validation runs **before** the Cloudinary branch, so the
  same 5 MB / allowed-extension guard applies.
- A failed **upload** is a write error: the caller (`upload_adjudication_photo`)
  already wraps `upload_photo` in `try/except` → 502 + rollback, which is correct.

## 6. Out of scope (deliberately not done)
- **`PhotoEvidence.filepath` (inspection photos)** — set by `process_and_stamp_image`
  as a local stamped file and consumed by the QStash OCR async payload
  (`{"file_path": filepath}`). Moving this to Cloudinary requires refactoring the
  OCR task to fetch over HTTP — a separate change. Leave as-is.
- A standalone `app/utils/cloudinary.py` module — not created; integration reuses
  the existing `app/utils/storage.py` abstraction to keep the diff minimal and the
  call sites untouched.


## 7. Evaluation Summary

### Current Status (as of 2026-08-02)

| Area | Status | Notes |
|------|--------|-------|
| **Adjudication Photos (`InspectionPhoto`)** | ✅ **Fully Cloudinary-Ready** | Upload/delete routed via `storage.py`; zero changes to routes/models. |
| **Inspection Photos (`PhotoEvidence`)** | ❌ **Not Migrated** | Requires OCR refactor (out of scope). |
| **Environment Setup** | ✅ **Complete** | `.env.example`, `render.yaml`, `pyproject.toml` updated. |
| **Fallback to R2/B2** | ✅ **Working** | If `CLOUDINARY_*` vars missing or SDK not installed, falls back to R2. |
| **PDF Generation** | ✅ **Compatible** | Cloudinary HTTPS URLs work with `embed_photos_as_base64` and `PDF_USE_DIRECT_URLS`. |
| **Testing** | ⚠️ **Incomplete** | No dedicated tests for Cloudinary paths. |

**Overall Score: 4.3/5**
- **Implementation Completeness:** 5/5
- **Code Quality:** 5/5
- **Security:** 5/5
- **Testing:** 2/5
- **Documentation:** 4/5
- **Future-Proofing:** 3/5

### Strengths
1. **Minimal Invasive Changes:** Integration reuses existing `storage.py` abstraction; no route/model changes required.
2. **Robust Fallback:** Gracefully falls back to R2/B2 if Cloudinary is misconfigured or SDK is missing.
3. **Security:** API keys are properly handled as secrets (`sync: false` in `render.yaml`, gitignored `.env`).
4. **Compatibility:** Works seamlessly with existing PDF embedding logic (HTTPS URLs fetched via `requests.get`).

### Gaps
1. **No Unit Tests:** Cloudinary helpers (`_extract_cloudinary_public_id`, `_upload_to_cloudinary`, etc.) lack dedicated tests.
2. **No Retry Logic:** Cloudinary operations lack retry mechanisms (unlike R2, which has botocore retries).
3. **No Early Credential Validation:** No startup check or health endpoint to verify Cloudinary credentials.
4. **`PhotoEvidence` Not Migrated:** Inspection photos still use local storage; requires OCR refactor.


## 8. Recommendations

### 🚀 High Priority (Do Before Production)

#### 1. Add Unit Tests for Cloudinary Helpers
- **Why:** Ensure reliability of URL parsing, upload, and delete operations.
- **How:**
  - Test `_extract_cloudinary_public_id` with various URL formats (with/without version, extensions, nested folders).
  - Mock `cloudinary.uploader.upload`/`destroy` to test `_upload_to_cloudinary` and `_delete_from_cloudinary`.
- **Example:**
  ```python
  # tests/test_storage_cloudinary.py
  def test_extract_cloudinary_public_id():
      assert _extract_cloudinary_public_id(
          "https://res.cloudinary.com/demo/image/upload/v1234567890/inspections/1/abc123.png"
      ) == "inspections/1/abc123"
      assert _extract_cloudinary_public_id(
          "https://res.cloudinary.com/demo/image/upload/inspections/1/abc123"
      ) == "inspections/1/abc123"
      assert _extract_cloudinary_public_id("https://example.com/foo.png") is None
  ```

#### 2. Add Retry Logic for Cloudinary Operations
- **Why:** Cloudinary API may have transient failures (rate limits, network issues).
- **How:** Use `tenacity` (already in the ecosystem) to retry failed uploads/deletes.
- **Example:**
  ```python
  from tenacity import retry, stop_after_attempt, wait_exponential
  
  @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
  def _upload_to_cloudinary(file_obj, adjudication_id):
      ...
  ```

#### 3. Validate Cloudinary Credentials Early
- **Why:** Fail fast if credentials are invalid (e.g., typo in `CLOUDINARY_API_SECRET`).
- **How:** Add a startup check or health endpoint.
- **Example:**
  ```python
  @app.route("/health/cloudinary")
  def health_cloudinary():
      if _cloudinary_configured():
          try:
              _get_cloudinary()  # Triggers config
              return {"status": "ok"}, 200
          except Exception as e:
              return {"status": "error", "error": str(e)}, 500
      return {"status": "not_configured"}, 200
  ```


### 📌 Medium Priority (Do Soon)

#### 4. Support `CLOUDINARY_URL` for Convenience
- **Why:** Simplify configuration by allowing a single `CLOUDINARY_URL` env var.
- **How:** Parse `cloudinary://<key>:<secret>@<cloud>` into individual components.
- **Example:**
  ```python
  def _parse_cloudinary_url(url):
      from urllib.parse import urlparse
      if not url:
          return None
      parsed = urlparse(url)
      return {
          "cloud_name": parsed.hostname,
          "api_key": parsed.username,
          "api_secret": parsed.password,
      }
  ```

#### 5. Add Metrics for Storage Backend Usage
- **Why:** Monitor which backend (Cloudinary/R2) is being used and detect fallback cases.
- **How:** Log backend selection and add Prometheus metrics (if applicable).
- **Example:**
  ```python
  logger.info("Uploading photo to %s backend", "Cloudinary" if _cloudinary_configured() else "R2/B2")
  ```

#### 6. Document Migration Path for `PhotoEvidence`
- **Why:** Inspection photos (`PhotoEvidence.filepath`) still use local storage.
- **How:** Create a separate plan to:
  1. Refactor `process_and_stamp_image` to upload to Cloudinary (or R2) instead of local disk.
  2. Update OCR pipeline to fetch images via HTTP (instead of local paths).
  3. Add a data migration script for existing local photos.


### 💡 Low Priority (Nice-to-Have)

#### 7. Add `type="private"` Option for Cloudinary
- **Why:** Support private uploads for sensitive photos.
- **How:** Add an env var (e.g., `CLOUDINARY_PRIVATE=true`) to toggle private uploads.
- **Example:**
  ```python
  upload_params = {"resource_type": "image"}
  if os.environ.get("CLOUDINARY_PRIVATE", "").lower() == "true":
      upload_params["type"] = "private"
  ```

#### 8. Add CDN Cache Busting for Cloudinary URLs
- **Why:** Bypass CDN cache when photos are updated.
- **How:** Append a query param (e.g., `?v=<timestamp>`) to Cloudinary URLs.

#### 9. Add Cleanup Script for Orphaned Cloudinary Assets
- **Why:** Avoid storage costs for unused photos.
- **How:** Script to delete Cloudinary assets not referenced in `InspectionPhoto.file_url`.


## 9. Deployment Checklist

- [ ] Set `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` in Render Dashboard (both web and worker services).
- [ ] Verify `cloudinary>=1.40.0` is installed in production (`pip show cloudinary`).
- [ ] Test adjudication photo upload (`POST /<adjudication_id>/photos`).
- [ ] Test photo deletion (`DELETE /photos/<id>`).
- [ ] Test PDF generation with Cloudinary URLs.
- [ ] Monitor logs for fallback warnings (indicates misconfiguration).
- [ ] Add unit tests for Cloudinary helpers (see [Recommendations](#8-recommendations)).


## 10. Open Questions

1. **Should `PhotoEvidence` be migrated to Cloudinary?**
   - **Pros:** Centralized storage, no local disk dependency, easier scaling.
   - **Cons:** Requires OCR refactor (currently relies on local paths).
   - **Recommendation:** Yes, but as a separate project (see [Medium Priority](#📌-medium-priority)).

2. **Should Cloudinary be the default backend?**
   - **Current:** R2/B2 is the default; Cloudinary is opt-in via env vars.
   - **Recommendation:** Keep as opt-in for now. Consider making it default if Cloudinary proves more reliable/cost-effective.

3. **Should we add rate limiting for Cloudinary uploads?**
   - **Current:** No explicit rate limiting.
   - **Recommendation:** Monitor Cloudinary API usage; add rate limiting if needed (e.g., via `tenacity` or a token bucket).


## 11. References

- [Cloudinary Python SDK Docs](https://cloudinary.com/documentation/python_image_manipulation)
- [Render Environment Variables](https://render.com/docs/environment-variables)
- [botocore Retry Configuration](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/retries.html) (for comparison with Cloudinary retries).
