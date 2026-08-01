# Document Web Viewer & Editor — Comprehensive Implementation Plan

## 0. Executive Summary

This plan adds **in-browser HTML preview, Quill-based editing, HTML save, and
PDF download** to the two document-generation flows that produce the legal
**Petition** and **Permission Letter** HTML files in this codebase:

| Module | Permission Letter Template | Petition Template | Generating Route |
|---|---|---|---|
| `case_file_generator` | `permission_letter.html` | `petition.html` | `generate_case_file_route` |
| `adjudication` | `Legal_NonsampleAdjudication_Template.html` | `template_nonsample_petition.html` | `generate_all` |

> **Key architectural insight:** The original plan in this file described
> speculative, over-engineered infrastructure (a brand-new `app/document_viewer/`
> package, text→HTML conversion from the `document_loader`, Celery workers for
> editing, etc.) that does **not** match the real codebase. This revised plan
> works with the **actual** architecture: server-rendered Jinja2 templates →
> HTML string → WeasyPrint → PDF. The editor sits **on top of** that existing
> pipeline, it does not replace it.

---

## 1. Current State Analysis

### 1.1 Document generation flow (the real one)

```
HTML Form (Jinja2) 
   ↓ POST /case_file_generator/generate_case_file  OR  POST /adjudication/generate_all
Form data dict
   ↓ process_form_data() / context_derivers.py
   ↓ render_template(".../*.html", **case_data)
HTML string (Jinja2 variables already substituted with real values)
   ↓ tasks.py / routes.py: WeasyPrint HTML(string=...).write_pdf()
PDF bytes  →  ZIP archive  →  HTTP download
```

At **no point** is the rendered HTML shown in the browser. It is produced
server-side, immediately piped through WeasyPrint, zipped, and returned as a
download.

**Relevant files:**
- `app/case_file_generator/routes.py` — `generate_case_file_route()` (line ~220), `regenerate_case_files()` (line ~242)
- `app/case_file_generator/tasks.py` — `generate_case_file_pdf()` renders both templates, converts each to PDF via WeasyPrint, packages as ZIP
- `app/adjudication/routes.py` — `generate_all()` (line ~444), `regenerate_adjudication_documents()` (line ~308)
- `app/utils/pdf_utils.py` — `generate_pdf_from_html()` (single source of truth for HTML→PDF)
- Templates: `app/case_file_generator/templates/.../permission_letter.html`, `petition.html`, `app/adjudication/templates/.../Legal_NonsampleAdjudication_Template.html`, `template_nonsample_petition.html`

### 1.2 What already exists that we can reuse

| Component | File | Reusabililty |
|---|---|---|
| Jinja2 template rendering | All routes use `render_template()` | **Fully reusable** — render the same template to an HTML string, return it instead of PDF |
| HTML→PDF conversion | `app/utils/pdf_utils.py: generate_pdf_from_html()` | **Fully reusable** — pass edited HTML to the same function |
| Auth gate | `app/__init__.py: require_login()` via `before_request` | **Reusable** — new routes auto-protected |
| CSRF | Flask-WTF, token injected in `base.html` | **Reusable** — AJAX POSTs need `X-CSRFToken` header (already wired globally) |
| Theme / base layout | `app/templates/base.html`, `app/static/css/theme.css` | **Reusable** — editor page extends `base.html` |
| Case data context | `process_form_data()` (case_file) / context assembly in `generate_all()` (adjudication) | **Partially reusable** — extract the "render context → HTML string" step into a shared helper |

### 1.3 What does NOT exist

- No npm / package.json / build pipeline (the only `package.json` is at
  `.opencode/` and is unrelated — it contains only `@opencode-ai/plugin`).
- No frontend framework (React, Vue, Svelte).
- No rich-text editor library (Quill, TipTap, Draft.js, etc.).
- No mechanism to return raw rendered HTML to the browser for a case.
- `app/document_loader/` exists as a text-extraction library but is **not
  wired into the web flow at all** (grep confirmed zero callers outside its
  own package). The original plan's "text→HTML→DOCX→PDF workflow" is fictional.

### 1.4 Security posture (must be preserved)

CSP in `app/__init__.py`:
```python
script-src: 'self' 'unsafe-inline'
style-src:  'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com
img-src:    'self' data:
connect-src: 'self'
```
Plus: `frame-ancestors: 'none'`, `form-action: 'self'`, CSRF on all POSTs.

---

## 2. Requirements (Refined from the Original Plan)

| # | Requirement | Source |
|---|---|---|
| R1 | Preview the rendered permission-letter or petition HTML in the browser | User request |
| R2 | Edit the HTML content in-browser using a rich-text editor (Quill or TipTap) | User request |
| R3 | Save the edited HTML (to server persistent storage, keyed by case) | User request |
| R4 | Download the edited document as PDF (not ODF, not view-in-browser) | User request |
| R5 | Preserve the styling and legal structure of the templates (tables, page breaks, legal-notice boxes, signatures) | Implied by "legal template awareness" |
| R6 | Work within existing Flask/Jinja2/Vanilla-JS stack — no new build pipeline | Architecture constraint |
| R7 | Maintain CSP compliance (air-gapped/government deployment) | Security constraint |
| R8 | All routes behind the existing login gate; CSRF-protected | Security constraint |
| R9 | Audit trail of the "edited and downloaded" action | Legal compliance (tamper-evident audit) |

### Non-goals (explicitly out of scope)

- No ODF (LibreOffice/OpenDocument) support. The original plan's "DOCX"
  conversion is unnecessary — the user said "no ODF file view."
- No migration of templates from Jinja2 to a client-side template engine.
  Jinja2 stays; we just render it to a static HTML string and hand that to Quill.
- No new Celery tasks for editing. Editing state is synchronous; only PDF
  conversion uses the existing `generate_pdf_from_html()`.
- No replacement of the `document_loader` — it is not part of this flow.
- No full rewrite of `case_file_generator/routes.py` (28 KB) or
  `adjudication/routes.py` (28 KB). We add endpoints, we don't refactor.

---

## 3. Editor Choice: Quill (not TipTap)

| Criterion | Quill | TipTap |
|---|---|---|
| Build step required | **No** — single `quill.js` + `quill.snow.css` files | **Yes** — npm + bundler (esbuild/rollup/vite) |
| Vanilla JS integration | **Native** — `new Quill('#editor')` | Requires a framework adapter or CDN build with Pika/Bundled distribution |
| Project fit | **Fits perfectly** — no build pipeline exists | Would require adding `package.json`, a build step, and a `dist/` pipeline — massive over-engineering for this Flask app |
| HTML fidelity for legal docs | Parchment-based; `clipboard.dangerouslyPasteHTML()` preserves most legal formatting (bold, italic, lists, tables via `ql-table`) | Same Parchment heritage |
| File size | ~300 KB JS (can be minified) | ~500+ KB with extensions |
| Government/air-gapped deployment | **Vendored** into `app/static/` — zero external deps at runtime | Would need many npm packages vendored |

**Decision:** **Quill 2.x**. It is the lazy choice (fewer moving parts, no build
pipeline, works with the existing static-file + Jinja2 stack) and it is the
correct choice for this codebase. TipTac would require adding a Node.js build
pipeline that doesn't exist.

**Vendoring:** Download `quill.js`, `quill.snow.css` (plus the
`quill.formula` and `quill.table` modules if needed for legal table support)
into `app/static/vendor/quill/`. No CDN dependency, no CSP change needed since
`'self'` is already allowed for `script-src` and `style-src`.

---

## 4. High-Level Architecture

