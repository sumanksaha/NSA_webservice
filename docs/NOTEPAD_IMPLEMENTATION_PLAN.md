# Notepad Implementation Plan

> Converged design from the 2026 grilling session (grill-with-docs). Glossary
> entry for **Note** lives in `CONTEXT.md`. Do not implement until the user
> confirms this plan.

## Purpose

An intake queue where an FSO pastes free-form content — ideas, to-dos,
proposals, or a PDF — and an AI evaluates it into a structured record with
implementation guidance plus game-theoretic, Talebian (antifragility), and
first-principles lenses.

## Domain language

- **Note** — one item in the queue. Pasted text or PDF-extracted text.
  **Shared with all FSOs by default**; author may make it private. Not a
  legal record — no hash-chained audit.
- Never abbreviated "DO" — collides with DO Intimation (Phase 21).
- **NoteEvaluation** — one AI-generated structured verdict on a Note.
  Append-only: re-evaluations add rows, never overwrite.

## Lifecycle

`new → evaluated → implemented | dismissed`

- `implemented` requires a short text field (`implemented_note`) describing
  what was done / where it landed.
- No separate "evaluating" state — evaluation is synchronous.

## Data model

New file `app/models/notepad.py`, re-exported via `app/models/__init__.py`;
one Alembic migration creating both tables.

**`Note`**

| Column                  | Type / notes                                   |
| ----------------------- | ---------------------------------------------- |
| id                      | PK                                             |
| author_id               | FK → User, index                               |
| content_text            | Text (paste or PDF-extracted)                  |
| source_type             | `pasted \| pdf`                                |
| is_shared               | Boolean, **default True**                      |
| status                  | `new \| evaluated \| implemented \| dismissed` |
| implemented_note        | Text, required on implementing                 |
| created_at / updated_at | Timestamps                                     |

**`NoteEvaluation`**

| Column         | Type / notes                                                                                                  |
| -------------- | ------------------------------------------------------------------------------------------------------------- |
| id             | PK                                                                                                            |
| note_id        | FK → Note, index                                                                                              |
| payload        | JSON: `summary · implementation_plan · risks · game_theory · talebian · first_principles · feasibility_score` |
| provider_model | Stamp of AI provider/model used                                                                               |
| created_at     | Timestamp                                                                                                     |

## Blueprint

`app/notepad/`, prefix `/notepad`, registered alphabetically in
`create_app()`.

| Route                 | Behaviour                                                                                                         |
| --------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `GET /`               | List: your notes + everyone's shared ones; status badges; "New note" form (textarea + optional PDF ≤10 MB) at top |
| `POST /new`           | Create; PDF text extracted server-side via existing `app/document_loader/`; binary discarded                      |
| `GET /<id>`           | Detail: content, Evaluate button, evaluations rendered as lens sections/tabs, status controls, privacy toggle     |
| `POST /<id>/evaluate` | Synchronous LLM call → appends a NoteEvaluation                                                                   |
| `POST /<id>/status`   | Status transitions (incl. required `implemented_note`); privacy toggle                                            |

**Access rule:** read = shared notes ∪ own private notes; edit / delete /
status = author only. Global login gate already enforces auth.

## AI integration

Same seam as Phase 11 ai_assistant: provider via `PluginRegistry.get_active()`
(`AI_PROVIDER`). Fixed-JSON output enforced by prompt + light validation (no
strict schema-mode). On failure: error message shown, no evaluation row
written.

Prompt asks for exactly the seven payload fields above; each field holds
markdown prose.

## Config

One cfg-seam row (`app/shared/config.py` declaration table + `.env.example`
entry, per house pattern):

- `NOTEPAD_AI_ENABLED` — opt-out convention (anything but `"false"`); flag
  off → clear "disabled" message on evaluate (kill switch for LLM spend).

## UI

Two Jinja templates (list + detail) extending `base.html`; one nav link in
`base.html`. No dashboard widget. Notes are NOT indexed into FTS5 or the RAG
corpus (private working material, not legal corpus).

## Tests

`tests/test_notepad.py` (~18 tests): CRUD; shared/private scoping across two
users; status flow incl. implemented_note requirement; append-only evaluation
behavior; flag-off behavior; PDF extraction path with mocked loader.

## Workflow lenses applied to the design itself

- **Synchronous before asynchronous** — single-user volume doesn't earn QStash
  plumbing; bolt on `task_status.js` pattern only if latency bites.
- **Append-only evaluations** — Talebian: never burn the evidence of being
  wrong; superseded verdicts stay visible.
- **Kill-switch flag** — optionality over obligation for LLM spend.
- **Shared-by-default** — skin-in-the-game: you paste it, ~20 colleagues see
  it; raises the bar on what's worth pasting.

## Effort estimate

~1 day: model + migration (~1 h), blueprint/routes (~2 h), AI service +
prompt (~1–2 h), templates (~2 h), tests (~2 h).
