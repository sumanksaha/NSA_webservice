# An agent that plans an FSO's day from a spoken intent: what exists, what blocks it, and the two ways to build it

**Question.** Can an agent take a free-text statement such as *"I will inspect a restaurant in
NSC Bose Road"*, search the licence/registration database, propose a daily schedule with
suggestions, and update the **Work Diary**? If so, how should it be created?

**Date:** 2026-10-10. **Status:** research only — no code, tests, or config changed.
**Scope note:** primary sources only (this repo's source code, and the official OpenCode
documentation at `opencode.ai/docs`). Repo claims below are `path:line` citations into the
working tree; OpenCode claims are cited to doc URLs.

---

## 1. Verdict

Yes — but **the agent is not the hard part.** Three of the four capabilities already exist in
this repo, and the fourth is blocked by data, not by tooling.

| # | Capability you asked for | Status | Blocker |
| --- | --- | --- | --- |
| 1 | Resolve "restaurant in NSC Bose Road" → premises | **Route exists, cannot answer this query** | Search matches `company_name` only; `full_address` is never searched |
| 2 | Suggest a daily schedule | **Half exists** | `DailyPlan` plans *Notepad notes*, not premises; no duration/route data exists |
| 3 | Update the Work Diary | **No programmatic path at all** | The only writer is an HTML form POST that replaces a whole month |
| 4 | Confirm before writing | **Missing** | `WorkDiaryEntry` is not in `audited_model_types` |

The repo's own doctrine decides the shape of the fix. `CONTEXT.md` is explicit that behaviour
belongs in one deep module behind a seam, and the deletion test recorded in the
`InspectionPhotoService` entry — *"it never gained a caller"* — is the standing rule: a seam
that nothing calls gets deleted. So the work is **two seams first, agent second**.

---

## 2. What already exists

### 2.1 The premises search route is real

`GET|POST /inspection/premises/search` (`app/inspection/routes/inspection_routes.py:456`) already
queries Supabase for both registry tables:

```sql
SELECT license_no, company_name, full_address, expiry_date
FROM fssai_licenses WHERE company_name ILIKE :name ORDER BY company_name LIMIT 50
```

`app/inspection/routes/inspection_routes.py:490-491`, with the registrations twin at `:501-502`.
The same query exists as a CLI at `scripts/supabase_premises_search.py:13-27`. The tables are
mapped by `app/models/lookup.py:33-71` and the migration rationale is recorded at
`DEPLOYMENT.md:41-51`.

There is also a prior design note for this exact flow — `analysis/premises_search_plan.md` — whose
own "lazy ladder" names the missing step as *"Add when query is slow (>500ms) or when multi-user
concurrency requires it."* §3.1 shows that threshold is already crossed by the wrong axis.

### 2.2 An AI planner already exists, for a different subject

**Daily Plan** (`CONTEXT.md:357-366`) generates an AI battle plan over an FSO's own open Notepad
notes: the top 3–5 plus a full ranking, least-time-first, append-only `DailyPlan` rows. It is
implemented end to end and is the closest precedent for "AI proposes a day's work":

- model — `app/models/notepad.py:91-115` (append-only, `payload` JSON blob, **no child model**);
- routes — `POST /notepad/plan/generate`, `GET /notepad/plan` (`app/notepad/routes.py:55-139`);
- generation — `AIAssistantService.plan_open_notes` (`app/ai_assistant/service.py:200-241`),
  one synchronous call, prompt built inline at `:213-233`;
- validation — `_validate_plan_payload` (`app/notepad/routes.py:40-52`), which checks the required
  keys, the effort-bucket enum, and that every returned `note_id` was actually offered;
- persist — validate, then a single `db.session.add` + `commit` (`app/notepad/routes.py:88-94`);
- flag — `NOTEPAD_AI_ENABLED`, default **on** (`app/shared/config.py:722-730`);
- tests — `tests/test_notepad_daily_plan.py` (5 tests), which patch the `_ai_service` factory seam
  (`app/notepad/routes.py:28-32`) rather than the transport.

Two properties of that path are worth copying verbatim, because they are the reason the feature
is safe: **the model never selects which DB operation runs**, and **nothing is persisted until a
strict allowlist validation passes**. See §5.3.

### 2.3 There is no tool-calling anywhere in this repo

`grep` for `tools=`/`tool_choice`/`function_call`/`bind_tools`/`tool_calls` returns **zero**
matches repo-wide. No LLM SDK is used in `app/rag/**`. Every DB write reachable from an LLM is a
fixed route with hand-rolled validation immediately before one guarded commit. The only
model-adjacent "act selection" in the codebase is deliberately the opposite of an agent:
`DeterministicActSelector` (`app/rag/advisor/`), invoked by the `fso_advisory` graph node, with
`CONTEXT.md:414` stating the invariant plainly — *"The LLM never picks the Act."*