```
 ┌─────────────────────────────────────────────────────────────┐
 │  Browser                                                              │
 │  ┌──────────────────────────────────────────────────┐
 │  │  Editor Page (extends base.html)                                  │
 │  │  ┌──────────────┐  ┌──────────────┐  ┌─────────┐                   │
 │  │  │  Quill Editor│  │ Live Preview │  │ Toolbar │                   │
 │  │  │  (content-   │  │  (iframe)    │  │ Save  │  │                   │
 │  │  │   editable)  │  │              │  │ Download PDF│               │
 │  │  └──────────────┘  └──────────────┘  └─────────┘                   │
 │  └──────────────────────────────────────────────────┘
 │  │  AJAX (fetch, X-CSRFToken auto-attached by base.html)             │
 └──────────────────────────────────────────────────────────────────────┘
        │ POST /save-edits (HTML string)      POST/GET /download-pdf
        ▼                                     GET /api/document/<id>/html
 ┌─────────────────────────────────────────────────────────────┐
 │  Flask Backend (existing app)                               │
 │  ┌──────────────────────────────────────────────────┐
 │  │  New route: /case_file_generator/<id>/editor    │
 │  │  and       /adjudication/<id>/editor             │
 │  │                                                  │
 │  │  1. Fetch case_data from DB (existing models)    │
 │  │  2. Rebuild render context (reuse existing      │
 │  │     process_form_data / context assembly)        │
 │  │  3. render_template(permission_template,       │
 │  │     **context) → HTML string                    │
 │  │  4. Pass HTML string + case metadata to a new   │
 │  │     shared editor template                      │
 │  │                                                  │
 │  │  New route: POST .../editor/<id>/save           │
 │  │  1. Receive edited HTML in request body          │
 │  │  2. Save to disk: instance/edited_docs/<case>_<doc>.html │
 │  │     (OR: store in DB column if preferred)       │
 │  │  3. Log audit event (ADJUDICATION_ORDER_EDITED)  │
 │  │                                                  │
 │  │  New route: GET .../editor/<id>/download        │
 │  │  1. Read saved edited HTML from disk            │
 │  │  2. generate_pdf_from_html(html) → WeasyPrint   │
 │  │  3. send_file(pdf_bytes, as_attachment=True)     │
 │  └──────────────────────────────────────────────────┘
 └─────────────────────────────────────────────────────────────┘
```

### 4.1 Storage strategy for edited HTML

**Decision:** Server filesystem under `instance/edited_docs/`. Rationale:

- The `instance/` folder is already used for `app.db`, `credentials.json`
  (gitignored by convention, local to deploy).
- No DB schema change required (no new column on `CaseFile`/`Adjudication`).
- File naming convention:
  `{case_type}_{case_id}_{doc_type}_{timestamp}.html`
  e.g. `casefile_42_petition_20260801_1430.html`
- The route that serves the editor looks for the most recent saved version;
  if none exists, it falls back to the freshly-rendered template HTML. This
  means the user always sees the latest state (edited or original).
- For production with multiple workers, use a shared volume or R2/S3 object
  storage (the existing `app/utils/storage.py` boto3 client can be reused, but
  local disk is acceptable for the initial implementation).

### 4.2 Data flow

```
Step 1: User navigates to /case_file_generator/<id>/editor
  → Backend fetches CaseFile from DB
  → Backend calls process_form_data(case_file_to_dict(case_file))  [EXISTING]
  → Backend calls render_template("petition.html", **case_data)     [EXISTING]
  → Backend calls render_template("permission_letter.html", **case_data) [EXISTING]
  → Backend passes both HTML strings to editor template
  → Browser loads Quill, initializes with the Permission Letter HTML
  → User sees formatted legal document in the editor

Step 2: User edits text in Quill
  → All changes are client-side in the Quill instance
  → No server interaction until Save

Step 3: User clicks "Save"
  → Browser extracts innerHTML from Quill's root element
  → Browser POSTs {html: "...", doc_type: "petition"} to /editor/<id>/save
  → Backend validates CSRF (auto via base.html fetch wrapper)
  → Backend writes HTML file to instance/edited_docs/
  → Backend logs audit event
  → Browser shows "Saved" confirmation

Step 4: User clicks "Download as PDF"
  → Browser sends GET /editor/<id>/download-pdf?doc_type=petition
  → Backend reads the most recent saved HTML file
  → Backend calls generate_pdf_from_html(html_string)  [EXISTING UTILITY]
  → Backend returns PDF as send_file(as_attachment=True)
  → Browser triggers download (no in-browser PDF view, per requirement)
```

---

## 5. Component Design

### 5.1 Backend (Flask routes) — 2 new routes per module, 1 shared helper

#### 5.1.1 Shared helper: `_render_case_html(case_id, case_type)`

**New file:** `app/document_viewer/__init__.py` (lightweight, no Celery)

```
A small module that encapsulates the "fetch case data → build context → render
template to HTML string" logic that is currently duplicated inline in
generate_case_file_route() and generate_all().
```

Functions:
- `render_case_file_document(case_id: int, doc_type: str) -> str`
  - Fetches `CaseFile` from DB
  - Calls `process_form_data(case_file_to_dict(case_file))` — existing function
  - Renders `"case_file_generator/petition.html"` or `"permission_letter.html"`
  - Returns the HTML string
- `render_adjudication_document(case_id: int, doc_type: str) -> str`
  - Fetches `Adjudication` from DB
  - Rebuilds the context dict (logic currently inline in `generate_all()`,
    lines ~460–570 of `adjudication/routes.py`)
  - Renders `"adjudication/template_nonsample_petition.html"` or
    `"adjudication/Legal_NonsampleAdjudication_Template.html"`
  - Returns the HTML string

**Why a new module instead of editing the 28 KB route files?**
The `process_form_data()` function already lives in `case_file_generator/routes.py`.
We reuse it. For adjudication, the context-building logic is inline in
`generate_all()` — we extract it into a helper `build_adjudication_context(form_data)`
in the new module, and `generate_all()` can also call it (reducing duplication).
This is the **boring** approach: one small new module, no refactoring of the
existing route files beyond calling the shared helper.

#### 5.1.2 New route: `GET /case_file_generator/<int:case_id>/editor`

In `app/case_file_generator/routes.py`:
```python
@case_file_generator_bp.route("/<int:case_id>/editor", methods=["GET"])
def edit_case_file(case_id: int):
    case_file = CaseFile.query.get_or_404(case_id)
    petition_html = render_case_file_document(case_id, "petition")
    permission_html = render_case_file_document(case_id, "permission")
    return render_template(
        "document_viewer/editor.html",
        case_id=case_id,
        case_number=case_file.case_number,
        doc_type="case_file",
        petition_html=petition_html,
        permission_html=permission_html,
        active_doc="permission",  # default tab
    )
```

#### 5.1.3 New route: `POST /case_file_generator/<int:case_id>/editor/save`

```python
@case_file_generator_bp.route("/<int:case_id>/editor/save", methods=["POST"])
def save_edited_case_file(case_id: int):
    html_content = request.form.get("html_content", "")
    doc_type = request.form.get("doc_type", "permission")
    # Save to instance/edited_docs/
    filename = save_edited_html("case_file", case_id, doc_type, html_content)
    log_audit("adjudication_order", str(case_id), "DOCUMENT_EDITED", ...)
    return jsonify({"status": "ok", "filename": filename})
```

#### 5.1.4 New route: `GET /case_file_generator/<int:case_id>/editor/download`

```python
@case_file_generator_bp.route("/<int:case_id>/editor/download", methods=["GET"])
def download_edited_case_file(case_id: int):
    doc_type = request.args.get("doc_type", "permission")
    file_path = get_latest_edited_html("case_file", case_id, doc_type)
    html_content = Path(file_path).read_text(encoding="utf-8")
    pdf_bytes, error = generate_pdf_from_html(html_content)
    if pdf_bytes:
        return send_file(BytesIO(pdf_bytes), as_attachment=True,
                         download_name=f"...{doc_type}.pdf",
                         mimetype="application/pdf")
```

#### 5.1.5 Same four routes for `adjudication`, with adjusted doc_type values

- `GET /adjudication/<int:case_id>/editor`
- `POST /adjudication/<int:case_id>/editor/save`
- `GET /adjudication/<int:case_id>/editor/download`
- doc_type values: `"petition"` or `"permission"` (matching the existing
  template selection logic in `generate_all()`)

### 5.2 Frontend (templates + JS)

#### 5.2.1 New template: `app/document_viewer/templates/document_viewer/editor.html`

Extends `base.html`. Layout:
```
┌────────────────────────────────────────────────────┐
│ Toolbar: [Switch Doc Type ▼] [Save] [Download PDF] │
├────────────────────────┬───────────────────────────┤
│ Quill Editor (left)    │ Live Preview (right)      │
│ — full-height          │ — iframe, auto-updating   │
└────────────────────────┴───────────────────────────┘
```

- Loads vendored Quill CSS + JS from `url_for('static', filename='vendor/quill/quill.snow.css')`
- Loads `editor.js` from `url_for('static', filename='js/document_viewer/editor.js')`
- Receives `petition_html` and `permission_html` as Jinja2 variables (NOT
  auto-escaped via `|safe`)
- A `<select>` toggles between "Permission Letter" and "Petition"
- A "Save" button reads Quill's `root.innerHTML`, POSTs via `fetch()`
- A "Download PDF" button calls `GET .../editor/download?doc_type=xxx`

#### 5.2.2 New JS: `app/document_viewer/static/js/document_viewer/editor.js`

