# Work Diary Feature — Primary-Source Research

> **Status:** Research only. Every claim below is traced to an actual file in this repo
> (path + line numbers). Items marked **(inferred)** are design conclusions, not existing code.

---

## 1. Repo conventions for research/design notes

The repo keeps feature research/design notes in **two places**:

### `docs/` — per-feature research + plan documents (the matching convention)

Actual filenames found (`docs/` directory listing):

| File | Kind |
| --- | --- |
| `docs/FSSAI_LOOKUP_POSTGRES_RESEARCH.md` | research |
| `docs/RAG_QUERY_PIPELINE_RESEARCH.md` | research |
| `docs/RAG_UI_RESEARCH.md` | research |
| `docs/CI_CD_RESEARCH.md` | research |
| `docs/FSSAI_LOOKUP_POSTGRES_PLAN.md` | plan |
| `docs/FSSAI_REINGEST_PLAN.md` | plan |
| `docs/HF_HOSTING_LANGGRAPH_INTEGRATION_PLAN.md` | plan |
| `docs/MULTIDOMAIN_INTEGRATION.md` | integration plan |
| `docs/DEEPENING.md`, `docs/COVERAGE_COMPLETENESS.md`, `docs/DOCUMENT_LOADER_PERFORMANCE.md`, `docs/INGESTION_READINESS.md`, `docs/LANGGRAPH_IMPLEMENTATION_EVALUATION.md`, `docs/RAG_GAP_BRIDGE_PLAN.md`, `docs/RUST_REFACTORING_EVALUATION.md`, `docs/PARALLEL_LEGAL_STRUCTURE_EVIDENCE_LAYER.md`, `docs/legal_document_processing_pipeline.md`, `docs/LINE_ENDINGS_SETUP.md`, `docs/FSSAI_LOOKUP_REFRESH.md` | misc design/ops notes |
| `docs/adr/`, `docs/enrichment/` | subdirectories |

### Root-level `*.md` — cross-cutting status/plans

Root contains (partial list): `plan.md`, `task.md`, `CONTEXT.md`, `CHANGELOG.md`,
`FASTAPI_IMPLEMENTATION_PLAN.md`, `DOCUMENT_VIEWER_IMPLEMENTATION_PLAN.md`,
`CLOUDINARY_PHOTO_MODULE_IMPLEMENTATION_PLAN.md`, `RAG_AUDIT_REPORT.md`,
`KG_READINESS_AUDIT*.md`, `CORPUS_IDENTITY_REPORT.md`, `REFACTORING_PLAN.md`,
`ENGINEERING_ASSESSMENT.md`, `SECURITY_TODO.md`.

**Decision:** the established naming pattern for a *feature-scoped* research doc is
`docs/<FEATURE>_RESEARCH.md` (precedents: `FSSAI_LOOKUP_POSTGRES_RESEARCH.md`,
`RAG_QUERY_PIPELINE_RESEARCH.md`, `RAG_UI_RESEARCH.md`). Therefore:

➡️ **This file lives at `docs/WORKDIARY_RESEARCH.md`.**
A follow-up implementation plan would be `docs/WORKDIARY_PLAN.md`.

---

## 2. Inspection model & routes

### 2.1 Models — `app/models/inspection.py`

**`FSO`** (`app/models/inspection.py:11-17`):
- `fso_name = db.Column(db.String(100), primary_key=True)` (line 14)
- `created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))` (line 15)
- Index `idx_fso_name` on `fso_name` (line 17)

**`Inspection`** (`app/models/inspection.py:20-51`) — ALL columns:

