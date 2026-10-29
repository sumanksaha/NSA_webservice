# Specification: Premises Resolution and FSO Schedule Planning

**Status:** specification only — no code, tests, or config changed.
**Date:** 2026-10-11.
**Implements:** [SCHEDULE_PLANNING_AGENT_IMPLEMENTATION_PLAN.md](SCHEDULE_PLANNING_AGENT_IMPLEMENTATION_PLAN.md)
(phases, file lists, risks).
**Evidence base:** [SCHEDULE_PLANNING_AGENT_RESEARCH.md](SCHEDULE_PLANNING_AGENT_RESEARCH.md)
(current-state verification).

**How to read this document.** Sections 3–5 are **normative**: `MUST`, `MUST NOT`, `SHOULD`, and
`MAY` carry their usual force. Section 6 onward is explanatory. Every requirement has an ID
(`FR-n`, `NFR-n`, `INV-n`, `V-n`, `SEC-n`) and appears in the traceability matrix (§13).

---

## 1. Problem statement

An FSO states a duty intention in free text — *"I will inspect a restaurant in NSC Bose Road"* —
and the system must:

1. resolve the statement to candidate FBO premises from the FSSAI licence/registration registry;
2. propose an **ordered** duty schedule for a date, with a rationale;
3. write the accepted schedule into the **Monthly Diary** (`work_diary`);
4. do so only after explicit FSO confirmation, and with an audit trail.

## 2. Scope and specified decisions

The implementation plan left four decisions open (§13 of that document). This spec is written
against the following defaults. **Each is a single-point change if the decision flips**, marked
inline.

| ID | Decision | Specified as | Flip cost |
| --- | --- | --- | --- |
| **D1** | Record *which* premises, or only how many? | ✅ **DECIDED 2026-10-11: count only.** No schema change. `work_diary.premises` stays `Integer`. **Phase 3 of the implementation plan is dropped.** | (closed) |
| **D2** | Auto-select a single match, or require disambiguation? | **Always ask** when >1 candidate (A5 hard rule) | One branch |
| **D3** | Monthly Diary only, or also draft an `Inspection`? | ✅ **CHANGED 2026-10-11: both.** The user asked for the selected premises to appear on the **inspection list**, so `/schedule/confirm` writes one draft `Inspection` row per selected premise alongside the diary slot. This reverses §2.1's exclusion. | (closed) |
| **D4** | Extend Daily Plan, or a new page? | **New page** at `/diary/schedule/`. Daily Plan plans *notes*; this plans *premises* — different subject vocabularies (`note_id` vs `fssai_number`), one payload cannot serve both | Route relocation only |

### 2.1 Explicitly out of scope

Timed itineraries with travel time; ward/zone/beats modelling; PostGIS /
`pg_trgm`; `lookup_ce` (KMC scraping — self-rate-limits to one call per 40 s
via a `fcntl` lock, `app/utils/lookup.py:51-53`, `:214-273`); the OpenCode-side
agent.

**Revised 2026-10-11 — two items moved out of "out of scope" and one moved in.**

- **Draft `Inspection` rows are now IN scope (D3 reversed).** `/schedule/confirm`
  writes one draft Inspection per selected premise so the work reaches the
  inspection list. This is a deliberate reversal of the original §2.1
  exclusion, driven by the stated requirement.
- **The LLM schedule planner is DEFERRED, not delivered.** FR-18…FR-24,
  V-1…V-8, §8.1's proposal/confirm state machine and AC-14…AC-19 are
  **normative but unimplemented**. The shipped flow is fully manual: the FSO
  searches, reviews the candidates, and selects. There is no model in the loop,
  so no proposal payload, no validator, and no double-validation exist yet.
  `DIARY_SCHEDULE_AI_ENABLED` is declared and defaults **off** (503) so the
  gap is explicit rather than silent. Revisit if ordering assistance is wanted.
- Auto-creating a *completed* Inspection (i.e. asserting findings) remains out
  of scope; only a draft row carrying the FBO identity and the compliance
  deadline is written.

---

## 3. Functional requirements

### 3.1 Configuration