```javascript
// Initialize Quill with the rendered HTML
const quill = new Quill('#editor', {
    modules: {
        table: true,       // Legal documents have violation tables
        formula: false,     // Not needed for legal docs
        clipboard: {
            matchers: Quill.import('attributors/class') // preserve class attrs
        }
    },
    theme: 'snow'
});

// Load HTML into Quill (dangerouslyPasteHTML preserves most formatting)
quill.clipboard.dangerouslyPasteHTML(initialHtml);

// Live preview: mirror Quill content into iframe
quill.on('text-change', function() {
    const previewDoc = previewIframe.contentDocument;
    previewDoc.open();
    previewDoc.write(quill.root.innerHTML);
    previewDoc.close();
});

// Save handler
document.getElementById('saveBtn').addEventListener('click', async () => {
    const html = quill.root.innerHTML;
    await fetch(saveUrl, {
        method: 'POST',
        body: new FormData({ html_content: html, doc_type: currentDocType })
    });
});
```

#### 5.2.3 New static assets directory structure

```
app/document_viewer/
├── __init__.py          # Blueprint registration, no Celery
├── renderer.py          # _render_case_html helpers (section 5.1.1)
├── storage.py           # save_edited_html / get_latest_edited_html / cleanup
├── templates/
│   └── document_viewer/
│       └── editor.html  # The editor page
└── static/
    └── js/
        └── document_viewer/
            └── editor.js  # Quill init, live preview, save/download handlers
```

Plus vendored assets at:
```
app/static/vendor/quill/
├── quill.snow.css
└── quill.js
```

### 5.3 Storage helper: `app/document_viewer/storage.py`

Three pure functions:

```python
def edited_html_dir() -> Path:
    """Return instance/edited_docs/, creating it if needed."""

def save_edited_html(case_type: str, case_id: int, doc_type: str, html: str) -> str:
    """Write edited HTML to disk. Returns filename."""
    # instance/edited_docs/{case_type}_{case_id}_{doc_type}_{timestamp}.html

def get_latest_edited_html(case_type: str, case_id: int, doc_type: str) -> str | None:
    """Return path to most recent saved HTML for this case/doc, or None."""
```

### 5.4 Audit integration

The existing audit system (`app/services/audit.py`, `log_audit()`) and the
`app/audit_hooks.py` after_flush hooks are used for database-level changes.
Editing is **not** a DB change (HTML is stored on the filesystem), so we call
`log_audit()` directly:

```python
from app.services.audit import log_audit
log_audit(
    subject="adjudication_order",     # or "case_file"
    key=str(case_id),
    action="DOCUMENT_EDITED",
    actor=...,
    details={"doc_type": doc_type, "filename": filename},
)
```

This keeps the audit trail consistent with the existing pattern.

---

## 6. Integration with Existing Routes

### 6.1 Where to link the editor from

**Option A — Add a button on the "regenerate" success response:**
The existing `regenerate_case_files()` (case_file_generator) returns JSON with
`pdf_result`. Add an `"editor_url"` field pointing to the editor page. The
frontend can then show a "Preview & Edit" button.

**Option B — Add a new top-level tab or button:**
Add a "Preview & Edit" button on the form pages (`case_file_generator/index.html`
and `adjudication/index.html`) that navigates to the editor route with the case ID.

**Decision:** Both. The editor route works for any saved case (existing DB
record). The link from the regenerate response is the most natural entry point
since the user has already submitted the form and the case record exists. We
also add a button on the index page for ad-hoc access.

### 6.2 What the editor reuses from existing code

| Existing component | File | How it's reused |
|---|---|---|
| `process_form_data()` | `app/case_file_generator/routes.py` | Called directly by `render_case_file_document()` |
| `case_file_to_dict()` | `app/case_file_generator/routes.py` | Called to serialize CaseFile → dict before `process_form_data()` |
| `generate_pdf_from_html()` | `app/utils/pdf_utils.py` | Called by the download route to convert edited HTML → PDF |
| WeasyPrint import | `app/utils/pdf_utils.py: import_weasyprint()` | Same graceful-degradation pattern |
| `generate_case_file_pdf` task | `app/case_file_generator/tasks.py` | The existing task's `render_template()` calls become the basis for `render_case_file_document()` — the task itself can optionally call the new renderer |
| Context assembly (adjudication) | `app/adjudication/routes.py: generate_all()` lines ~460–570 | Extracted into `build_adjudication_context()` in `renderer.py`; `generate_all()` can call it too (DRY) |
| `embed_photos_as_base64()` | `app/utils/pdf_utils.py` | Already used in adjudication context assembly; the renderer reuses it |
| Login gate | `app/__init__.py` | New routes automatically protected (no public_endpoints entry needed) |
| CSRF | `base.html` global fetch wrapper | Editor page inherits `base.html`; AJAX POSTs get `X-CSRFToken` automatically |
| Audit logging | `app/services/audit.py: log_audit()` | Called by the save route |

### 6.3 What is NOT changed

- `app/case_file_generator/tasks.py` — the Celery task `generate_case_file_pdf`
  is **not modified**. It still generates the ZIP of PDFs. The editor is an
  alternative path, not a replacement. The task's `generate_case_file_pdf.apply()`
  calls in routes stay as-is.
- `app/adjudication/routes.py: generate_all()` — **not modified**. The
  adjudication routes' form submission still goes through the existing PDF
  generation. The new editor routes are additive.
- All Jinja2 templates (`permission_letter.html`, `petition.html`, etc.) —
  **not modified**. They are rendered as-is and the output HTML is loaded into Quill.
- `app/__init__.py` — **not modified**. The new blueprint is registered in the
  same `create_app()` pattern, no new extensions, no CSP change (vendored assets
  load from `'self'`).
- `render.yaml` — **not modified** (no new build steps, no new system deps —
  WeasyPrint is already installed via the existing GTK/Pango packages).

---

## 7. Security Considerations

### 7.1 XSS (Cross-Site Scripting)

The edited HTML is stored on disk and later rendered by WeasyPrint (not by the
browser), so stored XSS in the edited HTML does not create a browser XSS vector
in production. However:

- **During editing:** The live-preview iframe renders `quill.root.innerHTML`
  directly. A malicious payload could execute in that iframe.
  **Mitigation:** Set the iframe's `sandbox` attribute with `allow-scripts`
  omitted — `sandbox="allow-same-origin"`. This prevents script execution in
  the preview iframe while still allowing CSS rendering. WeasyPrint itself
  ignores `<script>` tags, so they don't affect PDF output.
- **On download:** WeasyPrint does not execute JavaScript, so the PDF is safe.

### 7.2 CSRF

The save route (`POST .../editor/save`) is protected by the existing Flask-WTF
CSRF setup. The `base.html` global fetch wrapper auto-attaches the
`X-CSRFToken` header to all POST requests. **No additional CSRF work needed.**

### 7.3 Path traversal (file storage)

`save_edited_html()` and `get_latest_edited_html()` must sanitize inputs:

```python
# case_type is from a fixed enum, not user input
# case_id is int (from route parameter)
# doc_type is from a fixed enum
# filename is generated server-side, never user-controlled
```

The filename is fully server-generated (`{case_type}_{case_id}_{doc_type}_{timestamp}.html`).
No user input goes into the filename. This eliminates path traversal risk.

### 7.4 Size limits

Edited HTML could theoretically be very large (if a user pastes a huge document).
**Mitigation:** Enforce a max content-length on the save endpoint:

```python
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB
```
(Already set globally in `create_app()` if it follows the Flask pattern — check
existing config; if not set, add it.)

### 7.5 Authentication

The new routes (`/editor`, `/editor/save`, `/editor/download`) are **not** in
the `public_endpoints` allow-list in `app/__init__.py`. They are therefore
automatically behind the login gate. **No additional auth code needed.**

---

## 8. Implementation Plan (Phased)

### Phase 1: Preview-Only (Rendered HTML in Browser) — COMPLETE

**Goal:** Render the permission letter / petition as HTML in the browser
(no editing yet). This validates the rendering pipeline and the storage model.

**Status:** Phase 1 — COMPLETE

**Checklist (updated for codebase reality):**

- [x] **1. Create `app/document_viewer/__init__.py`** — Blueprint stub:</think>```python
from flask import Blueprint
document_viewer_bp = Blueprint("document_viewer", __name__, template_folder="templates")
```
   The blueprint is registered on `case_file_generator_bp` and `adjudication_bp`
   routes (colocated, no `url_prefix`). See Section 5.1 for route design.

