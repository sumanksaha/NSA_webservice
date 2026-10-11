# Implementation Plan: Schedule-Planning Agent for FSO Daily Duty

**Companion to:** [SCHEDULE_PLANNING_AGENT_RESEARCH.md](./SCHEDULE_PLANNING_AGENT_RESEARCH.md) (the
research that established the current state). This document is the **project report**: scope,
assumptions, work breakdown, and the exact set of files to create and modify.

**Date:** 2026-10-10. **Status:** plan only — no code, tests, or config changed.
**Scope note:** every current-state claim is a `path:line` citation into the working tree, verified
by reading the source. Where a claim could not be verified from source, it is marked
**UNVERIFIED** and listed in §12.

---

## 1. Executive summary

The requested capability — *"I will inspect a restaurant in NSC Bose Road"* → search → suggest →
update Work Diary — decomposes into four sub-capabilities. **One is blocked by data, two are
blocked by missing seams, one does not exist.** No sub-capability is blocked by tooling.

The correct sequence is therefore **seams before agent**. Building the agent first would produce a
model that invents premises it cannot look up and writes through a path that does not exist.

| Phase | Delivers | Blocks the next phase? |
| --- | --- | --- |
| 0 | `SUPABASE_DB_URL` declared in the config table | **Yes** — the search route cannot authenticate |
| 1 | `PremisesResolver` — locality-aware premise lookup | **Yes** — nothing to schedule without candidates |
| 2 | `DiaryPlanner` — programmatic, invariant-safe diary write | **Yes** — the agent has nowhere to write |
| 3 | *(decision gate)* premise↔diary schema linkage | **Yes** — changes Phases 5–6 shape |
| 4 | `WorkDiaryEntry` brought under the audit trail | No |
| 5 | The agent itself (propose → confirm → write) | — |
| 6 | Domain docs + ADR | — |

**Phases 0–2 are worth doing on their own merits** — each is independently useful to a human
inspector and each lands inside existing repo conventions. Phase 5 is the smallest phase by line
count and the only one that is an "agent" in the usual sense.

---

## 2. Scope

### 2.1 In scope

- Resolving a free-text locality/road/name to FSSAI licence/registration candidates in Supabase.
- Generating a proposed ordered duty schedule for one FSO and one date, with rationale.
- Writing accepted schedule items into the **Monthly Diary** (`work_diary` table) with all existing
  split-duty invariants preserved.
- A confirmation step between proposal and write.
- Audit coverage for the resulting writes.

### 2.2 Out of scope (deliberately)

| Excluded | Why |
| --- | --- |
| Auto-creating an `Inspection` record | `create_inspection` force-stamps the officer via `scoped_officer_name` (`app/inspection/routes/inspection_routes.py:174-176`) and is a separate legal act. Mixing it into scheduling needs its own decision. |
| Timed itineraries with travel time | No duration, distance, or beat data exists in the system. A "schedule" is an **ordered list**, and the UI must say so. |
| Ward/zone/beats modelling | A data-modelling project of its own. This plan searches free-text `full_address` instead. |
| PostGIS / `pg_trgm` / geo-indexing | Deferred behind a measured trigger (§11, R3). |
| Migrating `lookup_ce` (KMC scraping) behind the resolver | `lookup_ce` rate-limits itself to one request per 40 s via a `fcntl` lock (`app/utils/lookup.py:51-53`, `:214-273`). An agent loop calling it per candidate is unusable. Explicitly excluded. |
| The OpenCode-side agent (Option B in the research doc) | Personal tooling only; cannot serve an FSO from the deployed app. Optional §11, R7. |

---

## 3. Verified current state

The facts the plan depends on. Each was read from source, not inferred.

### 3.1 The premises search path

