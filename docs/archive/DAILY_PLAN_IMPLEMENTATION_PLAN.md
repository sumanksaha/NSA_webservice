# Notepad Daily Plan — Implementation Plan

> Converged design from the 2026 grilling session (grill-with-docs) extending
> `docs/NOTEPAD_IMPLEMENTATION_PLAN.md`. No code written yet; do not implement
> until the user confirms this plan. Glossary entry for **Daily Plan** lives in
> `CONTEXT.md`.

## Purpose

A one-click **Daily Plan**: the AI reads an FSO's open notes and returns a
short battle plan — the top 3–5 notes worth doing today, explicitly excluding
the rest (_via negativa_ — subtraction beats a ranked list of thirty). The
sequencing principle is **Shortest Processing Time first** (Smith's rule:
provably optimal for minimizing mean completion time across a batch), tie-
broken by portfolio-level lens scores.

## Settled decisions (grilling rounds 1–2)

| #   | Decision           | Choice                                                                                                                                                                                         |
| --- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Artifact shape     | Top 3–5 daily plan + full ranking as secondary output. Queue order itself unchanged; no `sort_rank` persistence, no drag-reorder UI.                                                           |
| 2   | Scope              | **Notepad Notes only** — not FBO issues / inspections / adjudications (those already have deadline semantics).                                                                                 |
| 3   | Lens attachment    | Lenses evaluate the **portfolio** (sequencing logic), not each note in isolation. Game theory/optionality/antifragility are properties of sequences.                                           |
| 4   | Persistence & HITL | Append-only persisted plans; no apply/reorder endpoints. Past-vs-implemented history is free evidence for the kill criterion.                                                                  |
| 5   | Ordering metric    | **Least-time-first**: AI estimates effort per note; order = effort ascending (SPT), tie-break by lens score.                                                                                   |
| 6   | Plan ownership     | Plans cover **own notes only** (`author_id` = current user). Preserves the author-only status-transition access rule; shared notes stay read-only inspiration.                                 |
| 7   | Effort granularity | Discrete buckets per item: `quick` (<30m) · `medium` (<half-day) · `long` (>half-day). No minute-level point estimates (fake accuracy).                                                        |
| 8   | Persistence shape  | New **`DailyPlan`** table — a plan spans many notes, so NoteEvaluation (`note_id` single-note) is the wrong home.                                                                              |
| 9   | Routes             | `GET /notepad/plan` (latest plan rendered) + `POST /notepad/plan/generate` (synchronous AI call). Regenerating appends a new row, never edits.                                                 |
| 10  | Feedback loop      | Item status badges pulled live from the Note at render time + prior-plan completion stats ("you implemented 2/5 last time") fed into the generation prompt as one line of calibration context. |
| 11  | Kill switch        | Reuses existing `NOTEPAD_AI_ENABLED` flag (off → clear disabled message, no row written).                                                                                                      |
| 12  | Kill criterion     | If the feature doesn't move median time-to-implemented (`created_at` → status transition) within ~4 weeks, delete it. Both values already exist — zero instrumentation needed.                 |

## Data model

New file additions to `app/models/notepad.py`; one Alembic migration adding
`daily_plans`. Re-exported via `app/models/__init__.py`.

**`DailyPlan`**

| Column     | Type / notes                                                                                                    |
| ---------- | --------------------------------------------------------------------------------------------------------------- |
| id         | PK                                                                                                              |
| author_id  | FK → User, index                                                                                                |
| payload    | JSON: `{items: [{note_id, effort_bucket, why}], ranking: [{note_id, effort_bucket, why}], portfolio_rationale}` |
| created_at | Timestamp                                                                                                       |

- Append-only like `NoteEvaluation`: regeneration inserts a new row.
- `items` = the top 3–5 battle plan; `ranking` = full ordered list of open
  own-notes (secondary output); `portfolio_rationale` = markdown prose from
  the three lenses applied to the sequence.
- No updated_at — rows are immutable.

## Blueprint additions (`app/notepad/routes.py`, prefix `/notepad`)

| Route                 | Behaviour                                                                                                                                           |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /plan`           | Latest plan for current user; items render with **live** Note status badges; link each item to its note detail page.                                |
| `POST /plan/generate` | Synchronous AI call over the user's open notes (any non-dismissed/non-implemented status); writes exactly one `DailyPlan` row; redirect to `/plan`. |

- Flag off (`NOTEPAD_AI_ENABLED=false`) → clear "AI disabled" message, no row.
- AI failure → error message, no row (same contract as `/evaluate`).
- Access rule unchanged: everyone only ever sees/generates their own plan.

## AI integration

Same seam as `/evaluate`: provider via `PluginRegistry.get_active("ai")`,
fixed-JSON output enforced by prompt + light validation.

Prompt requirements (v1):

1. Input: all of the user's open notes (id, content, source_type).
2. Estimate each note's effort bucket (`quick|medium|long`).
3. Order by bucket ascending (SPT); within a bucket, rank by portfolio lens
   scores.
4. Select the top 3–5 as today's plan; everything else goes to `ranking`.
5. Apply the three lenses to the _sequence_, e.g.:
    - **First principles** — which item unblocks or makes others trivial?
    - **Talebian** — prefer the reversible experiment first; never burn the
      option to learn; what should be _ignored_ today?
    - **Game theory** — which quick win buys cooperation/political capital for
      the hard ones?
6. Calibration line injected from the previous plan: implemented x/y last time.
7. Output JSON exactly: `{items, ranking, portfolio_rationale}`.

## UI

- One button on the notepad list page: "Plan my day".
- `notepad/plan.html`: numbered checklist (note title snippet, effort-bucket
  badge, `why` line, live status badge, link to note), then collapsible full
  ranking, then `portfolio_rationale` prose. Extends `base.html`.

## Tests (~8, added to `tests/test_notepad.py` or sibling file)

1. Generate creates exactly one `DailyPlan` row; regenerate appends (never updates).
2. Flag-off → no row, message shown.
3. AI failure → no row, error shown (mocked plugin raising).
4. Plan contains only the requesting user's notes (second user's shared note absent).
5. Payload validation: buckets limited to the three values; items ⊆ open notes.
6. GET /plan renders latest plan with live status badges (implemented note shows badge).
7. Unauthenticated → login gate (global, but assert route exists behind it).
8. Migration up/down round-trip.

## Workflow lenses applied to this feature's own design

- **Via negativa** — the product is mostly a _do-not-do_ list; exclusion is the value.
- **Append-only** — past plans are evidence, never overwritten; enables the kill criterion.
- **Kill-switch reuse** — no new spend surface without an off-ramp.
- **Own-notes-only** — keeps skin-in-the-game intact; no permission-model drift.
- **Buckets over minutes** — robust estimates over fragile precision.

## Effort estimate

~½ day: model + migration (~45 m), two routes (~1 h), prompt + payload
validation (~1 h), template (~1 h), tests (~1 h).

## Open items for implementation session

- None blocking. Implementer should re-read `docs/NOTEPAD_IMPLEMENTATION_PLAN.md`
  for the base blueprint conventions before starting.
