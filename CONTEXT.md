# CONTEXT.md — Domain & Architecture Glossary

> Living glossary for AI agents and developers. Terms here are the _names of
> good seams_ — use them exactly when discussing design. Created during the
> 2026-08-22 architecture review (Candidate 1: the config seam). ADRs live in
> `docs/adr/` (none yet).

## Architecture vocabulary

We use the codebase-design vocabulary: **module** (interface + implementation),
**interface** (everything a caller must know), **depth** (behaviour per unit of
interface), **seam** (where behaviour can vary without editing callers),
**adapter** (concrete thing satisfying an interface at a seam), **leverage**
(caller benefit of depth), **locality** (maintainer benefit: changes
concentrate in one place).

## Terms

### `cfg` — the configuration seam

**Module:** `app/shared/config.py`. The single place where feature flags and
settings are resolved. Owns:

- the **declaration table**: every setting declared once (key, type, default,
  boolean convention, description) — `cfg.table()` / `cfg.describe()`;
- the resolution rule (**Pattern A**, decided 2026-08-22): Flask
  `current_app.config` wins inside an app context; `os.environ` is read
  outside one; otherwise the declared default. `seed_config_from_env(app)`
  is called from `create_app()` so env vars work identically in-context;
- per-flag boolean conventions: `opt_in` (string must be `"true"`) vs
  `opt_out` (anything but `"false"`), preserved historically and declared
  explicitly per row.

Adding a flag = one table row + one `.env.example` entry (enforced by
`tests/test_shared_config.py::test_env_example_keys_are_declared`). Never
hand-roll a `try: current_app.config / except: os.environ` resolver again.

### `BackupRestorer` — the CSV restore adapter (D6)

**Module:** `app/services/backup_restorer.py`. The single deep module
that replaces the triplicated restore pipelines in `app/utils/sync.py`.
Owns:

- **canonical map**: `BACKUP_MODULE_TO_TABLE` — one table mapping module
  keys to worksheet/table names (previously triplicated as
  `_AIRTABLE_TABLE_MAP` = `_WORKSHEET_MAP` = `_SHEETS_RESTORE_MAP` =
  `_RESTORE_MODULE_MAP`);
- **public interface**: `restore_from(target) -> int`,
  `restore_if_empty() -> dict`, `auto_restore_if_empty() -> dict`;
- **dead-code deletion**: removed `sync_to_sheets()` (zero production
  importers, grep-confirmed) and `_build_column_map` no-op hook.

Adding a synced module = one line in `BACKUP_MODULE_TO_TABLE`, not
three copies of the restore pipeline. Tests cross the same seam as
callers: patch `BackupRestorer.restore_from()` rather than 7 private
internals.

### `InspectionCodeGenerator` — the inspection code seam (D7)

**Module:** `app/inspection/code_generation.py`. The single deep module
that replaces the triplicated code-generation logic in the old
`browser_use` utility. Owns:

- the **canonical generation rule**: `INSP-YYYY-####` with a zero-padded
  sequence persisted in the `inspections` table;
- the **public interface**: `generate_code() -> str`,
  `calculate_compliance_deadline() -> datetime | None`;
- **locality**: all sequence allocation, concurrency handling, and deadline
  arithmetic live in one place; callers only know the two method names.

Adding a new identifier format = one method on the module, not a new branch
in every inspector that needs the code.

### `NominatimGeocoder` — the location seam (D7)

**Module:** `app/inspection/verification/geocoding_adapter.py`. The deep
module that replaces the ad-hoc `requests.get()` calls scattered across
`browser_use` and `browser_use.py`. Owns:

- the **canonical location rule**: geocoding is done through one adapter with
  a single `reverse(lat, lng) -> dict` interface;
- **leverage**: every caller that needs a location gets the same semantics
  (timeout handling, error normalization, and future caching) without
  re-implementing HTTP logic;
- **locality**: all Nominatim-specific behaviour (endpoint, query shape,
  error mapping) is isolated from callers.

Adding a new location provider = a new adapter at the seam; callers never
change.

### `IpGeolocationAdapter` — the identity seam (D7)

**Module:** `app/inspection/verification/ip_adapter.py`. The deep module
that replaces the ad-hoc `requests.get()` calls in `browser_use.py`. Owns:

- the **canonical identity rule**: IP-to-location mapping through one adapter
  with a single `geolocate(ip) -> dict` interface;
- **leverage**: callers get normalized results (`region`, `city`, `error`)
  without knowing about the upstream service;
- **locality**: all ip-api.com-specific behaviour (endpoint, query shape,
  timeout handling) is isolated from callers.

### `LicenseLookupAdapter` — the licence seam (D7)

**Module:** `app/inspection/verification/lookup_adapter.py`. The deep module
that replaces the ad-hoc `requests.get()` calls in `browser_use.py`. Owns:

- the **canonical licence rule**: FSSAI and CE licence checks through one
  adapter with a single `lookup(source, id) -> dict` interface;
- **leverage**: callers get normalized results (`found`, `error`, `data`)
  without knowing about FSSAI vs CE differences;
- **locality**: all licence-provider-specific behaviour (endpoint, query
  shape, error mapping) is isolated from callers.

### `PhotoProcessor` — the image processing seam (D7)

**Module:** `app/inspection/services/photo_processor.py`. The deep module
that replaces the ad-hoc image handling in `browser_use.py`. Owns:

- the **canonical image rule**: validation, EXIF extraction, coordinate
  fallback, and temp-file creation through one interface
  `process(file, form_data) -> ProcessedPhoto`;