| Fact | Evidence |
| --- | --- |
| Route exists: `GET|POST /inspection/premises/search` | `app/inspection/routes/inspection_routes.py:456` |
| Queries `fssai_licenses` by `company_name ILIKE :name`, `LIMIT 50` | `app/inspection/routes/inspection_routes.py:490-491` |
| Queries `fssai_registrations` identically | `app/inspection/routes/inspection_routes.py:501-502` |
| **`full_address` is selected but never used as a predicate** | same two statements |
| Opens its own engine per request from `SUPABASE_DB_URL` | `app/inspection/routes/inspection_routes.py:466`, `:480` |
| `SUPABASE_DB_URL` is **absent** from the config declaration table | `app/shared/config.py:688-721` declares `ENABLE_SUPABASE_SYNC`, `SUPABASE_URL`, `SUPABASE_API_KEY`, `SUPABASE_SYNC_INTERVAL` only |
| `SUPABASE_DB_URL` is **absent** from `.env.example` | `.env.example:518-533` |
| Hand-rolled resolver (forbidden by `CONTEXT.md:36`) | `app/inspection/routes/inspection_routes.py:466` |
| `source_tables` is always empty — the search block is GET-guarded but `source_tables` is read on POST | `app/inspection/routes/inspection_routes.py:482`, `:527-531` |
| Template exists and is functional | `app/inspection/templates/inspection/premises_search.html` |
| Template placeholder is name-only: `"Enter premises name (e.g., devyani)"` | `app/inspection/templates/inspection/premises_search.html:19` |
| Template **displays** `full_address` | `app/inspection/templates/inspection/premises_search.html:36` |
| Selection dead-ends — "Generate Checklist" re-renders the same page; no handoff to `inspection.create` | `app/inspection/templates/inspection/premises_search.html:27`, `:45`, `:53-65` |
| Registry schema: PK is the FSSAI number; all other columns `Text` | `app/models/lookup.py:33-71` |
| No index on any name or address column anywhere in the model layer | enumerated; none declared |

### 3.2 The diary write path

| Fact | Evidence |
| --- | --- |
| Only writer is an HTML form POST | `app/diary.py:397` |
| A POST **replaces the whole month** — absent fields clear the day | `app/diary.py:410` |
| `premises` is an **`Integer` count, not a reference** | `app/models/diary.py:33` |
| `place_of_visit` is free text, max 200 | `app/models/diary.py:36` |
| Composite PK `(fso_name, work_date, duty_seq)` | `app/models/diary.py:29-31` |
| Split-duty promotion (lone seq 2 → seq 1) is inline in the route | `app/diary.py:420-428` |
| Holiday/leave exclusivity is inline in the route | `app/diary.py:430-439` |
| Post-save compaction is inline in the route | `app/diary.py:455-485` |
| Owner is resolved server-side; non-admins cannot target another officer | `app/diary.py:105-124`, `app/shared/rbac.py:103-115` |
| `WorkDiaryEntry` is **not** audited | `app/audit_hooks.py:159-161` |

### 3.3 The precedent to copy (Daily Plan)

| Fact | Evidence |
| --- | --- |
| Route decides the action; the model fills a bounded payload | `app/notepad/routes.py:55-96` |
| Strict allowlist validator, including an anti-hallucination check that every returned `note_id` was offered | `app/notepad/routes.py:40-52` |
| Single guarded commit; on any exception nothing persists | `app/notepad/routes.py:82-94` |
| Injectable factory seam `_ai_service()`, patched in tests rather than the transport | `app/notepad/routes.py:28-32`, `tests/test_notepad_daily_plan.py:82` |
| Config gate pattern | `app/shared/config.py:722-730`, consumed at `app/notepad/routes.py:63` |
| Prompt is an inline string built at call time | `app/ai_assistant/service.py:213-233` |
| **No tool-calling exists anywhere in the repo** | grep for `tools=`/`tool_choice`/`bind_tools`/`tool_calls` → zero matches |

---

## 4. Assumptions

Every assumption below is a **belief that, if wrong, changes the plan**. They are numbered so
review can attack them individually. "Validate by" is the cheapest test that would confirm or kill
the assumption.

### A1 — Supabase Postgres is the same database the app already reads for FSSAI lookups

**Assumed:** `fssai_licenses` / `fssai_registrations` are reachable both through the app's own
SQLAlchemy engine (`app/models/lookup.py` declares them as live models) and through a separately
configured `SUPABASE_DB_URL` (`app/inspection/routes/inspection_routes.py:466`).

**Why believed:** `DEPLOYMENT.md:43` states the two SQLite lookups *"were moved into the existing
Supabase Postgres"*; `DEPLOYMENT.md:63` lists Supabase as the primary store.