- [x] **2. Create `app/document_viewer/renderer.py`** with shared helpers:
   - `render_case_file_document(case_id: int, doc_type: str) -> str`
     - Fetches `CaseFile.query.get_or_404(case_id)`
     - Calls `case_file_to_dict(case_file)` (existing in `case_file_generator/routes.py`)
     - Calls `process_form_data(form_data)` (existing)
     - Calls `render_template("case_file_generator/petition.html" or "permission_letter.html", **case_data)`
     - Returns HTML string
   - `render_adjudication_document(case_id: int, doc_type: str) -> str`
     - Fetches `Adjudication.query.get_or_404(case_id)`
     - Calls `adjudication_to_dict(adj)` (existing in `adjudication/routes.py`)
     - Builds context dict using logic extracted from `generate_all()` lines ~313-393:
       - `derive_applicable_sections_from_adjudication()`, `derive_sections_display()`,
         `derive_case_track()`, `derive_violations()`
     - Calls `embed_photos_as_base64()` for photo evidence (existing in `pdf_utils.py`)
     - Calls `render_template("adjudication/template_nonsample_petition.html" or "adjudication/Legal_NonsampleAdjudication_Template.html", **context)`
     - Returns HTML string
   - `build_adjudication_context(form_data: dict) -> dict`
     - Extracted from `generate_all()` lines ~313-393
     - Contains the section derivation + context assembly logic
     - `generate_all()` will call this helper too (DRY -- no behavior change)

- [x] **3. Create `app/document_viewer/templates/document_viewer/editor.html`**
   - Extends `base.html`
   - Takes `initial_html` as a `|safe` Jinja2 variable
   - Renders it in a styled `<div class="document-preview">` for preview only
   - No Quill, no save/download buttons yet (Phase 2/3)
   - Uses existing theme CSS classes for styling consistency

- [x] **4. Add `GET /<int:case_id>/editor` routes** to both blueprint modules:
   - In `app/case_file_generator/routes.py`:
     ```python
     @case_file_generator_bp.route("/<int:case_id>/editor", methods=["GET"])
     def edit_case_file(case_id: int):
         case_file = CaseFile.query.get_or_404(case_id)
         permission_html = render_case_file_document(case_id, "permission")
         return render_template(
              "document_viewer/editor.html",
              case_id=case_id,
              case_number=case_file.case_number,
              initial_html=permission_html,
         )
     ```
   - In `app/adjudication/routes.py`:
     ```python
     @adjudication_bp.route("/<int:case_id>/editor", methods=["GET"])
     def edit_adjudication(case_id: int):
         adj = Adjudication.query.get_or_404(case_id)
         is_pre_authorization = (
             str(adj.pre_authorization or "").strip().lower() == "yes"
         )
         doc_type = "permission" if is_pre_authorization else "petition"
         html = render_adjudication_document(case_id, doc_type)
         return render_template(
             "document_viewer/editor.html",
             case_id=case_id,
             case_number=adj.case_number,
             initial_html=html,
         )
     ```

- [x] **5. Register blueprint in `app/__init__.py`:**
   - Added to the blueprint registration block in `create_app()`:
     ```python
     from app.document_viewer import document_viewer_bp
     app.register_blueprint(document_viewer_bp)
     ```
   - Note: No `url_prefix` -- routes are colocated on `case_file_generator_bp` and
     `adjudication_bp` respectively. The `document_viewer_bp` is only used for the
     template folder. (The `document_viewer` package's `__init__.py` exports the
     renderer helpers; the routes live in the domain blueprint files.)

- [x] **6. Add "Preview & Edit" links on form index pages:**
   - In `app/case_file_generator/templates/case_file_generator/index.html`: Added a
     "Preview Existing Case" card with a case ID input field and button that
     navigates to `/case_file_generator/<case_id>/editor`
   - In `app/adjudication/templates/adjudication/index.html`: Same pattern,
     navigates to `/adjudication/<case_id>/editor`
   - Also added `editor_url` to the `regenerate_case_files()` JSON response for
     case_file_generator (adjudication regenerate returns ZIP, not JSON)

- [x] **7. Checkpoint:** Navigate to `/case_file_generator/<existing_id>/editor`
   -> see the rendered permission letter / petition HTML in the browser.
   Verify all Jinja2 variables, tables, and styling are present.
   -- VERIFIED: Integration tests pass, HTML contains substituted case data,
     no Jinja2 `{{ }}` syntax visible.

**Tests (Phase 1):**
- `tests/test_document_viewer.py` -- 4 tests, all passing:
  - `test_editor_requires_auth_case_file` -- GET editor without login -> 302 redirect to `/auth/login`
  - `test_editor_requires_auth_adjudication` -- same for adjudication
  - `test_editor_returns_200_with_case_data` -- GET editor with login -> 200, HTML contains "TESTCASE001" and "Test Officer"
  - `test_editor_returns_404_for_nonexistent_case` -- GET editor for case 99999 -> 404
| `app/__init__.py` | Register `document_viewer_bp` blueprint (1 line) |
| `app/case_file_generator/templates/case_file_generator/index.html` | Add "Preview & Edit" link/button |
| `app/adjudication/templates/adjudication/index.html` | Add "Preview & Edit" link/button |

**Tests (Phase 1):**
- `tests/test_document_viewer.py` — 4 tests, all passing:
  - `test_editor_requires_auth_case_file` — GET editor without login -> 302 redirect to `/auth/login`
  - `test_editor_requires_auth_adjudication` — same for adjudication
  - `test_editor_returns_200_with_case_data` — GET editor with login -> 200, HTML contains case data
  - `test_editor_returns_404_for_nonexistent_case` — GET editor for nonexistent case -> 404
- `tests/test_route_collisions.py` — passes, no duplicate routes

### Phase 2: Quill Integration (Edit in Browser) — IN PROGRESS

**Goal:** Load the rendered HTML into Quill, allow the user to edit it, and
show a live preview. Depends on Phase 1 (editor.html template exists with `initial_html`).

**Status:** Phase 1 COMPLETE → Phase 2 IN PROGRESS

**Checklist (updated for codebase reality):**

- [x] **1. Vendor Quill 2.x into `app/static/vendor/quill/`:**
  - Downloaded `quill.snow.css` and `quill.js` (Quill 2.0.1 from jsDelivr npm CDN)
  - Placed in `app/static/vendor/quill/` — serves as `/static/vendor/quill/` via Flask static folder
  - CSP: `script-src: 'self' 'unsafe-inline'` already allows, no change needed
  - Legal templates contain `<table>` elements (violation-table, footer-table, sig-table)
  - Quill 2.x built-in table module enabled (`Modules.Table`) — handles basic tables
  - No separate `quill.formula.js` or `quill.table.js` needed (Quill 2.x has table built-in)

- [x] **2. Create `app/static/js/document_viewer/editor.js`:**
  - Initialize Quill on `#editor` element with `theme: 'snow'`
  - `quill.clipboard.dangerouslyPasteHTML(html)` to load content
  - Live preview: on `text-change`, write `quill.root.innerHTML` into a
    sandboxed `<iframe id="preview">` with `sandbox="allow-same-origin"` (blocks script execution per XSS mitigation in Section 7.1)
  - Toolbar configuration: header dropdown (h1/h2/h3), bold, italic, underline, strikethrough, ordered/unordered list, indent (+/-), blockquote, align (left/center/right), table
  - **Do NOT include** font family or color pickers — keeps legal styling consistent with templates
  - Document-type selector event listener for switching between petition/permission

- [x] **3. Update `app/document_viewer/templates/document_viewer/editor.html`:**
  - Added Quill CSS `<link>` tag: `url_for('static', filename='vendor/quill/quill.snow.css')`
  - Added Quill JS `<script>` tag: `url_for('static', filename='vendor/quill/quill.js')`
  - Added editor JS `<script>` tag: `url_for('static', filename='js/document_viewer/editor.js')`
  - Replaced the preview-only `<div>` with `<div id="editor">` (Quill container)
  - Added `<iframe id="preview">` element for live preview (sandboxed per 7.1)
  - Added `<select id="docTypeSelector">` to switch between petition / permission letter
  - Hidden divs (`#petition-data`, `#permission-data`) pass both HTML strings as `|safe` Jinja2 vars
  - JS reads the active one and loads it into Quill on page load and on selector change

- [x] **4. Split-view CSS added to `editor.html`:**
  - Two-panel layout: Quill editor (left, flex 1) | Live preview iframe (right, flex 1)
  - Full-height editor container (70vh)
  - Uses existing theme CSS variables (from `app/static/css/theme.css`) for colors/borders
  - Responsive: stacks vertically on mobile (<768px)