- **leverage**: callers get a structured `ProcessedPhoto` result without
  knowing about Pillow, UUIDs, or temp directories;
- **locality**: all image-specific behaviour (validation rules, EXIF parsing,
  coordinate fallback) is isolated from callers.

### `EvidenceStore` — the persistence seam (D7)

**Module:** `app/inspection/services/evidence_store.py`. The deep module
that replaces the ad-hoc Evidence row creation in `browser_use.py`. Owns:

- the **canonical persistence rule**: Evidence record creation, stamping
  updates, deletion, and listing through one interface
  `save(processed_photo) -> Evidence`;
- **leverage**: callers get a single persistence seam without knowing about
  SQLAlchemy session details;
- **locality**: all Evidence-specific behaviour (fields, lifecycle, audit
  logging) is isolated from callers.

### `OCRDispatcher` — the OCR seam (D7)

**Module:** `app/inspection/services/ocr_dispatcher.py`. The deep module
that replaces the ad-hoc OCR dispatch in `browser_use.py`. Owns:

- the **canonical OCR rule**: task dispatch with deduplication through one
  interface `dispatch(filepath) -> dict`;
- **leverage**: callers get a structured result (`task_id`, `result`, `mode`)
  without knowing about the background task queue;
- **locality**: all OCR-specific behaviour (queue naming, dedup key,
  timeout handling) is isolated from callers.

### Declaration table

The tuple of `Setting` rows inside `app/shared/config.py`. Single source of
truth for the config surface; doubles as living documentation.

### Pattern A

The resolution rule above. Chosen over a three-tier config→env→default rule
so that "what the app is configured with" stays inspectable in one place
(`app.config`) and env is authoritative only where there is no app context
(Celery workers, scripts).

## Domain concepts

### Bill issuance

The ordered transaction that turns unbilled **Samples** in a date range into a
**Bill**: validate the range → recompute totals from the Samples → persist the
Bill **atomically together with** marking those Samples billed and linking them
→ best-effort parallel sync → dispatch PDF generation.

Load-bearing invariant: **no Bill exists unless its Samples are marked billed**
(and vice versa within one issuance). A Bill whose PDF failed is recoverable; a
duplicated Bill is not — so persistence never depends on sync or PDF success.

### Inspection Checklist

The 12 yes/no hygiene-and-compliance items an FSO records during an inspection
(premises, refrigeration, attire, utensils, date tagging, veg/non-veg
separation, food segregation, licence display, artificial colour, expired
items, pest report, water report). Answering a flagged way on an item
constitutes an observed **violation**. The same item set and flag semantics
apply to non-sample Adjudications; the checklist is captured at inspection
time regardless of whether a sample was drawn.

### Improvement Notice (u/s 32, FSS Act)

The statutory document directing an FBO to take corrective action by a
compliance deadline. Always keyed to an **Inspection** — never to a Sample.
Its violations table is derived from the **Inspection Checklist**; its
corrective actions are derived from the violated items. Generated lazily: the
first render/download of the notice freezes the inspection record.

### Corrective Measures Implemented

The terminal state of an open inspection issue: the FBO has corrected the
reported problem. Replaces the retired concept of "dismissal". An inspection
with unresolved violations remains listed as an open issue until this state is
asserted (with actor + timestamp + audit note). There is no deadline
precondition for asserting it.

### Role gate (`ROLE_BLUEPRINTS`)

**Module:** `app/shared/rbac.py`. Phase 18's authorization seam. Maps each
non-admin role to the Flask blueprints it may reach; admins bypass entirely.
Consumed by the `enforce_rbac` before_request gate (deny = flash + redirect to
the role's landing page) and by `base.html` nav visibility. Adding a feature
to a role = one set entry, shipped with deploy.

### FSO account binding

A user holding the **`fso`** role is bound 1:1 to an entry of the `fso` table
via `users.fso_name` (set at provisioning, unique among active accounts).
The binding drives record-level scoping: CaseFile/Adjudication/Inspection/
Work Diary rows whose officer name equals the binding are visible; everything
else fails closed (admins see all). Creates force-stamp the bound name
server-side.

### Note

A single item in the **Notepad** intake queue (`app/notepad/`, `/notepad`):
free-form content — pasted text or PDF-extracted text — representing an idea,
a to-do, or a proposal. A Note is **shared with all FSOs by default**; the
author may make it private (author-only). It is _not_ a legal
record (no hash-chained audit). Lifecycle: `new → evaluated → implemented |`
`dismissed`. Evaluation means one append-only **NoteEvaluation**: an AI-
generated structured record (implementation plan, risks, game-theory,
Talebian antifragility, first-principles lenses) stored as JSON; re-runs
append rather than overwrite. Never abbreviated "DO" — that collides with DO
Intimation.

### Daily Plan

A short AI-generated battle plan for an FSO's own open Notes — the top 3–5
Notes worth doing today plus a full ranking of the rest. Plans are
**append-only** (`DailyPlan` rows, never overwritten), cover **own notes
only** (never another FSO's shared notes, since status transitions are
author-only), and order items **least-time-first** (effort buckets `quick` /
`medium` / `long`, tie-broken by portfolio-level lens scores). A past plan is
evidence: its hit-rate (implemented vs. planned) feeds back into future plan
generation and the feature's kill criterion.

(Existing domain language lives in AGENTS.md §1 — CaseFile vs Adjudication,
Canonical Key Contract, hash-chained audit, optimistic concurrency. Add new
named concepts here as they crystallize.)