That is the precedent this feature should follow too: the model proposes, deterministic code
disposes.

---

## 3. The blockers

### 3.1 B1 — "NSC Bose Road" cannot be resolved to a premise

Three independent reasons, each verified:

1. **The query never looks at the address.** Both statements filter on `company_name` only
   (`inspection_routes.py:491`, `:502`). `full_address` is selected into the result but is never
   a predicate. A statement naming a *road* therefore returns zero rows — always.
2. **There is no locality structure to resolve against.** No ward, zone, beat, pincode, or
   coverage-area column exists on any FBO-bearing table. `full_address` is one opaque `Text`
   blob (`app/models/lookup.py:47`, `:68`). There is no PostGIS, no `earthdistance`, no
   `pg_trgm`, no geo-index.
3. **Nothing is indexed.** Every declared `db.Index` in the model layer was enumerated; **zero**
   index a name or an address column. `fssai_licenses.company_name` is unindexed, which is why
   the route must `ILIKE '%q%'` with `LIMIT 50` — a full scan. This is the >500 ms threshold
   `analysis/premises_search_plan.md` predicted, and it is reached on a single non-selective
   scan.

The nearest things to a route/zone assignment: the `FSO` table holds only `fso_name` plus SMTP
config (`app/models/inspection.py:11-25`); `WorkDiaryEntry.place_of_visit` is free text capped at
200 chars (`app/models/diary.py:36`). There is nothing for a scheduler to reason over.

> **This is the one blocker that must be fixed before any agent can be demonstrated.**
> Without a locality predicate the agent has nothing to search, and a model that invents a
> candidate is worse than an error.

### 3.2 B2 — nothing can write the Work Diary programmatically

Exactly one write path exists: `POST /diary/bulk` (`app/diary.py:397`), an HTML form POST. There
is no service layer, no JSON API, no task, no CLI, and no agent code that writes
`WorkDiaryEntry`. A repo-wide scan for `WorkDiaryEntry` returns six production files: the model,
the re-export, the boot schema check, the route, the read engine, and three migrations.

Two consequences for an automated writer:

- **A POST replaces the whole month.** The route loops `for day in range(1, days_in_month + 1)`
  (`app/diary.py:410`) and treats every absent field as blank, which clears that day. An agent
  driving the form must submit the entire month every time.
- **The invariants live in the route, not in a callable unit.** Split-duty promotion
  (`app/diary.py:420-428`), holiday/leave exclusivity (`:430-439`), and post-save compaction
  (`:455-485`) are all inline in `bulk()`. `_save_slot` (`:356-389`) is the closest thing to a
  service function and is still coupled to `request.form`.

One hard modelling constraint worth deciding before building anything: **`premises` on a diary
row is an `Integer` count, not a reference** (`app/models/diary.py:33`). There is no column that
can point at a specific restaurant. The only free-text link is `place_of_visit` (200 chars). So
"inspect this restaurant and log it" can record *that* and *how many*, but cannot record *which*.

### 3.3 B3 — writes are neither audited nor confirmable

`app/audit_hooks.py:159-161` instruments exactly three models:

```python
audited_model_types = (Adjudication, Bill, CaseFile)
```

`WorkDiaryEntry` is not among them. An automated writer's rows leave no `RecordAudit` trace.
There is also no optimistic concurrency on the table, so concurrent writes to the same
`(fso_name, work_date, duty_seq)` are last-commit-wins.

### 3.4 Incidental defects found while tracing

Not required for the feature; recorded because they sit on the same code path.

| Defect | Evidence |
| --- | --- |
| `SUPABASE_DB_URL` is **undeclared config**. Read via a hand-rolled `current_app.config.get(...) or os.environ.get(...)` at `inspection_routes.py:466` — the exact Pattern-A re-implementation `CONTEXT.md:36` forbids. It is absent from both the declaration table (`app/shared/config.py:688-721` declares `ENABLE_SUPABASE_SYNC`, `SUPABASE_URL`, `SUPABASE_API_KEY`, `SUPABASE_SYNC_INTERVAL` only) and `.env.example:518-533`. | `inspection_routes.py:466` vs `config.py:688-721` |
| `source_tables` audit trail is **always empty**. The search block is guarded by `request.method == "GET"` (`:482`) but `source_tables` is computed on the POST branch from `results` (`:527-531`), which is only populated inside that guard. | `inspection_routes.py:482`, `:527-531` |
| **A new `create_engine` per request**, never disposed — the pool at `:480` is built unconditionally before the method check. | `inspection_routes.py:480` |
| `DailyPlan` declares `provider_model` nowhere, though `NoteEvaluation` does (`app/models/notepad.py:82`). A stored plan cannot be attributed to a model. | `app/models/notepad.py:102-110` |