| ID | Requirement |
| --- | --- |
| **FR-1** | `SUPABASE_DB_URL` **MUST** be declared in `_TABLE` (`app/shared/config.py:67-849`) as `Setting("SUPABASE_DB_URL", "supabase_db_url", str, "")`, and **MUST** have a matching `.env.example` entry in the same commit — `tests/test_shared_config.py::test_env_example_keys_are_declared` (`:195-204`) fails the build otherwise. |
| **FR-2** | `app/inspection/routes/inspection_routes.py:466` **MUST NOT** hand-roll a `current_app.config.get(...) or os.environ.get(...)` resolver. It **MUST** read through the `cfg` accessor. `CONTEXT.md:36` forbids re-implementing Pattern A. |
| **FR-3** | `PREMISES_SEARCH_LOCALITY_ENABLED` **MUST** be declared, default **`False`**, `opt_in=True`. When `False`, `premises_search()` **MUST** behave byte-for-byte as today (name-only matching). Rationale and precedent: `CONTEXT.md:166-169`. |
| **FR-4** | `DIARY_SCHEDULE_AI_ENABLED` **MUST** be declared, default **`False`**, `opt_in=True`. When `False`, the schedule routes **MUST** return `503`. Precedent: `app/notepad/routes.py:63-64`. |

### 3.2 Premises resolution

