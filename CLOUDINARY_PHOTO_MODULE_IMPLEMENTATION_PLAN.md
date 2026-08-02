# Cloudinary Photo Module — Implementation Plan

> Status: **implemented** (backend lives in `app/utils/storage.py`). No separate
> Cloudinary module exists — the photo-evidence pipeline already handles storage
> URLs, so Cloudinary is a single new backend inside the existing storage
> abstraction: `upload_photo`/`delete_photo` now branch to Cloudinary when the
> `CLOUDINARY_*` env vars are set, and fall back to R2/B2 otherwise. Routes are
> untouched.

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