**Risk if wrong:** if `SUPABASE_DB_URL` points at a *different* project than `DATABASE_URL`, the
resolver must keep two engines and Phase 1 roughly doubles. **Validate by:** comparing the two
DSNs' host/database component in a single throwaway query *before* writing Phase 1. **This is the
single highest-value pre-flight check in the plan.**

### A2 — `full_address` contains usable, searchable locality text

**Assumed:** FSSAI-registered addresses carry road/locality strings that an `ILIKE '%…%'` can
match, and that "NSC Bose Road" as an FSO would say it appears verbatim or near-verbatim.

**Why believed:** `full_address` is populated from the FSSAI bulk load (`DEPLOYMENT.md:48-50`).

**Risk if wrong — HIGH.** Addresses may be abbreviated (`N.S.C.B. Road`), use a different spelling,
or omit the road entirely. `ILIKE '%NSC Bose Road%'` would then return zero rows and the whole
feature looks broken. **Validate by:** running the Phase 0 acceptance query (§5, Phase 0 step 5)
against a real address sample *before* committing to the approach. If hit rate is poor, the
fallback is trigram search (`pg_trgm`), which is a materially larger change (§11, R3).

### A3 — Supabase credentials are available in the target environment

**Assumed:** `SUPABASE_DB_URL` will be set wherever this feature runs.

**Risk if wrong:** the feature is dead on arrival in any environment lacking it — which, per §3.1,
may be *every* environment today, since the variable is undeclared and undocumented.
**Validate by:** checking Render/dash environment config. Note this is an **operator action**, not
a code action, and cannot be resolved by this plan.

### A4 — The FSSAI number is the canonical premise identifier

**Assumed:** `license_no` / `registration_no` is stable and is the right key to carry forward into
a schedule.

**Why believed:** it is the PK of both tables (`app/models/lookup.py:44`, `:65`), and
`FboIssue.fbo_id` already uses the FSSAI number as its identifier (`app/models/issue.py:10-42`).

**Risk if wrong:** low. **Caveat that must be honoured:** dispatch is mechanical by first character
only — `"1"…` → licenses, `"2"…` → registrations — and `app/models/lookup.py:16-20` warns the
inversion is *historical and intentional*. Any code that "fixes" this will break lookups.

### A5 — An FSO's statement is about **one** premise at a time

**Assumed:** *"I will inspect a restaurant in NSC Bose Road"* resolves to a single premise the FSO
then identifies, rather than to an ambiguous pool requiring disambiguation.

**Risk if wrong:** medium. The UI already supports multi-select
(`app/inspection/templates/inspection/premises_search.html:33`), so the data model tolerates
multiplicity, but the agent's confirm step does not. **Handling:** the agent must stop and ask
when candidates are ambiguous — never guess. This is a hard rule, stated in the agent prompt.

### A6 — A diary row need only record *that* and *how many*, not *which*

**Assumed:** Phase 3 is **out of scope** and the feature ships with `premises` as a count.

**Why believed:** it is the smallest change that satisfies the request as literally stated.

**Risk if wrong — affects the product, not the code.** If an FSO needs "I inspected Sharma
Restaurants and Anand Sweets" rather than "I inspected 2 premises", the current schema cannot
represent it. This is **Decision D1** (§13) and it gates Phase 5's UI.

### A7 — Monthly Diary, not Inspection rows, is the correct write target

**Assumed:** the schedule writes `work_diary` slots, not `inspection` rows.

**Why believed:** the request says "update work diary"; the Work Diary *report* unions both but
the official preview/PDF buildup is Monthly-only (`include_inspections=False`), and
`derive_diary_purpose` (`app/diary.py:189-203`) maps `field`/`office` → `Inspection` purpose.

**Risk if wrong:** low; adding an Inspection later is additive.

### A8 — The agent runs server-side inside the Flask app (Option A), not as an OpenCode agent

**Risk if wrong:** low. Option B is a thin front door over the same services and can be added
later (§11, R7) without rework — provided Phases 1–2 land as importable services first. **This
assumption is exactly why Phases 1 and 2 are specified as importable modules, not route handlers.**

### A9 — An LLM call is warranted for the *ordering*, but not for the *lookup*