| Column | Type | Nullable | Line |
| --- | --- | --- | --- |
| `id` | Integer PK autoincrement | no | 23 |
| `version_id` | Integer, default 1 (optimistic concurrency via `__mapper_args__["version_id_col"]`, lines 26–28) | no | 24 |
| `inspection_code` | String(50), unique | no | 29 |
| `fso_name` | String(100), FK → `fso.fso_name` | no | 30 |
| `fssai_license` | String(50) | yes | 31 |
| `ce_license_no` | String(100) | yes | 32 |
| `fbo_name` | String(200) | yes | 33 |
| `fbo_address` | **Text** ← *place of visit* | yes | 34 |
| `concerned_food` | String(200) | yes | 35 |
| `problem` | **Text** ← *closest thing to "purpose" detail* | yes | 36 |
| `inspection_date` | **DateTime**, NOT NULL ← *date of visit* | no | 37 |
| `compliance_deadline` | DateTime, NOT NULL | no | 38 |
| `is_dismissed` | Boolean, default False | — | 39 |
| `dismissed_by` | String(100) | yes | 40 |
| `dismissed_at` | DateTime | yes | 41 |
| `adjudication_id` | Integer FK → `adjudications.id` (ondelete SET NULL) | yes | 42 |
| `created_at` | DateTime default now(UTC) | — | 43 |
| `synced_at` | DateTime | yes | 44 |

Indexes (lines 46–51): `idx_inspection_code`, `idx_inspection_date`,
`idx_inspection_compliance_deadline`, `idx_inspection_fso_name`.

**Key finding for Work Diary:** there is **NO dedicated "place of visit" field other
than `fbo_address`** (Text, nullable) and **NO routine-vs-complaint distinction
anywhere** — no enum, boolean, or category column exists on `Inspection`. The
free-text `problem` column (Text, line 36) is the only purpose-like data. A Work Diary
"Purpose" of *"Routine Inspection" vs "Complaint"* cannot be derived from current
columns with certainty; it must either be (a) inferred heuristically from `problem`
being empty (routine) vs populated (complaint) — **lossy, inferred only** — or (b)
added as a new column/migration. Date comes from `inspection_date`; place comes from
`fbo_address` (+ optionally `fbo_name` for context).

### 2.2 Routes — `app/inspection/routes/` package

Package layout (`app/inspection/routes/__init__.py:1-16`): submodules
`inspection_routes` (CRUD/list/index), `lookup_routes` (FSSAI/CE license lookups),
`derived_views` (open issues/pending/history/dismissal/adjudication linking),
`photo_routes` (photo evidence). Blueprint re-exported from `app.inspection`.

**Creation flow** — `POST /inspection/create`
(`app/inspection/routes/inspection_routes.py:113-200`):
- Form fields map 1:1 to columns (lines 118–158): form key
  `food_safety_officer_name` → `fso_name` (validated against FSO table, lines
  121–128); `inspection_date` (required, parsed via `parse_date`, lines 119–124);
  `fssai_license`, `ce_license_no`, `fbo_name`, `fbo_address`, `concerned_food`,
  `problem` (all optional, `.strip() or None`, lines 138–143);
  `compliance_deadline` defaults to
  `calculate_compliance_deadline(parse_date(inspection_date))` when blank
  (lines 132–136); `inspection_code` generated by `generate_inspection_code()`
  (line 130). Returns JSON 201 with id/code (lines 190–197).
- Update route `PUT /<id>` applies the same field set conditionally (lines
  240–264); optimistic-concurrency guard returns 409 on `StaleDataError`
  (lines 293–295).

**Routine vs complaint distinction:** none. Neither create nor update accepts any
category/type field; nothing in `form_data` distinguishes them.

**FSO selection:** index page loads FSO names via `get_all_fso_names()` from
`app/utils/fso_data.py` (`inspection_routes.py:46-47`). That helper parses the
root-level markdown file `fso_list.md` (`# FSO List` / `- Name` bullets,
`app/utils/fso_data.py:23-40`) and upserts names additively into the `fso`
table (module docstring, lines 1–8). The list view also exposes an FSO filter
dropdown (`filter_fso`, `inspection_routes.py:57,63-64,98-104`).

---

## 3. Blueprint + template conventions

Reference implementations examined: `app/timeline/` (Phase 13) and
`app/food_cell/` (Phase 21).

### 3.1 Blueprint definition

`app/timeline/__init__.py:10-18`:

```python
timeline_bp = Blueprint(
    "timeline",
    __name__,
    url_prefix="/timeline",
    template_folder="templates",
)

# Import routes after blueprint is defined so the route decorators register.
from app.timeline import routes  # noqa: F401
```

`app/food_cell/__init__.py:12-17` (url_prefix instead supplied at registration time):