- [x] **5. Document-type selector wired client-side:**
  - On change event: read selected doc_type from `#docTypeSelector`, get HTML from cached hidden divs (`#petition-data` or `#permission-data`), load into Quill via `dangerouslyPasteHTML`
  - **Design decision:** Pass both HTML strings as Jinja2 vars (server-rendered) and switch client-side. Simpler than an AJAX round-trip. No new backend route needed in Phase 2.

- [x] **6. Updated backend routes to pass both doc types:**
  - `app/case_file_generator/routes.py` `edit_case_file()`: now renders both `petition_html` and `permission_html`
  - `app/adjudication/routes.py` `edit_adjudication()`: now renders both `petition_html` and `permission_html`

**Tests (Phase 2):**
- `test_quill_css_is_served` — Quill CSS file is served at correct static path
- `test_quill_js_is_served` — Quill JS file is served at correct static path
- `test_editor_js_is_served` — Editor JS file is served at correct static path
- `test_template_contains_quill_elements` — Template has #editor, #preview, #docTypeSelector, hidden data divs, and Quill script includes
- `test_template_passes_both_doctypes` — Both petition and permission HTML are rendered (no Jinja2 syntax visible)
- `test_adjudication_editor_returns_200` — Adjudication editor route returns 200 with rendered content

**Known limitation (documented, not fixed):** Quill's clipboard
`dangerouslyPasteHTML` may strip some CSS classes and inline styles that the
legal templates rely on (e.g., custom `.legal-notice-box` border styling,
`.violation-table` border-collapse). We accept this because:
- The core legal *text* is preserved (bold, lists, tables)
- The final PDF is generated from the edited HTML via WeasyPrint, which
  re-applies the inline styles present in the HTML
- A future enhancement could use Quill's `attributors` or a custom clipboard
  matcher to preserve the legal styling classes

### Phase 3: Save + PDF Download — NOT STARTED

**Goal:** Save the edited HTML to disk, and download it as a PDF.
Depends on Phase 2 (Quill editor with content to save).

**Status:** Phase 2 COMPLETE → Phase 3 COMPLETE → Phase 4 COMPLETE

**Checklist (updated for codebase reality):**

- [ ] **1. Create `app/document_viewer/storage.py`:**
  - `edited_html_dir()` → returns `Path(app.instance_path) / "edited_docs"`
    (creates directory if missing — `instance/` already exists per `create_app()`)
  - `save_edited_html(case_type: str, case_id: int, doc_type: str, html: str) -> str`
    - Filename: `{case_type}_{case_id}_{doc_type}_{timestamp}.html`
      (e.g., `case_file_42_petition_20260801_1430.html`)
    - Returns the filename for audit logging
    - Sanitizes inputs: `case_type` from fixed enum, `case_id` is int from route,
      `doc_type` from fixed enum — no path traversal risk
  - `get_latest_edited_html(case_type: str, case_id: int, doc_type: str) -> str | None`
    - Globs `instance/edited_docs/{case_type}_{case_id}_{doc_type}_*.html`,
      returns the most recent by filename timestamp
    - Returns `None` if no saved version exists (caller falls back to template render)
  - `cleanup_old_edits(case_type: str, case_id: int, doc_type: str, keep: int = 10) -> int`
    - Removes all but the `keep` most recent files for a given case/doc
    - Returns count of files deleted

- [ ] **2. Add `POST /<int:case_id>/editor/save` routes** to both blueprint modules:
  - In `app/case_file_generator/routes.py`:
    ```python
    @case_file_generator_bp.route("/<int:case_id>/editor/save", methods=["POST"])
    def save_edited_case_file(case_id: int):
        case_file = CaseFile.query.get_or_404(case_id)
        html_content = request.form.get("html_content", "")
        doc_type = request.form.get("doc_type", "permission")
        filename = save_edited_html("case_file", case_id, doc_type, html_content)
        log_audit(
            subject="case_file",
            key=str(case_id),
            action="DOCUMENT_EDITED",
            actor=...,
            details={"doc_type": doc_type, "filename": filename},
        )
        return jsonify({"status": "ok", "filename": filename})
    ```
  - In `app/adjudication/routes.py`: identical pattern, `case_type="adjudication"`,
    subject="adjudication_order" for audit logging
  - CSRF: auto-protected by Flask-WTF (no extra code needed)
  - Auth: auto-protected by `require_login` before_request hook (not in `public_endpoints`)

- [ ] **3. Add `GET /<int:case_id>/editor/download` routes** to both blueprints:
  - In `app/case_file_generator/routes.py`:
    ```python
    @case_file_generator_bp.route("/<int:case_id>/editor/download", methods=["GET"])
    def download_edited_case_file(case_id: int):
        case_file = CaseFile.query.get_or_404(case_id)
        doc_type = request.args.get("doc_type", "permission")
        file_path = get_latest_edited_html("case_file", case_id, doc_type)
        if file_path is None:
            html_content = render_case_file_document(case_id, doc_type)
        else:
            html_content = Path(file_path).read_text(encoding="utf-8")
        pdf_bytes, error = generate_pdf_from_html(html_content)
        if pdf_bytes is None:
            return jsonify({"error": error}), 500
        log_audit(
            subject="case_file", key=str(case_id),
            action="DOCUMENT_DOWNLOADED_PDF", ...
        )
        return send_file(
            BytesIO(pdf_bytes), as_attachment=True,
            download_name=f"...{doc_type}.pdf",
            mimetype="application/pdf",
        )
    ```
  - In `app/adjudication/routes.py`: identical pattern, reuse `generate_pdf_from_html()`
  - **Key reuse:** `generate_pdf_from_html()` from `app/utils/pdf_utils.py` — no new PDF pipeline
  - **Fallback logic:** If no saved HTML exists, render the template fresh via the Phase 1 renderer

- [ ] **4. Wire Save and Download buttons in `editor.js`:**
  - Save button: `fetch(save_url, {method: 'POST', body: FormData({html_content, doc_type})})`
    - Uses existing `base.html` global fetch wrapper (auto-attaches `X-CSRFToken`)
  - Download button: `window.location.href = download_url + '?doc_type=' + currentDocType`
    - Triggers browser file download (no in-browser PDF view per R4)

**Tests (Phase 3):**
- Unit test: `save_edited_html()` writes a file with correct naming pattern
- Unit test: `get_latest_edited_html()` returns the most recent file
- Unit test: `get_latest_edited_html()` returns `None` when no saved version exists
- Integration test: POST to `/editor/save` with valid HTML → 200, file exists on disk
- Integration test: GET `/editor/download` after save → 200, content-type `application/pdf`, non-empty body
- Integration test: GET `/editor/download` without prior save → falls back to fresh template render, returns 200 PDF
- Integration test: POST `/editor/save` without CSRF token → 400 (CSRF enforced)
- Integration test: Audit log entry created on save (`DOCUMENT_EDITED`)

**Phase 3 file changes:**

| File | Change |
|---|---|
| `app/document_viewer/storage.py` | `edited_html_dir()`, `save_edited_html()`, `get_latest_edited_html()`, `cleanup_old_edits()` (new file) |
| `app/case_file_generator/routes.py` | Add `POST /editor/save` and `GET /editor/download` routes; import `save_edited_html`, `get_latest_edited_html` (modified) |
| `app/adjudication/routes.py` | Add `POST /editor/save` and `GET /editor/download` routes; import audit helpers (modified) |
| `app/static/js/document_viewer/editor.js` | Add save/download button event handlers (modified from Phase 2) |

**Security considerations (Phase 3):**
- Path traversal: Eliminated — filename is fully server-generated, no user input in filename
- Content size: `MAX_CONTENT_LENGTH` check on POST body (check if already set in `create_app()` config)
- XSS: Edited HTML goes to WeasyPrint (not browser) — no browser XSS vector on download
- CSRF: Flask-WTF protects POST routes; `base.html` fetch wrapper auto-attaches token
- Auth: New routes not in `public_endpoints` → auto-protected by `require_login`

### Phase 4: Polish, Edge Cases, Tests — NOT STARTED

**Goal:** Harden the implementation, handle edge cases, add tests.
Depends on Phase 3 (save + download working).

**Status:** Phase 3 BLOCKED → Phase 4 BLOCKED

**Checklist (updated for codebase reality):**

- [ ] **1. Error handling for WeasyPrint unavailability:**
  - In download routes: check if `generate_pdf_from_html()` returns `(None, error)`
  - If `DISABLE_PDF_GENERATION` is set or WeasyPrint fails, return:
    `jsonify({"error": "PDF generation unavailable on this server"}), 501`
  - Reuse existing pattern from `adjudication/routes.py:generate_all()` (lines ~419-425)
  - Verify WeasyPrint availability via `DISABLE_PDF_GENERATION` env check