**Assumed:** premise resolution is deterministic SQL (no model); the model is used only to order
and justify a candidate set. This follows `CONTEXT.md:414` — *"The LLM never picks the Act."*

**Risk if wrong:** low. If ordering is later shown to be deterministic (e.g. simple distance sort),
the model can be dropped from Phase 5 without touching Phases 1–2.

### A10 — `LookupResult`-style error normalisation is the right shape

**Assumed:** the resolver returns a normalised result object carrying `found`/`error`/`data`,
mirroring `app/utils/lookup.py:70-99`.

**Risk if wrong:** very low. **Do not copy `LicenseLookupAdapter`'s docstring claims** — it claims
"retry and caching" (`app/inspection/verification/lookup_adapter.py:1`, `:7`) but is an 18-line
dispatcher with neither. Do not inherit that drift.

---

## 5. Work breakdown

### Phase 0 — Declare `SUPABASE_DB_URL` (smallest; unblocks everything)

| # | Task | Type |
| --- | --- | --- |
| 0.1 | Add `Setting("SUPABASE_DB_URL", "supabase_db_url", str, "", help=...)` to `_TABLE` beside the other Supabase rows | modify |
| 0.2 | Add the matching `.env.example` entry with a comment | modify |
| 0.3 | Replace the hand-rolled resolver at `app/inspection/routes/inspection_routes.py:466` with the `cfg` accessor | modify |
| 0.4 | Add the config-parity test case | test |
| 0.5 | **Pre-flight:** run the live acceptance query below | verify |

The parity test is mechanical — `tests/test_shared_config.py::test_env_example_keys_are_declared`
(`:195-204`) already fails the build if a declared key lacks an `.env.example` entry. **Do 0.1 and
0.2 in the same commit or the build breaks.**

**Acceptance query (validates A1 and A2):**

```sql
SELECT license_no, company_name, full_address
FROM fssai_licenses
WHERE full_address ILIKE '%NSC Bose Road%'
LIMIT 20;
```

Also run `EXPLAIN ANALYZE` on it. A sequential scan on `fssai_licenses` is **expected** (no index —
§3.1); record the timing, because `analysis/premises_search_plan.md` set ">500ms" as the trigger to
add a real index.

**Done when:** the route reads the declared setting; parity test green; the acceptance query's
result and timing are recorded in the PR description.

### Phase 1 — `PremisesResolver` (the locality seam)

New file: **`app/services/premises_resolver.py`**, following the `BackupRestorer` precedent
(`app/services/backup_restorer.py`, documented in `CONTEXT.md:38-56`).

| # | Task | Type |
| --- | --- | --- |
| 1.1 | Define `PremiseCandidate` (fssai_number, company_name, full_address, expiry_date, source_table) | create |
| 1.2 | Module-level cached engine + `dispose()`; never build one per request | create |
| 1.3 | `resolve(text, *, limit=50) -> list[PremiseCandidate]` — `ILIKE` on **`company_name` OR `full_address`** across both tables, merged and deduped on `(fssai_number, full_address)` | create |
| 1.4 | `search_by_fssai(number) -> PremiseCandidate \| None` — reuses the existing dispatch rule from A4 | create |
| 1.5 | Error normalisation: never raise into the route; return `error=` in the result | create |
| 1.6 | Refactor `premises_search()` to delegate; delete the inlined SQL | modify |
| 1.7 | Refactor `scripts/supabase_premises_search.py` to delegate (kill the duplicate) | modify |
| 1.8 | Fix the `source_tables` bug — carry candidates across the POST | modify |
| 1.9 | Update the template placeholder to advertise locality search | modify |
| 1.10 | **Decide** whether to add the selection→`inspection.create` handoff (out of scope per §2.1 unless trivial) | decide |

**Interface contract (fixed, so Phase 5 can rely on it):**

```python
def resolve(text: str, *, limit: int = 50) -> list[PremiseCandidate]: ...
def search_by_fssai(number: str) -> PremiseCandidate | None: ...
```

`resolve` **must never raise** and **must never return an empty list silently** — an empty list
and a connection failure must be distinguishable by the caller, or the agent will report "no such
premise" when the truth is "the database was unreachable".