```python
food_cell_bp = Blueprint(
    "food_cell",
    __name__,
    template_folder="templates",
    static_folder="static",
)
```

### 3.2 Registration in `app/__init__.py::create_app()`

Imports at `app/__init__.py:426,436`:

```python
from app.food_cell import food_cell_bp
...
from app.timeline import timeline_bp
```

Registration block (`app/__init__.py:462-470`):

```python
    app.register_blueprint(health_bp)
    app.register_blueprint(food_cell_bp, url_prefix="/food-cell")

### 3.3 Template folder layout & base.html inheritance

- Layout convention: `<blueprint>/templates/<blueprint>/<page>.html` — e.g.
  `app/timeline/templates/timeline/index.html`;
  `app/food_cell/templates/food_cell/do_intimation.html` and
  `do_intimation_inline.html`.
- Templates extend the master layout:
  `app/timeline/templates/timeline/index.html:1` → `{% extends "base.html" %}`.

### 3.4 Nav links in `base.html`

Nav links live in `<nav class="nav-tabs" id="navList">` starting at
`app/templates/base.html:201`. Pattern (Inspection example, `base.html:214-219`):

```html
<a
    href="{{ url_for('inspection.index') }}"
    class="nav-link {% if request.blueprint == 'inspection' %}active{% endif %}"
>
    <i class="fa-solid fa-magnifying-glass-chart"></i> Inspection
</a>
```

Other entries follow identically: case_file_generator (203), sample (209),
adjudication (221), bill_generator (227), settings (233), search (239), RAG behind
`{% if config.get('RAG_ENABLED', True) %}` (244-251), analytics (253), sync behind
`ENABLE_SUPABASE_SYNC` (258+). A Work Diary link would use

---

## 4. PDF generation pattern

### 4.0 The seam

All HTML→PDF goes through `generate_pdf_from_html(html_content) -> tuple`
in `app/utils/pdf_utils.py:39-44`, which delegates to
`PDFAssemblyEngine.generate_from_html` (`app/pdf_assembly/engine.py`) and returns
**(pdf_bytes | None, error | None)**. WeasyPrint is imported defensively via
`import_weasyprint()` (`app/utils/pdf_utils.py:29-36`) so the app boots on hosts
without GTK; tests run with `DISABLE_PDF_GENERATION=1` (`tests/conftest.py:53`),
so callers must handle `pdf_bytes is None`.

### 4.1 Canonical download route — `app/document_viewer/routes.py:105-173`

This is the exact pattern to copy (string/HTML in, BytesIO PDF out):

```python
    pdf_bytes, pdf_error = generate_pdf_from_html(pdf_html)          # line 161
    if pdf_bytes is None:                                            # lines 162-164
        current_app.logger.error("PDF generation failed for case %s: %s", case_id, pdf_error)
        return jsonify({"error": f"PDF generation failed: {pdf_error}"}), 500

    # --- Return PDF as file download ---
    pdf_filename = f"{resolved.case_number}_{doc_type}_{result.timestamp}.pdf"   # 167
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=pdf_filename,
    )                                                                # lines 168-173