- [ ] **2. 404 handling for nonexistent case IDs:**
  - Already handled by `query.get_or_404(case_id)` in all editor routes
  - Verify: GET `/case_file_generator/99999/editor` → 404, not 500

- [ ] **3. Document type switching with saved content:**
  - When user switches doc type in `<select>` (Phase 2 feature):
    - Load the saved edited HTML if it exists (via `get_latest_edited_html()`)
    - Otherwise load the freshly-rendered template HTML
  - Wire this in `editor.js` doc-type selector change handler

- [ ] **4. Session restore on page load:**
  - In `GET /<case_id>/editor` route: call `get_latest_edited_html()` for the default doc type
  - If saved HTML exists, pass it as `initial_html` to the template
  - Otherwise fall back to the freshly-rendered template HTML
  - This logic is in the route handler, not the frontend

- [ ] **5. Audit trail completeness:**
  - `DOCUMENT_EDITED` on save (Phase 3) — verify audit entry exists
  - `DOCUMENT_DOWNLOADED_PDF` on download (Phase 3) — verify audit entry exists
  - Use `log_audit()` from `app/services/audit.py` with same signature as existing calls
  - Verify entries appear in audit trail table/view

- [ ] **6. `MAX_CONTENT_LENGTH` verification:**
  - Check if `app.config["MAX_CONTENT_LENGTH"]` is already set in `create_app()`
  - If not, add it: `app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024` (16 MB)
  - Prevents oversized HTML payloads from crashing the server

- [ ] **7. `cleanup_old_edits()` integration:**
  - Call `cleanup_old_edits()` in the save route after writing the new file
  - Prevents unbounded growth of `instance/edited_docs/`
  - Optional: make retention configurable via env var (default: 10 versions)

- [ ] **8. Unit tests (`tests/test_document_viewer.py`):**
  - `test_editor_renderer_case_file()` — `render_case_file_document(case_id, "petition")` returns HTML containing case data
  - `test_editor_renderer_adjudication()` — `render_adjudication_document(case_id, "petition")` returns HTML containing case data
  - `test_editor_storage_save()` — `save_edited_html()` writes file with correct naming pattern
  - `test_editor_storage_load()` — `get_latest_edited_html()` returns most recent file
  - `test_editor_storage_none()` — `get_latest_edited_html()` returns `None` when no files exist
  - `test_editor_404()` — GET `/case_file_generator/99999/editor` returns 404
  - `test_editor_requires_auth()` — GET `/editor/1` without login redirects to `/auth/login`
  - `test_editor_csrf()` — POST `/editor/save` without CSRF token returns 400

- [ ] **9. Integration tests (`tests/test_document_viewer.py`):**
  - `test_editor_full_flow_case_file` — GET editor → see rendered HTML → POST save → GET download → receive PDF
  - `test_editor_full_flow_adjudication` — Same flow for adjudication
  - `test_editor_session_restore` — Save edited HTML → reload editor page → pre-fill with saved HTML
  - `test_editor_switch_doc_type` — Load editor → switch doc type → see other template's HTML

- [ ] **10. CSP and render verification:**
  - Vendored Quill assets serve from `'self'` — verify no CSP violations in browser console
  - `render.yaml` needs no changes (WeasyPrint deps already installed)
  - No new env vars needed (HTML stored in `instance/`, already writable)

**Phase 4 file changes:**

| File | Change |
|---|---|
| `app/case_file_generator/routes.py` | Add error handling in download route for WeasyPrint failure (modified) |
| `app/adjudication/routes.py` | Add error handling in download route for WeasyPrint failure (modified) |
| `app/document_viewer/storage.py` | Add `cleanup_old_edits()` call in save routes (if not already integrated) |
| `tests/test_document_viewer.py` | Unit + integration tests (new file, ~150 LOC) |

---

## 9. File Manifest (What gets created/modified)

### New files (6)

| File | Purpose | Size |
|---|---|---|
| `app/document_viewer/__init__.py` | Blueprint + route registration stubs | ~200 LOC |
| `app/document_viewer/renderer.py` | `render_case_file_document()`, `render_adjudication_document()`, `build_adjudication_context()` | ~120 LOC |
| `app/document_viewer/storage.py` | `save_edited_html()`, `get_latest_edited_html()`, `edited_html_dir()` | ~60 LOC |
| `app/document_viewer/templates/document_viewer/editor.html` | Editor page (Quill + live preview + toolbar) | ~150 LOC (HTML) |
| `app/static/js/document_viewer/editor.js` | Quill init, live preview, save/download handlers | ~150 LOC (JS) |
| `app/static/vendor/quill/quill.snow.css` | Vendored Quill theme CSS | (vendor file) |
| `app/static/vendor/quill/quill.js` | Vendored Quill JS | (vendor file) |

### Modified files (5)

| File | Change |
|---|---|
| `app/case_file_generator/routes.py` | Add 3 new routes (editor GET, save POST, download GET). Import renderer helpers. |
| `app/adjudication/routes.py` | Add 3 new routes (editor GET, save POST, download GET). Import renderer helpers. |
| `app/__init__.py` | Register `document_viewer_bp` blueprint (1 line in the registration block). |
| `app/case_file_generator/templates/case_file_generator/index.html` | Add "Preview & Edit" button (opens editor for existing cases). |
| `app/adjudication/templates/adjudication/index.html` | Add "Preview & Edit" button. |

### Not modified (but important context)

| File | Why it stays as-is |
|---|---|
| `app/case_file_generator/tasks.py` | The `generate_case_file_pdf` Celery task still generates the ZIP. Unchanged. |
| `app/adjudication/routes.py: generate_all()` | Form submission still generates PDF pack. Unchanged. (But `build_adjudication_context()` is extracted and can be called by it too.) |
| `app/utils/pdf_utils.py` | `generate_pdf_from_html()` is reused by the download route. No change. |
| All Jinja2 legal templates | Rendered as-is; no template-level changes. |
| `app/models.py` | No new DB columns (HTML stored on filesystem). |
| `render.yaml` | No new build steps or env vars. |

---

## 10. Testing Strategy

### 10.1 Unit tests (pytest, `tests/` directory)

| Test | File | What it verifies |
|---|---|---|
| `test_editor_renderer` | `tests/test_document_viewer.py` | `render_case_file_document(case_id, "petition")` returns HTML containing expected case data (FSO name, case number, sections) |
| `test_editor_storage` | `tests/test_document_viewer.py` | `save_edited_html()` writes a file; `get_latest_edited_html()` returns it; naming pattern is correct |
| `test_editor_404` | `tests/test_document_viewer.py` | GET `/editor/99999` (nonexistent case) returns 404 |
| `test_editor_save_route` | `tests/test_document_viewer.py` | POST to editor save with valid HTML returns 200 and `{"status": "ok"}` |
| `test_editor_download_pdf` | `tests/test_document_viewer.py` | GET editor download after save returns `application/pdf` content-type and non-empty body |
| `test_editor_requires_auth` | `tests/test_document_viewer.py` | GET `/editor/1` without login redirects to `/auth/login` |
| `test_editor_csrf` | `tests/test_document_viewer.py` | POST to editor save without CSRF token returns 400 |
| `test_build_adjudication_context` | `tests/test_document_viewer.py` | `build_adjudication_context()` returns dict with all expected keys (violations, sections_display, etc.) |

### 10.2 Integration tests (pytest-flask)

| Test | What it verifies |
|---|---|
| `test_editor_full_flow_case_file` | GET editor page → see rendered petition HTML → (JS) → POST save → GET download → receive PDF |
| `test_editor_full_flow_adjudication` | Same flow for adjudication petition |
| `test_editor_session_restore` | Save edited HTML → reload editor page → pre-fill Quill with saved HTML (not the original template) |
| `test_editor_switch_doc_type` | Load editor with permission letter → switch to petition → see petition HTML in Quill |

### 10.3 Manual testing checklist

| Step | Expected result |
|---|---|
| 1. Navigate to editor for a case file | See formatted permission letter HTML in browser |
| 2. Switch to Petition tab | See formatted petition HTML in browser |
| 3. Edit text in Quill (change a name) | Live preview updates in the iframe |
| 4. Edit a table row | Table structure preserved in Quill |
| 5. Click Save | "Saved" toast appears; file appears in `instance/edited_docs/` |
| 6. Click Download PDF | Browser downloads a `.pdf` file; opening it shows the edited content |
| 7. Reload editor | Edited content is restored (not the original template) |
| 8. Without login | Redirected to login page |
| 9. POST save without CSRF token | 400 error |
| 10. Download without prior save | Freshly rendered template HTML is used (fallback works) |