**Done when:** `resolve("NSC Bose Road")` returns ≥1 candidate on live data; the route and the CLI
share one code path; the engine is constructed once per process.

### Phase 2 — `DiaryPlanner` (the write seam)

New file: **`app/services/diary_planner.py`**.

| # | Task | Type |
| --- | --- | --- |
| 2.1 | `record(owner, work_date, duty_seq, activity, premises, samples, notes, place) -> str` returning `saved`/`deleted`/`noop` | create |
| 2.2 | Reuse — do **not** re-declare — `ACTIVITIES`, `COUNT_ACTIVITIES`, `EXCLUSIVE_ACTIVITIES`, `MAX_COUNT`, `PLACE_MAX_LEN`, `FIELD_ACTIVITY` from `app/diary.py:38-76` | create |
| 2.3 | Port split-duty promotion from `app/diary.py:420-428` | create |
| 2.4 | Port holiday/leave exclusivity from `app/diary.py:430-439` | create |
| 2.5 | Port post-save compaction from `app/diary.py:455-485` | create |
| 2.6 | Reuse `_summary_line` (`app/diary.py:211-226`) and `_parse_count` (`:127-136`) | create |
| 2.7 | Refactor `bulk()` to delegate to `record()`; route keeps parsing and month-loop only | modify |
| 2.8 | Force-stamp `owner` from `scoped_officer_name(current_user)` — never accept it as an argument from a caller | create |

**The invariant this must not break:** *a day must never hold a bare `duty_seq=2` row.* Both the
pre-save promotion and the post-save compaction enforce it today; `record()` must enforce it
unconditionally, because a single-slot agent call has no "month loop" to normalise it.

**Done when:** all 35 tests in `tests/test_diary.py` still pass **unmodified**, plus new
`record()`-level tests proving the three invariants hold when called one slot at a time.

> **This is the regression risk of the whole plan.** The Phase 2 acceptance criterion is
> deliberately "existing tests unchanged" — refactoring a green suite into a service is where
> silent invariant loss happens.

### Phase 3 — *(decision gate)* premise↔diary schema linkage

Blocked on **Decision D1** (§13). Three options, in ascending cost:

| Option | Change | Cost | Records *which* premise? |
| --- | --- | --- | --- |
| **3a** | None | nil | No — count only |
| **3b** | New `work_diary_premise` sibling table: `(fso_name, work_date, duty_seq, fssai_number, source_table)` | migration + model + engine change | Yes |
| **3c** | Nullable `premises_json` column on `work_diary` | migration + engine change | Yes, loosely |

**Recommendation: 3a for v1.** It satisfies the request as stated, and 3b can be added later
without disturbing the write path — the diary slot row stays the source of truth for activity
counts either way. Choose 3b only if D1 comes back "yes, specific premises matter".

Migration hazards if 3b/3c are chosen — both already documented for `work_diary`:

- The table was rebuilt once already to change its PK (`migrations/versions/add_diary_duty_seq.py:44`).
- On PostgreSQL the rebuilt constraint keeps the auto-generated name `work_diary_new_pkey`; a
  later migration must reference that name, not `work_diary_pkey`
  (`migrations/versions/add_diary_duty_seq.py:30-32`).
- `work_diary` is in the fail-loud boot schema check (`app/db_bootstrap.py:40-48`, `:82-119`);
  bypass is `SKIP_SCHEMA_CHECK=1`.

### Phase 4 — Audit the diary

| # | Task | Type |
| --- | --- | --- |
| 4.1 | Add `WorkDiaryEntry` to `audited_model_types` (`app/audit_hooks.py:159-161`) | modify |
| 4.2 | **Verify the audit hook tolerates a composite PK** — see Risk R4 | verify |
| 4.3 | If it does not, take the alternative: log writes via the planner rather than via the hook | decide |

**Done when:** a `DiaryPlanner.record()` call produces an audit row; existing audit tests
(Adjudication/Bill/CaseFile) unaffected.

### Phase 5 — The agent

New: **`app/ai_assistant/service.py` method** (extend the existing service — it already owns
`plan_open_notes`, `app/ai_assistant/service.py:200-241`) rather than a new package, so the
config, transport, and retry logic stay in one place.