| ID | Requirement |
| --- | --- |
| **FR-5** | A new module `app/services/premises_resolver.py` **MUST** expose the interface in §5.2. It is the single owner of FSSAI-registry querying. |
| **FR-6** | The inline SQL in `app/inspection/routes/inspection_routes.py:488-509` and the duplicate in `scripts/supabase_premises_search.py:13-27` **MUST** both be deleted and replaced by calls to `PremisesResolver`. |
| **FR-7** | The SQLAlchemy engine **MUST** be constructed once per process and reused. The current per-request `create_engine` at `inspection_routes.py:480` **MUST NOT** remain. |
| **FR-8** | When `PREMISES_SEARCH_LOCALITY_ENABLED` is on, the match predicate **MUST** consider `full_address` in addition to `company_name`. `full_address` is currently selected but never a predicate (`inspection_routes.py:490-502`). |
| **FR-9** | User-supplied text **MUST** have `%`, `_`, and `\` escaped before interpolation into the `ILIKE` pattern, so a user typing `%` gets a literal `%` and not a wildcard scan. *(New behaviour — the current code interpolates `f"%{query}%"` raw.)* |
| **FR-10** | The resolver **MUST NOT** raise into callers. All failures are reported via `ResolveResult.error`. |
| **FR-11** | The `source_tables` audit trail (`inspection_routes.py:527-531`) **MUST** be populated. It is currently always empty because the search block is `GET`-guarded (`:482`) while `source_tables` is read on the POST branch. |
| **FR-12** | The search template placeholder (`app/inspection/templates/inspection/premises_search.html:19`) **SHOULD** advertise that a road or locality may be searched once FR-8 ships. |

### 3.3 Diary writing

| ID | Requirement |
| --- | --- |
| **FR-13** | A new module `app/services/diary_planner.py` **MUST** expose the interface in §5.3 and **MUST** enforce INV-1…INV-7 (§6). |
| **FR-14** | `DiaryPlanner` **MUST** import — never re-declare — `ACTIVITIES`, `COUNT_ACTIVITIES`, `EXCLUSIVE_ACTIVITIES`, `MAX_COUNT`, `PLACE_MAX_LEN`, `FIELD_ACTIVITY` from `app/diary.py:38-76`, and **MUST** reuse `_summary_line` (`:211-226`) and `_parse_count` (`:127-136`). |
| **FR-15** | `app/diary.py::bulk()` **MUST** delegate slot persistence to `DiaryPlanner`. It **MUST** retain month-resolution, form parsing, and the redirect. |
| **FR-16** | All 35 tests in `tests/test_diary.py` **MUST** pass **unmodified**. This is the Phase 2 regression gate. |
| **FR-17** | The three split-duty behaviours — promotion (`app/diary.py:420-428`), holiday/leave exclusivity (`:430-439`), post-save compaction (`:455-485`) — **MUST** move into `DiaryPlanner` without behavioural change. |

### 3.4 Schedule planning

| ID | Requirement |
| --- | --- |
| **FR-18** | `AIAssistantService.plan_daily_schedule(...)` **MUST** be added to `app/ai_assistant/service.py`, alongside `plan_open_notes` (`:200-241`), so config, transport, and retry logic stay in one place. |
| **FR-19** | The model **MUST NOT** select which database operation runs. The route fixes the action; the model fills a bounded, allowlisted payload. Precedent: `CONTEXT.md:414` — *"The LLM never picks the Act."* |
| **FR-20** | Premise resolution **MUST NOT** involve the model. It is deterministic SQL (assumption A9). |
| **FR-21** | `POST /diary/schedule/plan` **MUST NOT** write to the database. |
| **FR-22** | `POST /diary/schedule/confirm` **MUST** write via `DiaryPlanner.record()` only, never raw SQLAlchemy. |
| **FR-23** | A proposal that fails any validator rule (§7) **MUST** write nothing and **MUST** return an error. Precedent: `app/notepad/routes.py:82-94`. |
| **FR-24** | If `ResolveResult` returns more than one candidate, the agent **MUST** ask the FSO to disambiguate and **MUST NOT** guess (D2). This rule **MUST** appear in the agent prompt. |

---

## 4. Non-functional requirements

| ID | Requirement |
| --- | --- |
| **NFR-1** | `ResolveResult` **MUST** let a caller distinguish "no match" from "lookup failed" — the distinction that makes a hallucination report distinguishable from an outage. Mirrors the `LookupResult` invariant at `app/utils/lookup.py:84-85`. |
| **NFR-2** | No test in the fast shard **MAY** reach the network. The resolver is the injection seam; tests stub at it. `tests/conftest.py:133-152` is the existing LLM-stub pattern. |
| **NFR-3** | Application modules **MUST NOT** `print` (`CODING_STANDARDS.md`, "Evaluation CLIs print to stdout"). Only `evaluation/*.py` carries the `T201` exemption. |
| **NFR-4** | No root-level `*.py` file may be added — `tests/test_repo_root_layout.py` freezes the root inventory; one-off scripts belong in `scripts/` (`CODING_STANDARDS.md`). |
| **NFR-5** | Prompt text **MUST** be an inline module constant or a call-time string. There is no prompts package in this repo. Convention: `AUDITOR_SYSTEM_PROMPT` (`app/auditor/service.py:21-33`). |
| **NFR-6** | The LLM call is one synchronous request with the service's existing retry budget. No Celery — it was removed 2026-09-13; the queue is QStash (`CHANGELOG.md:66`, `app/utils/qstash_client.py:25-37`). |
| **NFR-7** | Every `work_diary` write produced by this feature **MUST** be attributable. See FR-25 below and §9. |

---

## 5. Data contracts

### 5.1 `PremiseCandidate`

```python
@dataclass(frozen=True, slots=True)
class PremiseCandidate:
    """One FSSAI registry row. Immutable; safe to hand to a template or a prompt."""

    fssai_number: str    # license_no | registration_no — the canonical premise id (A4)
    company_name: str | None
    full_address: str | None
    expiry_date: str | None   # DD-MM-YYYY Text, pass-through only — see app/models/lookup.py:22-25
    source_table: str          # "fssai_licenses" | "fssai_registrations"
```

`expiry_date` **MUST** stay a string. A typed `Date` column would change the `lookup_fssai` API
response contract (`app/models/lookup.py:22-25`).

### 5.2 `PremisesResolver`

```python
def resolve(text: str, *, limit: int = 50) -> ResolveResult: ...
def search_by_fssai(number: str) -> ResolveResult: ...
```

```python
@dataclass(frozen=True, slots=True)
class ResolveResult:
    found: bool
    error: str | None = None
    candidates: tuple[PremiseCandidate, ...] = ()

    def __bool__(self) -> bool: return self.found
```

**Invariant (NFR-1), copied from `LookupResult` (`app/utils/lookup.py:84-85`):**
`found is False and error is None` ⇒ the lookup **completed without error and found no row**.

**Matching rules (normative), applied to `resolve`:**

| # | Rule |
| --- | --- |
| M1 | Escape `%`, `_`, `\` in `text`; build the pattern as `'%' + escaped + '%'`. |
| M2 | Query **both** tables. `fssai_licenses` by `license_no`; `fssai_registrations` by `registration_no`. |
| M3 | Predicate when FR-8 is on: `company_name ILIKE :q OR full_address ILIKE :q`. When off: `company_name ILIKE :q` only. |
| M4 | Cap each table at `limit` **before** merging; cap the merged list at `limit`. |
| M5 | Dedupe on `(fssai_number, full_address.strip().casefold())`. |
| M6 | Order: exact `company_name` match first, then `company_name` prefix match, then address-only match. |
| M7 | `error` **MUST** be non-`None` on connection or query failure, and `candidates` **MUST** then be empty. |
| **M8** | **Normalisation.** A raw `ILIKE` over `full_address` is **NOT** sufficient — see §5.6 and FR-26. Matching **MUST** run against a normalised query and a normalised column expression. |
| **M9** | **Disambiguation.** Results on a *different* road sharing a trailing name (e.g. `SARAT BOSE ROAD`) **MUST NOT** be presented as matches for the queried road. See FR-27. |

### 5.6 Address normalisation — normative, new from live measurement

Live data shows `full_address` is free text with at least **nine** distinct spellings of one street.
Measured 2026-10-11 against the live registry:

| Form | Rows |
| --- | --- |
| `N.S.C BOSE ROAD` | 146 |
| `N.S.C. BOSE ROAD` | 145 |
| `N.S.C.BOSE ROAD` | 112 |
| `N.S.C. Bose Road` | 59 |
| `N S C BOSE ROAD` | 47 |
| `NSC BOSE ROAD` | 37 |
| `N. S. C. BOSE ROAD` | 28 |
| `N.S.C Bose Road` | 25 |
| `NETAJI SUBHAS CHANDRA BOSE ROAD` (long form) | 97 |

A single `ILIKE '%NSC Bose Road%'` returns **97** rows — it misses every punctuated variant.

Conversely `ILIKE '%Bose Road%'` returns **3,924** rows spanning **different streets**:

| Street | Rows | Same road? |
| --- | --- | --- |
| `NETAJI SUBHAS CHANDRA BOSE ROAD` | 97 | yes |
| `N.S.C. BOSE ROAD` | 39 | yes |
| `A.J.C BOSE ROAD` / `AJC Bose Road` (Entally) | 36 | **no** |
| `SARAT BOSE ROAD` | 35 | **no** |
| `DOCTOR SUDHIR BOSE ROAD` | 14 | **no** |

**Consequence.** A naive substring match both **under-returns** (missing ~590 correctly-spelled NSC
Bose Road rows) and **over-returns** (surfacing Sarat/AJC Bose Road as if it were NSC Bose Road). An
agent handed that list will confidently report premises on the wrong street — the exact failure
this feature exists to prevent.

**Required normalisation pipeline:**

| Step | Rule |
| --- | --- |
| N1 | Uppercase; strip control characters — live data contains literal `_x000d_` carriage-return artifacts. |
| N2 | Collapse all runs of whitespace to one space. Handles `N. S. C  BOSE`. |
| N3 | Strip `.` entirely. Collapses `N.S.C.` to `NSC`. |
| N4 | Expand a configured alias table before matching, e.g. `NSC BOSE ROAD` → `NETAJI SUBHAS CHANDRA BOSE ROAD`. Aliases **MUST** be data, not code branches. |
| N5 | Match on **tokens**, not raw substrings, so `BOSE ROAD` alone cannot match a road qualified by another name. |

**FR-26.** `PremisesResolver` **MUST** apply N1–N5 before matching. A raw `ILIKE` over
`full_address` **MUST NOT** ship.

**FR-27.** Where the query names a road in a known family (`*BOSE ROAD*`), the result set **MUST**
either be restricted by alias match, or be annotated with the matched road name so a caller cannot
mistake `SARAT BOSE ROAD` for `NSC BOSE ROAD`. Silent conflation **MUST NOT** occur.

> **Implementation note.** Applying N1–N3 in Python over 126,870 rows is not viable. The practical
> route is a generated or expression column (e.g. `upper(regexp_replace(full_address,'\s+',' ','g'))`)
> maintained by the bi-monthly bulk load (`scripts/load_fssai_lookup.py`). **This is a schema
> change and is deferred** — see §14, Q5.

`search_by_fssai` dispatches by first character — `"1"…` → licenses, `"2"…` → registrations —
mirroring `app/utils/lookup.py:316-325`. **The inversion is historical and intentional**
(`app/models/lookup.py:16-20`); this spec forbids "fixing" it. When assumption **A1** resolves
true (same database), this method **SHOULD** delegate to the existing `lookup_fssai`
(`app/utils/lookup.py:288-341`) rather than re-query.

### 5.3 `DiaryPlanner`

```python
def record(
    owner: str,
    work_date: str,
    duty_seq: int,
    activity: str | None,
    premises: int = 0,
    samples: int = 0,
    notes: str | None = None,
    place: str | None = None,
) -> str:  # "saved" | "deleted" | "noop"
```

**Return values** match the existing `_save_slot` vocabulary (`app/diary.py:356-389`), so the
route's counter logic keeps working unchanged.

| Return | Condition |
| --- | --- |
| `noop` | `activity` is non-empty and not in `ACTIVITIES`; or the stored row already equals the incoming values |
| `deleted` | `activity` is empty **and** the day is blank, and a row existed |
| `saved` | otherwise |

`owner` is an explicit argument so the module is callable without a request context. **SEC-1**
constrains where it may come from.

### 5.4 Config rows

```python
Setting("SUPABASE_DB_URL",           "supabase_db_url",  str, "", help="Supabase Postgres DSN used for FSSAI registry lookups.")
Setting("PREMISES_SEARCH_LOCALITY_ENABLED", "premises_search_locality_enabled", bool, False, opt_in=True,
        help="Match premises by full_address as well as company_name.")
Setting("DIARY_SCHEDULE_AI_ENABLED", "diary_schedule_ai_enabled", bool, False, opt_in=True,
        help="Kill switch for AI schedule planning (LLM spend). 503 when off.")
```

### 5.5 Agent payload

**Input to the prompt:** the statement, the resolved candidates, the existing `work_diary` rows
for `(owner, work_date)`, and the date.

**Required output — a single JSON object, no fences, no prose:**

```json
{
  "items": [
    {
      "fssai_number": "12345678901234",
      "company_name": "Sharma Restaurant",
      "activity": "field",
      "order": 1,
      "why": "One-sentence justification for this position in the day."
    }
  ],
  "rationale": "Markdown prose about the sequence, not the individual items."
}
```

`activity` ∈ `ACTIVITIES` (`app/diary.py:38-45`). `order` is 1-based and contiguous.

**Two deliberate departures from Daily Plan**, both because that feature's gaps are documented:

- the `items` bound **MUST** be enforced in the validator, not in the prompt — Daily Plan's
  "top 3–5" exists only as prompt text (`app/ai_assistant/service.py:218`) and
  `_validate_plan_payload` never checks `len(items)`;
- the ordering **MUST** be re-applied deterministically in code after the call — Daily Plan has no
  server-side sort.

---

## 6. Invariants

Normative. Every one **MUST** hold after `DiaryPlanner.record()` returns.

| ID | Invariant | Origin |
| --- | --- | --- |
| **INV-1** | For any `(owner, work_date)`: if a row with `duty_seq=2` exists, a row with `duty_seq=1` **MUST** also exist | `app/diary.py:420-428`, `:455-485` |
| **INV-2** | If `duty_seq=1.activity` ∈ `{holiday, leave}`, no `duty_seq=2` row **MAY** exist | `app/diary.py:430-439` |
| **INV-3** | If `activity` ∉ `COUNT_ACTIVITIES`, then `premises == 0 and samples == 0` | `app/diary.py:382-384` |
| **INV-4** | `activity` ∈ `ACTIVITIES ∪ {None, ""}` | `app/diary.py:374-375` |
| **INV-5** | `len(place_of_visit) ≤ 200`; an empty value **MUST** be stored as `NULL`, not `""` | `app/diary.py:68`, `:312` |
| **INV-6** | `0 ≤ premises ≤ 999_999` and `0 ≤ samples ≤ 999_999` | `app/diary.py:76`, `:136` |
| **INV-7** | `work_date` matches `^\d{4}-\d{2}-\d{2}$` | non-conforming rows are silently dropped from reports (`app/workdiary/engine.py:249-251`) |
| **INV-8** | `duty_seq` ∈ `{1, 2}` | `app/models/diary.py:31` |
| **INV-9** | At most one row per `(fso_name, work_date, duty_seq)` | composite PK, `app/models/diary.py:29-31` |

---

## 7. Validation rules

Applied to the model's return value before anything is written. Modelled on
`_validate_plan_payload` (`app/notepad/routes.py:40-52`). On failure: **write nothing**, return
the error.

| ID | Rule | Error code |
| --- | --- | --- |
| **V-1** | Payload is a JSON object | `payload_not_object` |
| **V-2** | Contains `items` (list) and `rationale` (str) | `payload_incomplete` |
| **V-3** | `1 ≤ len(items) ≤ 5` | `too_many_items` / `no_items` |
| **V-4** | Every `item.activity` ∈ `ACTIVITIES` | `invalid_activity` |
| **V-5** | **Every `item.fssai_number` was in the offered candidate set** | `ungrounded_candidate` |
| **V-6** | No duplicate `fssai_number` within `items` | `duplicate_candidate` |
| **V-7** | `order` values are 1-based and contiguous | `invalid_order` |
| **V-8** | `owner` was not present in the payload (server-derived only) | `owner_in_payload` |

**V-5 is the anti-hallucination rule** and is the single most important line in this document. It
is the direct analogue of the check at `app/notepad/routes.py:47-48`, where a returned `note_id`
that was never offered is rejected.

---

## 8. Route contracts

| Route | Method | Writes? | Behaviour |
| --- | --- | --- | --- |
| `/diary/schedule/` | GET | no | Today's schedule; existing `work_diary` slots for the date; prior proposals |
| `/diary/schedule/plan` | POST | **no** | Body: statement + date. Resolves candidates, generates a proposal, returns it for review |
| `/diary/schedule/confirm` | POST | **yes** | Body: the proposal as shown, plus explicit approval. Validates (§7), then calls `DiaryPlanner.record()` once per item |

All three **MUST** carry `@login_required` and CSRF. `DIARY_SCHEDULE_AI_ENABLED` off ⇒ `503` on
both POSTs, matching `app/notepad/routes.py:63-64`.

### 8.1 Confirm-flow state machine

```
        statement
            │
            ▼
    ┌───────────────┐  error != None
    │  resolve()    ├──────────────────► E1 resolver_unavailable
    └───────┬───────┘
            │ error is None
            ▼
      len(candidates)
       ╱         ╲
   == 0         >= 1
     │             │
     ▼             ▼
 E2 no_match   len == 1 ?
                   ╱   ╲
                yes    no
                 │      │
                 │      ▼
                 │   E3 ambiguous ──► ask the FSO (D2), never guess
                 │
                 ▼
        plan_daily_schedule()
                 │
                 ▼
        validate (§7)
                 │            any failure
                 │                ▼
                 │            E4 payload_invalid
                 ▼            E5 ungrounded_candidate
        PROPOSAL SHOWN                │
                 │                    ▼
                 │              nothing written
                 ▼
            FSO confirms
                 │
                 ▼
      validate (§7) again ──► re-validate on the way in; the payload
                 │            round-trips through the client
                 ▼
   DiaryPlanner.record() per item
                 │
                 ▼
        N work_diary rows written
```

**The proposal is validated twice** — once when generated, once when confirmed. The client sends
the payload back, and a modified payload **MUST** be re-validated against the same rules. V-5 in
particular must be re-checked against the candidate set recomputed at confirm time, because a
client can alter a payload between the two calls.

---

## 9. Security requirements

| ID | Requirement |
| --- | --- |
| **SEC-1** | `owner` **MUST** originate from `scoped_officer_name(current_user)` (`app/shared/rbac.py:103-115`) and **MUST NOT** be read from request data. A non-admin's `?fso_name=` is ignored (`app/diary.py:115-117`). |
| **SEC-2** | An unbound non-admin resolves to `""`, which matches nothing — deny-by-default. This **MUST** be preserved in the schedule routes. |
| **SEC-3** | Both POSTs **MUST** be CSRF-protected. Neither **MAY** be `@csrf.exempt`. |
| **SEC-4** | The model **MUST NOT** receive credentials, connection strings, or any other officer's diary rows. Prompt input is limited to the candidates and the owner's own slots for the target date. |
| **SEC-5** | Registry queries **MUST** remain parameterised. Identifiers are hardcoded; only values are bound — the property the existing code documents at `inspection_routes.py:485-486`. |
| **SEC-6** | Database errors **MUST NOT** leak raw driver text to the user. Existing behaviour: log server-side, show a generic message (`inspection_routes.py:511-519`). |

---

## 10. Audit requirements

| ID | Requirement |
| --- | --- |
| **FR-25** | Writes made through this feature **MUST** be attributable. Currently `audited_model_types = (Adjudication, Bill, CaseFile)` (`app/audit_hooks.py:159-161`) excludes `WorkDiaryEntry`. |
| **AUD-1** | Phase 4 **MUST** first determine whether the hook supports a composite primary key — `work_diary`'s PK is `(fso_name, work_date, duty_seq)` (`app/models/diary.py:29-31`) and none of the three currently audited models uses one. |
| **AUD-2** | If it does not, attribution **MUST** be implemented at the planner level (log actor + timestamp + intent), and the limitation **MUST** be recorded in the ADR. |

---

## 11. Error taxonomy

| Code | HTTP | Meaning | User-facing behaviour |
| --- | --- | --- | --- |
| `resolver_unavailable` | 200 + error banner | Supabase unreachable | "Lookup failed. Try later." — **never** "no such premises" |
| `no_match` | 200 | Clean lookup, zero rows | "No registered premises matched." Offer a broader query |
| `ambiguous` | 200 | >1 candidate | Render the selection list (D2) |
| `payload_invalid` | 200 | Model output failed §7 | "Could not build a plan. Try rephrasing." Nothing written |
| `ungrounded_candidate` | 200 | Model named an unoffered FSSAI number | Same as above; **log it** — it is a grounding failure |
| `flag_disabled` | **503** | `DIARY_SCHEDULE_AI_ENABLED` off | Precedent: `app/notepad/routes.py:63-64` |
| `not_authorized` | 302 | Login gate | Global gate, `app/shared/rbac.py:56-59` |

---

## 12. Acceptance criteria

Written as testable assertions. Each maps to a test in §13.

**Premises resolution**
- **AC-1** Given a live registry and `resolve("NSC Bose Road")`, when the locality flag is on,
  then ≥1 candidate is returned. *(validates A2)*
- **AC-2** Given `resolve("%")`, then the pattern contains no unescaped wildcard and the result
  is an ordinary empty match, not a full scan.
- **AC-3** Given a database that raises on connect, then `error is not None` and `found is False`.
- **AC-4** Given a clean lookup with no rows, then `error is None` and `found is False`.
  *(AC-3 + AC-4 together prove NFR-1.)*
- **AC-5** Given the locality flag off, then `resolve()` matches `company_name` only and the
  premises-search route's output is unchanged.

**Diary writing**
- **AC-6** Given the refactor, then all 35 tests in `tests/test_diary.py` pass unmodified.
- **AC-7** Given `record(owner, d, 2, "vvip", ...)` on a day with no slot 1, then a `duty_seq=1`
  row exists and no `duty_seq=2` row exists. *(INV-1)*
- **AC-8** Given slot 1 is `holiday`, then no `duty_seq=2` row exists. *(INV-2)*
- **AC-9** Given `activity="leave"`, then `premises == 0 and samples == 0`. *(INV-3)*
- **AC-10** Given a 250-character place, then the stored value is 200 characters. *(INV-5)*
- **AC-11** Given an empty place, then the stored value is `NULL`. *(INV-5)*
- **AC-12** Given `record()` called twice with identical values, then the second returns `noop`.
- **AC-13** Given a non-admin `owner` is locked to their binding, then a request carrying another
  officer's `fso_name` writes nothing. *(SEC-1)*

**Schedule planning**
- **AC-14** Given the AI flag off, then both POSTs return 503 and no row is written.
- **AC-15** Given a payload naming an FSSAI number that was not offered, then the response is
  `ungrounded_candidate` and **no row is written**. *(V-5)*
- **AC-16** Given a payload of 6 items, then `too_many_items` and nothing is written. *(V-3)*
- **AC-17** Given a tampered payload at confirm time containing an unoffered number, then nothing
  is written. *(§8.1 double validation)*
- **AC-18** Given a valid confirmed payload of 2 items, then exactly 2 `work_diary` rows exist and
  each renders correctly in `/workdiary/pdf`.
- **AC-19** Given the model raises, then no row is written and the response is
  `payload_invalid`. *(Precedent `app/notepad/routes.py:82-86`.)*

---

## 13. Traceability

| Requirement | Test file | Phase |
| --- | --- | --- |
| FR-1, FR-2 | `tests/test_shared_config.py` (existing `:195-204`) | 0 |
| FR-3, FR-5…FR-12, AC-1…AC-5 | `tests/test_premises_resolver.py` *(new)* | 1 |
| FR-13…FR-17, INV-1…INV-9, AC-6…AC-13 | `tests/test_diary.py` (existing, unmodified) + `tests/test_diary_planner.py` *(new)* | 2 |
| FR-18…FR-24, V-1…V-8, AC-14…AC-19 | `tests/test_diary_schedule.py` *(new)* | 5 |
| FR-25, AUD-1, AUD-2 | `tests/test_audit_hooks.py` *(modify)* | 4 |
| SEC-1…SEC-6 | `tests/test_rbac.py` (existing, extended) | 2, 5 |

**Fast-shard command** (what CI runs, `.github/workflows/validation.yml:204-212`):

```bash
pytest -m "not slow" --tb=short -q legal_paragraph_detection_engine/tests/ tests/
```

---

## 14. Question status

### 14.1 Resolved — answered against the live database, 2026-10-11

| # | Answer | Evidence |
| --- | --- | --- |
| **Q1** | **The Supabase project *is* the app's own database.** It holds `fssai_licenses` (33,487 rows) and `fssai_registrations` (93,383) **alongside** `work_diary` (48), `inspection` (2), `user` (1), `fso` (21), `case_files`, `daily_plan`, `note`. 51 tables total. | live `information_schema` query |
| **Q2** | **Partly — and this is the important finding.** Text is searchable, but raw `ILIKE` is *wrong in both directions*. Nine spellings of NSC Bose Road exist; `%NSC Bose Road%` catches only 97, while `%Bose Road%` returns 3,924 rows polluted with **Sarat Bose Road**, **AJC Bose Road** and **Doctor Sudhir Bose Road** — different streets. | §5.6 |
| **Q3** | **Yes.** `SUPABASE_DB_URL`, `SUPABASE_URL` and `SUPABASE_API_KEY` are set in `.env` (gitignored — `.gitignore:255`). `DATABASE_URL` deliberately left empty so local dev stays on SQLite. | `.env` |
| **D1** | **Decided: count only.** No premise-linkage schema change; Phase 3 is dropped. | user decision, 2026-10-11 |
| **R3** | **Latency crosses the repo's own trigger** — measured **755–2,271 ms** (noisy shared instance) against the >500 ms threshold in `analysis/premises_search_plan.md`. Plain btree indexes were created and **did not help**; see §14.2. | live timing |

### 14.2 Indexing outcome

Plain btree indexes were created on `fssai_licenses(company_name)` and
`fssai_registrations(company_name)` (`CREATE INDEX CONCURRENTLY`, ~1.3 MB and ~4.2 MB).

| Query | Before | After | Verdict |
| --- | --- | --- | --- |
| `full_address ILIKE '%NSC Bose Road%'` (substring) | 2,271 ms | 1,166 ms | Noise — a btree cannot serve a substring scan |
| `company_name ILIKE 'MAST%'` (prefix) | 590 ms | 603 ms | **No improvement** |

`EXPLAIN` shows why:

```
Index Only Scan using idx_fssai_registrations_company_name on fssai_registrations
  Filter: (company_name ~~* 'MAST%'::text)
```

The whole index is scanned and the predicate applied as a residual `Filter` — there is **no
`Index Cond`**. The database collation is `en_US.UTF-8`, under which `ILIKE 'prefix%'` cannot
produce an index bound.

**The fix is an operator class, not an extension** — `text_pattern_ops` ships with core
PostgreSQL:

```sql
CREATE INDEX CONCURRENTLY idx_fssai_registrations_company_name
  ON fssai_registrations (company_name text_pattern_ops);
```

**Recommendation (not applied — operator decision, Q6).** Replace the two plain btrees with
`text_pattern_ops`. That alone makes prefix company-name search fast. It will **still not** serve
the locality query, which is a substring scan; that needs `pg_trgm` or the normalised-column route
in §5.6. **This is the one performance gap left open.**

### 14.3 Still open

| # | Question | Blocks | Owner |
| --- | --- | --- | --- |
| **Q4** | Does `app/audit_hooks.py` support a composite PK? (AUD-1) | Phase 4 approach | Source read + test |
| **Q5** | Approve a **generated/expression column** on the registry carrying normalised search text? Schema change on the shared database, refreshed by the bi-monthly bulk load. | FR-26 / §5.6 | **Operator decision** |
| **Q6** | Adopt `text_pattern_ops` to replace the plain btrees? (§14.2) | Latency work | **Operator decision** |
| **D4** | New page, or extend Daily Plan? | Route layout | **Product decision** |

---

## 15. Change log

| Date | Change |
| --- | --- |
| 2026-10-11 | Initial specification. Decisions D1–D4 fixed to the plan's recommended defaults (§2). |
| 2026-10-11 | **Verified against the live database.** Q1/Q2/Q3/R3 answered (§14.1). D1 decided — count only, Phase 3 dropped. |
| 2026-10-11 | **Added FR-26 / FR-27 and M8 / M9 + §5.6.** Live address data proved a raw `ILIKE` on `full_address` both under- and over-returns; normalisation and road-family disambiguation are now mandatory. |
| 2026-10-11 | Created plain btree indexes on `company_name` (§14.2); measured no benefit under `en_US.UTF-8`. Recorded `text_pattern_ops` as the recommendation (Q6, not applied). |
| 2026-10-11 | **Review remediation.** (i) `record()` now enforces INV-1/INV-2/INV-7 and returns the full `"saved"/"deleted"/"noop"` vocabulary — previously these lived only in `record_many` and a malformed date was accepted. (ii) `premises_search()` and `scripts/supabase_premises_search.py` now delegate to `PremisesResolver` (FR-2/6/7/11); per-request `create_engine` and the duplicated inline SQL are gone. (iii) `bulk()` delegates persistence to `DiaryPlanner.record()`; dead `_upsert`/`_is_unchanged_stored` deleted. (iv) `PREMISES_SEARCH_LOCALITY_ENABLED` + `DIARY_SCHEDULE_AI_ENABLED` declared (FR-3/4). (v) `WorkDiaryEntry` added to `audited_model_types` with composite-key id derivation (FR-25/AUD-1 — answered: the hook did not support composite keys). (vi) `road_group` string replaced by a `RoadGroup` value type; M6 ordering implemented. (vii) Cross-module imports now use public names exposed by `app/diary.py`. |