---

## 4. Two ways to build it

The four capabilities map onto OpenCode's documented extension points as follows
(https://opencode.ai/docs/agents/, /custom-tools/, /permissions/, /plugins/):

| Capability | Extension point | Verdict |
| --- | --- | --- |
| NL → intent | **Agent** (`.opencode/agents/*.md`) — `description` + system prompt | Use an agent |
| Premises search | **Custom tool** (`.opencode/tools/*.ts`) | Use a custom tool |
| Schedule suggestion | **Agent prompt** (extract to a Skill only if the rubric grows) | Prompt it |
| Diary write + confirm | **Permission `ask`** + a `tool.execute.before` hook | Use both |

That table is a *composite*. OpenCode owns the tool loop; you supply an agent, two tools, and a
gate. MCP is **not** required — the docs position MCP as a route to *"external tools and
services"* (https://opencode.ai/docs/mcp-servers/), and warn about its context cost. For a
first-party call into the same repo, `.opencode/tools/` is lower friction.

### Option A — in-app agent (recommended)

A service + a route inside the Flask app, mirroring the Daily Plan shape. This is what the
existing architecture argues for: `CONTEXT.md` treats the RAG/auditor agents as *modules inside
the app*, and every AI feature here is gated by a `cfg` row and reached by a route.

Plugs in at the two points the repo already has for this:

- a config row in `_TABLE` (`app/shared/config.py:67-849`) plus a `.env.example` entry — parity is
  enforced by `tests/test_shared_config.py::test_env_example_keys_are_declared`;
- a prompt constant following the `AUDITOR_SYSTEM_PROMPT` convention
  (`app/auditor/service.py:21-33`); prompts are **always** inline module constants in this repo —
  there is no prompts package.

Shape, in the order the repo would demand:

1. **`PremisesResolver`** — a deep module owning locality matching: normalise the free-text
   address, query both tables by `company_name` **and** `full_address`, merge, dedupe, return
   candidates with ids. Fixes B1 and replaces the duplicated ILIKE in the route and the CLI with
   one seam.
2. **`DiaryPlanner`** — a deep module exposing `record(...)` that reuses the existing
   `ACTIVITIES`, `COUNT_ACTIVITIES`, `EXCLUSIVE_ACTIVITIES`, `MAX_COUNT`, `PLACE_MAX_LEN`,
   `_summary_line`, and `print_activity` constants/functions from `app/diary.py`, and applies the
   three split-duty invariants from `app/diary.py:420-485`. Fixes B2 by giving the logic a
   callable home; the route then becomes a thin caller.
3. **The agent** — one LLM call returning a bounded, allowlisted schedule payload, validated by a
   `_validate_*` function in the `app/notepad/routes.py:40-52` style before anything is written.
   Two-phase: propose, then confirm, then write.

Cost of Option A: it lands in the app's review culture (tests, RBAC, config declaration, ADR).
Cost of Option B: none, but it also fixes nothing for the 30 other FSOs.

### Option B — OpenCode-side agent driving the app

`.opencode/agents/` + `.opencode/tools/` + a gate plugin. The **filename becomes the tool name**
and the **filename becomes the agent name**
(https://opencode.ai/docs/custom-tools/#structure, /agents/#markdown).

Two facts from the docs make this safe to propose at all:

- **Permissions default permissive.** *"By default, all tools are enabled and don't need
  permission to run"* (https://opencode.ai/docs/permissions/#defaults), and `most permissions
  default to "allow"`. **A diary-write tool would run with no prompt by default** — `ask` must be
  set explicitly.
- **Hooks can hard-block.** A `tool.execute.before` hook that throws is the documented pattern
  (https://opencode.ai/docs/plugins/#env-protection), so the gate does not depend on the model
  cooperating.

Sketch (frontmatter shape verified against `https://opencode.ai/config.json`):

```markdown
---
description: Plans food-safety inspection runs for a named premises and day.
mode: primary
temperature: 0.2
steps: 20
permission:
  edit: deny
  bash: { "*": deny }
  diary_write: ask
---
```

The realistic Option B shape is a **custom tool that shells the Python** rather than
reimplementing the query in TypeScript — the docs show exactly this for non-TS stacks
(https://opencode.ai/docs/custom-tools/#write-a-tool-in-python), and the search logic already
exists as `scripts/supabase_premises_search.py`. Per `CODING_STANDARDS.md`, that script belongs
in `scripts/`, and `tests/test_repo_root_layout.py` freezes the root inventory.

**Limit of Option B.** It is a *you, personally, driving your own machine* tool. It cannot be
reached by an FSO from the deployed web app, and it puts an LLM in front of a table with no audit
trail (B3) and no optimistic concurrency.

---

## 5. Recommendation

1. **Fix B1 first, and fix it without an agent.** Widen the search to `full_address` behind a
   single `PremisesResolver` seam, and declare `SUPABASE_DB_URL` in the config table. Until a
   locality is resolvable, every downstream piece is untestable and every demo is a hallucination.
2. **Fix B2 second, also without an agent.** Extract `DiaryPlanner.record()` from `app/diary.py`
   and write it as an in-app service. It is useful to a human-facing route long before an agent
   calls it, and it satisfies the repo's own rule that a seam must have a caller.
3. **Then add the agent**, Option A, gated by a new `cfg` row, reusing the Daily Plan's
   propose → validate → confirm → single-commit shape. Add `WorkDiaryEntry` to
   `audited_model_types` at the same time so the writes are traceable.
4. **Treat Option B as a local convenience**, if at all — it is the same logic behind a different
   front door, and it is only safe once B3 has an answer.

### 5.3 The shape to copy

Every AI write path in this repo obeys the same three rules, and the schedule feature should not
be the exception:

1. **The model never selects the DB operation.** The route decides the action; the model fills a
   bounded, allowlisted payload (`app/notepad/routes.py:55-96`).
2. **Nothing is written until a strict validator passes** — including the anti-hallucination
   check that every returned `note_id` was actually offered (`app/notepad/routes.py:40-52`).
3. **One guarded commit, and on any exception, nothing is persisted** (`app/notepad/routes.py:82-94`).

### 5.4 Loose ends already visible in Daily Plan

Worth knowing before the new agent copies the precedent — the precedent is not flawless:

- **Hit-rate feedback does not exist.** `CONTEXT.md:365-366` claims it *"feeds back into future
  plan generation and the feature's kill criterion"*; there is **no matching code in `app/`**.
  The glossary is aspirational. If the schedule feature ships, the same glossary sentence should
  not be written before the code exists.
- **"Top 3–5" is unenforced.** `_validate_plan_payload` checks keys, buckets, and note-id
  membership but never `len(items)`; the bound exists only as prompt text
  (`app/ai_assistant/service.py:218`).
- **Ordering is prompt-only.** No server-side sort implements the least-time-first rule the
  prompt asks for.
- `portfolio_rationale` is requested as markdown but rendered through Jinja auto-escaping into a
  `<div class="prose">` with no markdown filter — literal `**` will appear.

---

## 6. Open questions

1. **Which premises should a schedule hold?** `work_diary.premises` is an `Integer` count. If the
   agent is to track *specific* restaurants across a day, the table needs a real premise
   reference (or the diary needs a sibling table). This is a schema decision, not an agent one.
2. **What is a "day" for planning?** No duration, travel-time, or beat data exists anywhere. Until
   it does, any suggested schedule is an ordered list, not a timed one — worth saying plainly in
   the UI rather than implying timings.
3. **Is locality search in scope for the agent, or should the FSO pick from a list?** The lazy
   option is to widen `PremisesResolver` to `full_address ILIKE` and keep the human in the loop
   via the existing checkbox page (`app/templates/inspection/premises_search.html`). The agent
   then only *reads*. That is materially cheaper and safer to ship first.

---

## 7. Source map

| Claim area | Primary source |
| --- | --- |
| Premises search route | `app/inspection/routes/inspection_routes.py:456-539` |
| Registry schema | `app/models/lookup.py:33-71`, `DEPLOYMENT.md:41-51` |
| Existing CLI search | `scripts/supabase_premises_search.py:13-27` |
| Prior design note | `analysis/premises_search_plan.md` |
| Daily Plan (precedent) | `app/models/notepad.py:91-115`, `app/notepad/routes.py:55-139`, `app/ai_assistant/service.py:200-241` |
| Diary model + only writer | `app/models/diary.py:24-40`, `app/diary.py:397-556` |
| Diary read/report engine | `app/workdiary/engine.py:58-271` |
| RBAC scoping | `app/shared/rbac.py:23-35`, `:103-115` |
| Audit gap | `app/audit_hooks.py:159-161` |
| Config seam | `app/shared/config.py:42-63`, `:67-849` |
| Agent infra (LangGraph, no Celery) | `app/rag/agent/graph.py:508-534` |
| Prompt convention | `app/auditor/service.py:21-33`, `app/rag/generation/structured_reasoner.py:24-36` |
| OpenCode extension points | https://opencode.ai/docs/agents/ · /custom-tools/ · /skills/ · /permissions/ · /plugins/ · /mcp-servers/ |