| # | Task | Type |
| --- | --- | --- |
| 5.1 | `plan_daily_schedule(candidates, existing_slots, date) -> dict` — one call, bounded payload | modify |
| 5.2 | Prompt as an inline module constant or call-time string, per `AUDITOR_SYSTEM_PROMPT` convention (`app/auditor/service.py:21-33`) | create |
| 5.3 | `_validate_schedule_payload()` in the `app/notepad/routes.py:40-52` style: enum-check `activity`, **and verify every returned FSSAI number was in the offered candidate set** (the anti-hallucination check) | create |
| 5.4 | `Setting("DIARY_SCHEDULE_AI_ENABLED", ...)` default **off**, opt-in — plus `.env.example` | modify |
| 5.5 | Route: `POST /diary/schedule/plan` (propose only, persists nothing) and `POST /diary/schedule/confirm` (writes via `DiaryPlanner.record()`) | create |
| 5.6 | Owner force-stamped from `scoped_officer_name`; never from request input | create |
| 5.7 | Confirm-step template reusing the existing Bootstrap card style | create |
| 5.8 | Enforce the hard rule from A5: ambiguous candidates ⇒ ask, never guess | create |

**Payload contract** (mirror `DailyPlan`, minus the unenforced bounds):

```json
{
  "items": [{"fssai_number": "...", "company_name": "...", "activity": "field",
             "order": 1, "why": "..."}],
  "rationale": "markdown prose over the sequence"
}
```

Two deliberate departures from Daily Plan, both because that feature's gaps are known:

- **Enforce the item-count bound in the validator**, not in the prompt. Daily Plan's "top 3–5"
  exists only as prompt text (`app/ai_assistant/service.py:218`); `_validate_plan_payload`
  never checks `len(items)`.
- **Sort deterministically in code** after the call. Daily Plan's least-time-first ordering is
  prompt-only with no server-side sort.

**Done when:** flag off ⇒ 503 and nothing written; malformed payload ⇒ nothing written; a payload
naming an FSSAI number that was not offered is **rejected**; confirmed write lands in
`work_diary` with invariants intact.

### Phase 6 — Documentation

| # | Task | Type |
| --- | --- | --- |
| 6.1 | New glossary entries in `CONTEXT.md` for `PremisesResolver` and `DiaryPlanner`, in the established shape (**Module / canonical rule / public interface / leverage / locality / boundary**) | modify |
| 6.2 | New glossary entry for the schedule concept itself | modify |
| 6.3 | `docs/adr/0011-*.md` — next free number after `0010` | create |
| 6.4 | `docs/INDEX.md` entry | modify |
| 6.5 | **Decide** whether to also close the Daily Plan hit-rate drift (§11, R6) | decide |

---

## 6. Files to create

| Path | Purpose | Phase |
| --- | --- | --- |
| `app/services/premises_resolver.py` | The locality seam | 1 |
| `app/services/diary_planner.py` | The diary write seam | 2 |
| `tests/test_premises_resolver.py` | Resolver unit + integration | 1 |
| `tests/test_diary_planner.py` | `record()` invariants | 2 |
| `docs/adr/0011-schedule-planning-agent.md` | The decision | 6 |

## 7. Files to modify

| Path | Change | Phase |
| --- | --- | --- |
| `app/shared/config.py` | `SUPABASE_DB_URL`, `DIARY_SCHEDULE_AI_ENABLED` rows | 0, 5 |
| `.env.example` | Matching entries (**must ship with the config change**) | 0, 5 |
| `app/inspection/routes/inspection_routes.py` | Declared config; delegate to resolver; fix `source_tables` | 0, 1 |
| `scripts/supabase_premises_search.py` | Delegate to the resolver | 1 |
| `app/inspection/templates/inspection/premises_search.html` | Locality-oriented placeholder | 1 |
| `app/diary.py` | Delegate slot writes to `DiaryPlanner` | 2 |
| `app/ai_assistant/service.py` | `plan_daily_schedule` + prompt | 5 |
| `app/audit_hooks.py` | Audit `WorkDiaryEntry` | 4 |
| `CONTEXT.md` | Glossary entries | 6 |
| `docs/INDEX.md` | Index entry | 6 |