```

Imports: `send_file` from `flask` (routes.py:41), `io.BytesIO`,
`generate_pdf_from_html` / `post_process_pdf_html` from `app.utils.pdf_utils`
(routes.py:49).

### 4.2 Sibling variants

- Template-rendered pages (`adjudication/routes.py` imports at line 54; call
  sites at lines 425, 599; also `app/shared/document_case_manager.py:385,618`)
  use: `render_template(...)` → optional `post_process_pdf_html(...)` →
  `generate_pdf_from_html(rendered_html)` → the same
  `send_file(io.BytesIO(pdf_bytes), as_attachment=True, download_name=...,
  mimetype="application/pdf")`.
- ZIP variant: `adjudication/routes.py:438-448` wraps multiple PDFs into a
  BytesIO zip with `mimetype="application/zip"`.
- Disk-file variant exists in `app/food_cell/routes.py:32-37`
  (`send_file(pdf_path, as_attachment=True,

---

## 5. Test conventions

Examined `tests/test_timeline.py` and `tests/test_food_cell_do_intimation.py`.
Both share the identical hand-rolled fixture trio (there are **no shared
app/client/db fixtures** for these modules in `tests/conftest.py` — conftest only
pins `DATABASE_URL` to a temp SQLite DB before app import, sets `SECRET_KEY`,
`SKIP_FSO_STARTUP_SYNC=1`, `DISABLE_PDF_GENERATION=1`, and adds a
leaked-app-context autouse popper; `tests/conftest.py:1-53`):

```python
def _setup_test_env():
    """Create a test app with in-memory SQLite, a user, and an FSO."""
    from app import create_app
    from app.extensions import db
    from app.models import FSO, User

    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False          # CSRF off for POSTs

    app_context = app.app_context()
    app_context.push()

    db.drop_all(); db.create_all()

    user = User(username="timelineuser", password_hash="pbkdf2:sha256$test$dummy")
    db.session.add(user)
    db.session.add(FSO(fso_name="Test Officer"))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)             # Flask-Login session login

    return app, client, app_context


def _teardown_test_env(app_context):
    db.session.remove(); db.drop_all(); app_context.pop()
```

(`tests/test_timeline.py:26-59`; identical shape at
`tests/test_food_cell_do_intimation.py:33-62`.)

---

## 6. Config seam (AGENTS.md §3.6)

New flags must be declared once as a `Setting(...)` row in the `_TABLE` tuple in
`app/shared/config.py` (declaration table starts at `app/shared/config.py:65-66`).

`Setting` is a frozen dataclass (`app/shared/config.py:42-62`) with fields
`(key, attr, type, default, opt_in=True, help="")`: `key` = env/Flask-config
name, `attr` = the `cfg.` accessor name, `type` ∈ bool/int/float/str, and
`opt_in=True` means the raw string must be `"true"` to enable (opt-out =
anything except `"false"`).

Real example — `app/shared/config.py:76-82`:

```python
Setting(
    "RAG_USE_AGENT_PIPELINE",
    "use_agent_pipeline",
    bool,
    False,
    help="LangGraph agent pipeline on POST /api/rag/query/agent (M3).",
),
```

One-line form also accepted:
`Setting("RAG_AGENT_HITL", "agent_hitl", bool, False, help="...")`
(`config.py:83`).

**Yes — .env.example parity is enforced by test:**
`tests/test_shared_config.py:185-193` (`test_env_example_keys_are_declared`)
regex-extracts all `^(RAG_[A-Z0-9_]+|ENABLE_[A-Z0-9_]+)=` keys from
`.env.example` and asserts each is declared in `cfg.table()` minus an explicit
`_NOT_SEAM_SETTINGS` allowlist (`test_shared_config.py:177-182`). Related
meta-tests: attr/key uniqueness (`test_shared_config.py:196-200`) and
default-seeding assertions (lines 165-169). So a new flag such as
`WORKDIARY_ENABLED` needs a `Setting(...)` row; if it is named with an
`ENABLE_` prefix it must also appear in `.env.example` to keep the parity test
green. Adding both a table row and an `.env.example` entry is the documented
contract (AGENTS.md §3.6).

---

## 7. Summary of implications for Work Diary (inferred design input)

1. **Data source:** `Inspection` already carries Date (`inspection_date`, NOT NULL)
   and Place (`fbo_address`, Text, nullable). Purpose does **not** exist — closest
   proxy is `problem` (free text) and its absence. Options: derive
   Routine-vs-Complaint heuristically (empty `problem` ⇒ routine) or add a proper
   column (e.g. `visit_purpose` enum) via Alembic migration; the latter matches the
   requirement's strict two-value constraint.
2. **Module shape to copy:** `app/workdiary/__init__.py` (Blueprint, own
   `url_prefix="/work-diary"`, `template_folder="templates"`) + `routes.py` +
   `templates/workdiary/index.html` extending `base.html`; register in
   `app/__init__.py` near the other phase blueprints (§3.2); add nav anchor in
   the `base.html` navList (§3.4) with an FSO selector reusing
   `get_all_fso_names()` (`app/utils/fso_data.py`).
3. **PDF endpoint to copy verbatim:** the `document_viewer/routes.py:161-173`
   pattern — `generate_pdf_from_html(rendered_html)` → None-check →
   `send_file(io.BytesIO(pdf_bytes), mimetype="application/pdf",
   as_attachment=True, download_name=f"Work_Diary_{fso}.pdf")`.
4. **Tests:** clone the `_setup_test_env`/`_teardown_test_env` trio from
   `tests/test_timeline.py:26-59`; login via `sess["_user_id"]`;
   `WTF_CSRF_ENABLED=False`.
5. **Config:** any toggle = one `Setting(...)` row in `app/shared/config.py` +
   one `.env.example` entry; parity enforced by
   `tests/test_shared_config.py::test_env_example_keys_are_declared`.

Naming conventions: module docstring enumerating covered behaviours
(`test_timeline.py:1-9`); private helpers prefixed `_`
(`_setup_test_env`, `_teardown_test_env`, `_make_case_file` / `_make_sample`
factory functions with `defaults = dict(...)` + overrides); test classes grouped
by concern (e.g. `TestSyncForwarding`, `TestDownloadEndpoint`,
`TestStatusEndpoint`). Login is simulated purely via `sess["_user_id"]` — no
password check needed. PDF-dependent tests mock generation or rely on
`DISABLE_PDF_GENERATION=1` (`tests/conftest.py:53`).
  download_name=f"DO_Intimation_{sample_id}.pdf", mimetype="application/pdf")`),
  but the in-memory BytesIO path is the right one for a stateless diary render.

### 4.3 Preview vs download (inferred)

For an HTML-table preview, just `render_template("workdiary/diary.html",
rows=rows)`. For PDF, render the same (or a print-styled) template to a string
and feed it to `generate_pdf_from_html` exactly as in §4.1.
`url_for('workdiary.index')` + `request.blueprint == 'workdiary'`. Timeline itself
has no nav-tab link — it is reached through the global case-picker modal
(`base.html:274-277,317-328`), so a nav tab is *not* mandatory but is the norm for
standalone pages.

### 3.5 CSRF / auth for POST routes

- Auth: global `before_request` login gate — every blueprint route requires login
  unless listed in `public_endpoints` (AGENTS.md §3.5; enforced in `create_app()`).
- CSRF: Flask-WTF CSRF protects all POSTs automatically; JSON POSTs read the token
  from `<meta name="csrf-token">` in `base.html` via the global fetch wrapper —
  documented verbatim in `app/document_viewer/routes.py:119-121`.
- Tests disable it with `app.config["WTF_CSRF_ENABLED"] = False` (see §5).
    app.register_blueprint(kg_bp, url_prefix="/knowledge-graph")
    app.register_blueprint(sync_bp, url_prefix="/sync")
    from app.ai_assistant import ai_bp

    app.register_blueprint(ai_bp, url_prefix="/ai-assistant")
    # timeline_bp carries its own url_prefix ("/timeline") in the Blueprint.
    app.register_blueprint(timeline_bp)
```

(Note: registration is roughly alphabetical but newer phases were appended at
the end; either style is acceptable — quote-comment the deviation.)

---

## Implementation status (2026-08-26)

Implemented on top of this research:

1. **Official report format** — `app/workdiary/templates/workdiary/report.html`
   reproduces `FSO_Work_Diary_Template.html` (title, Name/Month/Year meta,
   Place of Posting / Area of Jurisdiction lines, roman-numeral column
   headers (i)–(iv), min 15 rows with blank-row padding, FSO signature +
   DO countersign). Both `/workdiary/preview` and `/workdiary/pdf` render it.
2. **Explicit purpose at entry** — `Inspection.visit_purpose`
   (`"routine"` | `"complaint"` | NULL; migration
   `add_inspection_visit_purpose.py`, down_revision
   `add_fssai_lookup_tables`). Picked via a required select in the
   inspection entry form; validated in create/update routes;
   `WorkDiaryEngine.derive_purpose` prefers it over the legacy
   problem-presence heuristic (NULL rows keep working).
3. **Lookup regression test fix** — `test_route_collisions.py` now seeds a
   known `FssaiLicense` row so reachability (non-302 + JSON 200) can be
   asserted without depending on bulk-loaded reference data.