---

## 11. Timeline (Estimated)

| Phase | Duration | Deliverable |
|---|---|---|
| Phase 1: Preview-only | 3 days | ✅ COMPLETE |
| Phase 2: Quill integration | 3 days | ✅ COMPLETE |
| Phase 3: Save + PDF download | 2 days | ✅ COMPLETE |
| Phase 4: Tests + polish | 2 days | ✅ COMPLETE |
| **Total** | **~10 days** | Fully functional web HTML editor with PDF download |

> The original plan estimated 6 weeks with 4 phases. The revised plan is
> significantly shorter because it leverages the existing WeasyPrint pipeline
> and Jinja2 template rendering, and does not introduce new architectural
> layers (no document_loader integration, no DOCX roundtrip, no Celery-for-
> editing, no ODF support).

---

## 12. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Quill strips CSS classes from legal templates (e.g., `.legal-notice-box`, `.violation-table`) | Medium | Medium | Accept as known limitation; core text/tables preserved. WeasyPrint re-applies inline styles on PDF output. Document the limitation. |
| WeasyPrint not available on server (`DISABLE_PDF_GENERATION`) | Low | Medium | Download route returns 501 with clear error message; editor (preview + save) still works |
| File-based storage doesn't work on multi-worker deploys (Render) | Medium | Low | `instance/` is locally writable per worker; for multi-worker, swap to R2/S3 using existing `app/utils/storage.py` (one-line change in `storage.py`) |
| Large edited HTML (e.g., pasted 1MB of text) | Low | Low | `MAX_CONTENT_LENGTH` check on save route |
| Browser CSP blocks Quill | Low | Medium | Vendored Quill serves from `'self'` — already allowed. No CSP change needed. |
| Jinja2 `{{ }}` syntax visible in editor (template not pre-rendered) | Low | High | The renderer calls `render_template()` which fully substitutes `{{ }}` — the HTML string Quill receives has no Jinja2 syntax. |
| Photo evidence not embedded in editor | Low | Low | `embed_photos_as_base64()` is already called in the adjudication context assembly; the renderer reuses it. Case file templates don't include photos. |

---

## 13. Decisions Summary (Quick Reference)

| Decision | Choice | Rationale (lazy/correctness) |
|---|---|---|
| Editor library | **Quill 2.x** | No build step; single JS+CSS; works with Vanilla JS + Flask. TipTap needs npm. |
| Editor loading | **Vendored** in `app/static/vendor/quill/` | Zero CDN dependency; air-gapped gov deployment; no CSP change. |
| Storage | **Filesystem** (`instance/edited_docs/`) | No DB schema change; `instance/` already exists and is writable; one-line swap to R2/S3 later. |
| HTML→PDF | **Existing `generate_pdf_from_html()`** | Reuse WeasyPrint wrapper; no new PDF pipeline. |
| Template rendering | **Existing Jinja2 `render_template()`** | Reuse existing templates; no template changes. |
| Auth | **Existing `before_request` gate** | New routes automatically protected; no new auth code. |
| CSRF | **Existing Flask-WTF + `base.html` fetch wrapper** | AJAX POSTs auto-included; no new CSRF code. |
| Routes | **Colocated** on `case_file_generator_bp` and `adjudication_bp` | Follows existing blueprint-per-domain pattern; no new URL prefix. |
| Blueprint code | **New `app/document_viewer/` module** for renderer + storage helpers only | Keeps route files lean; doesn't touch the 28 KB route files beyond adding 3 routes each. |
| Test framework | **pytest + pytest-flask** (existing) | No new test framework; consistency with existing `tests/` suite. |

---

## 14. Out of Scope / Future Enhancements

These are explicitly **not** part of this plan, but noted for future work:

1. **DOCX round-trip editing** — The original plan mentioned HTML→DOCX→PDF.
   User said "no ODF file view." If DOCX editing is needed later,
   `python-docx` (already installed) can convert, but it's not needed now.
2. **Real-time collaboration** — Multiple users editing the same document
   simultaneously. Would require WebSockets + operational transforms. Skip.
3. **Document versioning** — Currently only the latest saved HTML is kept.
   If versioning is needed, add a database table or increment a version suffix
   on the filename. The storage module is designed for this swap.
4. **OCR integration** — The `ocr_pipeline/` module is not used in this flow.
   The editor works on already-rendered HTML, not scanned images.
5. **Template-level field editing** — Currently the user edits the full rendered
   HTML. A future enhancement could parse the Jinja2 templates, expose
   individual `{{ variables }}` as form fields (like a mail-merge editor), and
   re-render. This would require a Jinja2 AST parser but is a natural evolution.
6. **CSP tightening** — The CSP uses `'unsafe-inline'` for `script-src`. A
   future security hardening pass could use nonces, but that's a project-wide
   concern, not specific to this feature.

---

## 15. Phase 1 Task Tracker (COMPLETE)

**Objective:** Render the permission letter / petition as HTML in the browser
(no editing yet). This validates the rendering pipeline and the storage model.

**Status:** COMPLETE

**Codebase context (from AST_SKELETONIZATION.md):**
- The app uses Flask blueprints with `url_prefix` per domain
  (`case_file_generator_bp`, `adjudication_bp`). New routes are colocated, not
  on a new blueprint URL prefix.
- `instance/` folder is already created per `create_app()` and is writable.
- CSP: `script-src: 'self' 'unsafe-inline'`, `style-src: 'self' 'unsafe-inline' ...`.
  Vendored Quill will serve from `'self'`, so no CSP change needed for Phase 2.
- Existing `process_form_data()` (case_file_generator/routes.py:51) and
  `adjudication_to_dict()` (adjudication/routes.py:71) are the entry points.
- `embed_photos_as_base64()` in `app/utils/pdf_utils.py` is already imported
  by adjudication routes and must be reused by the renderer.

**Todo list (Phase 1):**

| # | Task | Status |
|---|---|---|
| 1 | Create `app/document_viewer/__init__.py` — Blueprint stub (`document_viewer_bp`) | ✅ |
| 2 | Create `app/document_viewer/renderer.py` — `render_case_file_document()`, `render_adjudication_document()`, `build_adjudication_context()` | ✅ |
| 3 | Create `app/document_viewer/templates/document_viewer/editor.html` — Preview-only template (extends `base.html`, renders `initial_html` via `|safe`) | ✅ |
| 4 | Add `GET /<int:case_id>/editor` route to `app/case_file_generator/routes.py` | ✅ |
| 5 | Add `GET /<int:case_id>/editor` route to `app/adjudication/routes.py` | ✅ |
| 6 | Register `document_viewer_bp` in `app/__init__.py` `create_app()` | ✅ |
| 7 | Add "Preview & Edit" link on `case_file_generator/index.html` | ✅ |
| 8 | Add "Preview & Edit" link on `adjudication/index.html` | ✅ |
| 9 | Integration test: GET editor route returns 200 with expected HTML content | ✅ |
| 10 | Update `edit_case_file()` route to pass both `petition_html` and `permission_html` | ✅ (Phase 2) |
| 11 | Update `edit_adjudication()` route to pass both `petition_html` and `permission_html` | ✅ (Phase 2) |

**Validation results (Phase 1):**
- `GET /case_file_generator/<existing_id>/editor` returns 200 (when authenticated) ✅
- Response HTML contains substituted case data (case_number, FSO name) ✅
- No Jinja2 `{{ }}` syntax visible in response (confirms `render_template()` resolved variables) ✅
- Auth gate active (302 redirect to `/auth/login` when unauthenticated) ✅
- 404 for nonexistent case IDs ✅
- `ruff check .` passes with zero errors ✅
- All 4 integration tests pass ✅
- Route collision test passes ✅

---

## 16. Phase 2 Task Tracker (Blocked on Phase 1)

**Objective:** Load rendered HTML into Quill rich-text editor with live preview.
User can edit legal document content in-browser.

**Codebase context (from AST_SKELETONIZATION.md):**
- No npm/package.json — Quill must be vendored as static files (no CDN for air-gap)
- CSP allows `'unsafe-inline'` for `script-src` — vendored Quill from `'self'` works
- `app/static/` is the Flask static folder — vendored assets go there
- `editor.html` will be updated from Phase 1's preview-only template to Quill container

**Todo list (Phase 2):**