**Deliberately unchanged:** `app/models/lookup.py` (the naming inversion at `:16-20` is
intentional — do not "fix" it), `app/workdiary/engine.py` (its rebuild-at-print-time contract is
already correct), and `app/shared/rbac.py` (scoping already fails closed).

---

## 8. Test plan

| Suite | Coverage | Gate |
| --- | --- | --- |
| `tests/test_shared_config.py` | New declaration rows + `.env.example` parity | existing test, must pass |
| `tests/test_premises_resolver.py` | name match, **locality match**, both tables, dedupe, empty result, **connection failure distinguishable from empty** | new |
| `tests/test_diary.py` | **All 35 tests unchanged** | regression gate for Phase 2 |
| `tests/test_diary_planner.py` | `record()` one slot at a time; promotion; exclusivity; compaction; place truncation; count clamping | new |
| `tests/test_diary_schedule.py` | Flag off ⇒ 503; bad payload ⇒ no write; **unoffered FSSAI rejected**; confirm ⇒ row written; non-admin cannot target another officer | new |
| `tests/test_steering_docs.py` | `docs/INDEX.md` links resolve | existing test, must pass |

**Command** (fast shard — this is what CI runs, `.github/workflows/validation.yml:204-212`):

```bash
pytest -m "not slow" --tb=short -q legal_paragraph_detection_engine/tests/ tests/
```

**Test-isolation notes that will bite:** `tests/conftest.py` pins `DATABASE_URL` to a temp SQLite
file before any app import (`:19-20`), rebuilds the schema per test module (`:26-44`), and sets
`DISABLE_RBAC=1` session-wide (`:91`). Therefore:

- A new test that needs RBAC enforced must opt in explicitly.
- A new test that needs Supabase **must not** reach the network in the fast shard. Stub at the
  resolver seam — which is precisely why Phase 1 makes `resolve` the injectable boundary.
- Tests must not `print` (unlike `evaluation/*.py`, which have a `T201` exemption per
  `CODING_STANDARDS.md`).

---

## 9. Acceptance criteria

**Phase 0** — `cfg.supabase_db_url` resolves; parity test green; live acceptance query result and
timing recorded.

**Phase 1** — Given a live database, `resolve()` returns ≥1 candidate for a real road name.
Given a disconnected database, the caller can tell "no match" from "lookup failed". The route and
the CLI share one code path.

**Phase 2** — All 35 existing `tests/test_diary.py` tests pass unmodified. `record()` called once
for a lone duty-2 slot results in a duty-1 row.

**Phase 3** — D1 decided and recorded. If 3b/3c: migration runs forward on both SQLite and
PostgreSQL.

**Phase 4** — A planner write appears in the audit trail.

**Phase 5** — End-to-end: an FSO states an intent; candidates are shown; a schedule is proposed;
nothing is written until confirmed; after confirmation exactly one `work_diary` row exists and it
renders correctly in `/workdiary/pdf`.

**Phase 6** — `CONTEXT.md` entries use the established glossary shape; `tests/test_steering_docs.py`
passes.

---

## 10. Sequencing and dependencies

```
Phase 0 ──► Phase 1 ──► Phase 3 (decision) ──┐
                  │                          ├──► Phase 5 ──► Phase 6
                  └──────► Phase 2 ──► Phase 4 ─┘
```

Phases 0–2 are strictly sequential only in the sense that each is small. Phases 1 and 2 touch
disjoint files and can proceed in parallel by different people. Phase 3 is a **decision**, not
work — it can be answered while 1 and 2 run.

**Effort shape:** Phases 0, 1, 2 are each a fraction of a day. Phase 3 is 3b/3c-scale (1–3 days)
if chosen. Phase 5 is the largest single item because it carries the most new surface (route +
template + validator + tests).

---

