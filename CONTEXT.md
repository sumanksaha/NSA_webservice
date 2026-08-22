# CONTEXT.md — Domain & Architecture Glossary

> Living glossary for AI agents and developers. Terms here are the *names of
> good seams* — use them exactly when discussing design. Created during the
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

(Existing domain language lives in AGENTS.md §1 — CaseFile vs Adjudication,
Canonical Key Contract, hash-chained audit, optimistic concurrency. Add new
named concepts here as they crystallize.)