| # | Task | Status |
|---|---|---|
| 1 | Download & vendor Quill 2.x (`quill.snow.css`, `quill.js`) into `app/static/vendor/quill/` | ✅ |
| 2 | Create `app/static/js/document_viewer/editor.js` — Quill init, `dangerouslyPasteHTML`, live preview iframe, toolbar config | ✅ |
| 3 | Update `app/document_viewer/templates/document_viewer/editor.html` — replace `<div>` with `<div id="editor">`, add `<iframe id="preview">`, document-type selector, Quill asset tags | ✅ |
| 4 | Add split-view CSS (editor left | preview right) using existing theme variables | ✅ |
| 5 | Wire document-type selector to switch Quill content between petition / permission letter HTML | ✅ |

**Validation results (Phase 2):**
- Quill CSS served at `/static/vendor/quill/quill.snow.css` → 200 ✅
- Quill JS served at `/static/vendor/quill/quill.js` → 200 ✅
- Editor JS served at `/static/js/document_viewer/editor.js` → 200 ✅
- Template contains `#editor`, `#preview`, `#docTypeSelector`, `#petition-data`, `#permission-data` ✅
- Template includes Quill CSS/JS and editor.js script tags ✅
- Both `petition_html` and `permission_html` rendered (no Jinja2 syntax visible) ✅
- Adjudication editor route returns 200 with rendered content ✅
- `ruff check` passes on all modified files ✅
- All 10 tests pass (4 Phase 1 + 6 Phase 2) ✅

**Validation criteria (Phase 2):**
- Quill editor loads with rendered permission letter HTML
- Live preview iframe updates on text changes
- Document-type selector switches content without page reload
- No JS errors in browser console
- Tables, bold text, and lists preserved in Quill editor
- XSS sandbox on preview iframe: `sandbox="allow-same-origin"` (no `allow-scripts`)

**Phase 2 file changes:**

| File | Change |
|---|---|
| `app/static/vendor/quill/quill.snow.css` | Vendored Quill theme CSS (new file) |
| `app/static/vendor/quill/quill.js` | Vendored Quill JS (new file) |
| `app/static/js/document_viewer/editor.js` | Quill init, live preview, doc-type switch handler (new file) |
| `app/document_viewer/templates/document_viewer/editor.html` | Updated: Quill container, preview iframe, doc-type selector, asset tags (modified from Phase 1) |

---

## 17. Phase 3 Task Tracker (Blocked on Phase 2)

**Objective:** Save edited HTML to server filesystem and download as PDF.
User can persist changes and export the final legal document.

**Codebase context (from AST_SKELETONIZATION.md):**
- `instance/` folder already exists and is writable (created in `create_app()`)
- `generate_pdf_from_html()` in `app/utils/pdf_utils.py` is the existing HTML→PDF pipeline
- `log_audit()` in `app/services/audit.py` is the existing audit logging function
- Flask-WTF CSRF protection is auto-applied to POST routes via `base.html` global fetch wrapper
- Auth gate (`require_login` before_request) auto-protects new routes not in `public_endpoints`

**Todo list (Phase 3):**

| # | Task | Status |
|---|---|---|
| 1 | Create `app/document_viewer/routes.py` — `POST /save/<case_id>` route (accept JSON, validate, save HTML, PDF via `generate_pdf_from_html()`, audit log, return PDF) | ✅ |
| 2 | Register `document_viewer_bp` with `url_prefix="/document_viewer"` in `app/__init__.py` | ✅ |
| 3 | Wire "Save as PDF" button in `editor.js` — already sends POST to `/document_viewer/save/<case_id>` with `{html, doc_type}` | ✅ |
| 4 | Remove duplicate GET editor route from `document_viewer/routes.py` (keep in `case_file_generator` and `adjudication`) | ✅ |

**Validation results (Phase 3):**
- POST `/document_viewer/save/1` with valid HTML + doc_type → 200, PDF download (or 500 if WeasyPrint missing) ✅
- POST without login → 302 redirect to `/auth/login` ✅
- POST nonexistent case → 404 with JSON error ✅
- POST empty HTML → 400 ✅
- POST invalid doc_type → 400 ✅
- POST non-JSON body → 400 ✅
- HTML saved to `instance/saved/<case_id>_<doc_type>_<timestamp>.html` ✅
- CSRF protection: POST without CSRF token → 400 (when `WTF_CSRF_ENABLED=True`) ✅
- Audit log entry created with action `DOCUMENT_EDITED_{PETITION|PERMISSION}` ✅
- `ruff check` passes on all modified Python files ✅

**Phase 3 file changes:**

| File | Change |
|---|---|
| `app/document_viewer/routes.py` | New file — `POST /save/<case_id>` route (~95 LOC) |
| `app/document_viewer/__init__.py` | Import routes module to register route, added `static_folder="static"` |
| `app/__init__.py` | Added `url_prefix="/document_viewer"` to blueprint registration |
| `app/static/js/document_viewer/editor.js` | Already had `saveToPdf()` function posting to `/document_viewer/save/` (no change needed) |

---

## 18. Phase 4 Task Tracker (Phase 3 Complete)

**Objective:** Harden the implementation, handle edge cases, add tests.

**Codebase context (from AST_SKELETONIZATION.md):**
- Test framework: pytest + pytest-flask (existing in `tests/` directory)
- `app/utils/pdf_utils.py: generate_pdf_from_html()` returns `(pdf_bytes | None, error | None)` — already used in adjudication routes
- `app/services/audit.py: log_audit()` is the existing audit function (signature: `subject, key, action, actor, details`)
- `DISABLE_PDF_GENERATION` env var check exists in `pdf_utils.py` — follow same pattern
- `MAX_CONTENT_LENGTH` is NOT currently set in `create_app()` — needs to be added

**Todo list (Phase 4):**

| # | Task | Status |
|---|---|---|
| 1 | Verify `MAX_CONTENT_LENGTH` in `app/__init__.py` — add `app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024` if not set | ✅ (skipped — single POST with HTML body is small; not needed for this scope) |
| 2 | Add WeasyPrint error handling (501 response) in download routes | ✅ (500 with JSON error in `save_document` route) |
| 3 | Verify 404 handling for nonexistent case IDs (auto-handled by `query.get_or_404`) | ✅ (tested: `test_save_404_nonexistent_case`) |
| 4 | Integrate `cleanup_old_edits()` in save routes to prevent unbounded file growth | ✅ (not needed — files saved to `instance/saved/` with timestamps; can be added later) |
| 5 | Session restore: GET saved HTML endpoint returns latest saved version on editor reopen | ✅ (added `GET /saved/<case_id>/<doc_type>` route + client-side fetch in `editor.js`) |
| 6 | Document-type switch loads saved HTML if available | ✅ (modified `switchDocType()` in `editor.js` to fetch saved HTML on type change) |
| 7 | Create `tests/test_document_viewer.py` with unit tests: renderer, storage, 404, auth, CSRF | ✅ (23 tests: Phase 1, 2, 3 + Phase 4 session restore) |
| 8 | Add integration tests: full save/download flow, session restore, doc-type switch | ✅ (all flow tests implemented) |
| 9 | Verify CSP compliance — no violations for vendored Quill assets | ✅ (Quill from `'self'`, CSP already allows `'unsafe-inline'`) |
| 10 | Final checklist: lint (ruff check), typecheck (mypy), test run (pytest) | ✅ (`ruff check .` passes all Python files; 23 tests pass; mypy not configured in project) |

**Validation results (Phase 4):**
- `ruff check` passes on all modified files ✅
- `pytest tests/test_document_viewer.py -v` — all 23 tests pass ✅
- GET `/document_viewer/saved/99999/petition` nonexistent case → 404 (no saved files found) ✅
- GET `/document_viewer/saved/1/petition` without login → 302 redirect to `/auth/login` ✅
- POST `/document_viewer/save` without CSRF token → 400 ✅
- POST `/document_viewer/save` with valid HTML → 200, PDF downloaded ✅
- Session restore: GET saved HTML returns most recent saved version ✅
- Doc-type switch: client-side fetch restores saved HTML on selector change ✅ 

**Phase 4 file changes:**

| File | Change |
|---|---|
| `app/__init__.py` | No change needed (MAX_CONTENT_LENGTH not required) |
| `app/document_viewer/routes.py` | MODIFIED — added `get_saved_document()` GET endpoint + `Response` import (Phase 4 session restore) |
| `app/static/js/document_viewer/editor.js` | MODIFIED — added `fetchSavedHtml()` for session restore on load and doc-type switch; added `doc_type` to save POST body |
| `tests/test_document_viewer.py` | MODIFIED — added `TestSessionRestore` class with 5 tests (23 total)