## 11. Risks

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| R1 | **`SUPABASE_DB_URL` is unset in the deployed environment** (A3) — an operator action this plan cannot perform | **High** | Phase 0 acceptance surfaces it immediately; treat as a go/no-go gate before Phase 1 |
| R2 | **`full_address` text does not match how FSOs speak** (A2) | **High** | Run the Phase 0 acceptance query first. Fallback is `pg_trgm` — a larger change |
| R3 | No index ⇒ sequential scan on every `resolve()` | Medium | Measure in Phase 0. `analysis/premises_search_plan.md` set >500 ms as the trigger for a real index; defer until measured |
| R4 | **The audit hook may assume a single-column PK**, which `work_diary` does not have | Medium | Verify in Phase 4 before committing; fallback is planner-level logging (task 4.3) |
| R5 | Phase 2 refactor silently loses a split-duty invariant | Medium | The "35 tests unchanged" gate is the control. Add `record()`-level invariant tests |
| R6 | The glossary claims Daily Plan hit-rate feedback that does not exist (`CONTEXT.md:365-366`) | Low | Pre-existing. Do not repeat the error in 6.2 — write the glossary **after** the code, per `docs/agents/domain.md` |
| R7 | OpenCode-side agent (Option B) built instead of Option A | Low | A8. Phases 1–2 are importable services either way |
| R8 | Scope creep into auto-creating `Inspection` records | Low | Held out in §2.1 deliberately |
| R9 | `lookup_ce` 40-second rate limit stalls an agent loop | Low | Excluded in §2.1. The resolver never calls it |

---

## 12. Unverified items

Carried forward honestly rather than guessed:

- **Whether `SUPABASE_DB_URL` and `DATABASE_URL` resolve to the same database** (A1). Requires
  environment access. **Highest-priority check.**
- Whether `fssai_licenses.full_address` contains searchable road text (A2). Requires live data.
- Whether `app/audit_hooks.py` supports a composite-PK model (R4). Read of the three audited
  models shows none uses a composite PK, so the question is open.
- The `RecordAudit` model's own PK/referential requirements were not read.

---

## 13. Decisions required before Phase 5

| # | Decision | Options | Blocks |
| --- | --- | --- | --- |
| **D1** | Must the diary record *which* premises, or only how many? | Phase 3a / 3b / 3c | Phase 5 UI, Phase 3 |
| **D2** | Should the search auto-select a single match, or always require FSO disambiguation? | auto / ask | Phase 5 agent prompt |
| **D3** | Should the schedule write to the **Monthly Diary** only, or also draft an `Inspection`? | diary only / both | Phase 5 scope |
| **D4** | Does the feature replace or extend the existing Daily Plan page? | extend `/notepad/plan` / new page | Phase 5 routes |

**D4 deserves a note.** Daily Plan already exists and plans *notes*; this feature plans *premises*.
Extending it conflates two subjects with different vocabularies (`note_id` vs `fssai_number`) and
one payload shape cannot serve both cleanly. A separate page under `/diary/schedule/` is the
cleaner default, but it does mean a second AI surface and a second flag.

---

## 14. Where this plan came from

| Claim area | Source |
| --- | --- |
| Premises search route + defects | `app/inspection/routes/inspection_routes.py:456-539` |
| Premises search template | `app/inspection/templates/inspection/premises_search.html` |
| Registry schema + naming-inversion warning | `app/models/lookup.py:16-20`, `:33-71` |
| Config seam + `Setting` shape | `app/shared/config.py:42-63`, `:67-849`, `:688-721` |
| Diary model | `app/models/diary.py:24-40` |
| Diary write path + invariants | `app/diary.py:105-124`, `:127-136`, `:189-203`, `:211-253`, `:356-389`, `:397-556` |
| Diary tests (35) | `tests/test_diary.py` |
| Audit gap | `app/audit_hooks.py:159-161` |
| RBAC scoping | `app/shared/rbac.py:23-35`, `:103-115` |
| Daily Plan precedent | `app/notepad/routes.py:28-52`, `:55-139`; `app/ai_assistant/service.py:200-241` |
| Prompt convention | `app/auditor/service.py:21-33` |
| Service-module convention | `app/services/backup_restorer.py`, `CONTEXT.md:38-56` |
| Test isolation | `tests/conftest.py:19-20`, `:26-44`, `:91` |
| Prior design note | `analysis/premises_search_plan.md` |
| Migration hazard | `migrations/versions/add_diary_duty_seq.py:30-32`, `:44` |
| Boot schema check | `app/db_bootstrap.py:40-48`, `:82-119` |
| Extension points (Option B) | https://opencode.ai/docs/agents/ · /custom-tools/ · /permissions/ · /plugins/ |