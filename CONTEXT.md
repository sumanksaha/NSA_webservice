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
