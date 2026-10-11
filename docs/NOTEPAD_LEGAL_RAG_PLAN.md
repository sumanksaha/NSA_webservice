# Notepad → Legal RAG Q&A Thread — Implementation Plan

Status: **Draft for review** — derived from the user's requirement statement (2026-10-11) plus a codebase audit of `app/notepad/`, `app/models/notepad.py`, `app/ai_assistant/service.py`, and `app/rag/`.
Target seams: `app/notepad/` (routes + new `thread.py`), `app/models/notepad.py`, `app/notepad/templates/notepad/detail.html`, `tests/test_notepad.py`, `migrations/versions/`.

## 0. Requirement traceability

| # | Requirement | Today | Needed |
|---|---|---|---|
| R1 | Add a problem / query / note | Exists (`POST /notepad/new`, paste + PDF) | Nothing |
| R2 | Press an AI button → get an explanation/answer | Exists but calls `AIAssistantService.evaluate_note()` — a **generic LLM**, no retrieval | Point the button at the legal RAG pipeline |
| R3 | Answer must come from the **legal RAG module**, not generic AI | Missing | Swap the service call to `run_generation_pipeline` (circuit-breaker wrapped), persist answer + citations + verification signals |
| R4 | Update the statement, add further questions, fragment the query → RAG updates the answer | Missing entirely: no note edit route, no thread model, no follow-up | Add note editing, an append-only Answer thread, deterministic conversation-aware query rewriting, and user-driven query fragmentation |

## 1. Evaluation of the current feature

**Current flow.** `Note` (`app/models/notepad.py`) holds `content_text`; the detail page's *Evaluate with AI* button POSTs `/<id>/evaluate` (`app/notepad/routes.py:232`), which calls `AIAssistantService.evaluate_note()` (`app/ai_assistant/service.py:153`) and appends a `NoteEvaluation` row holding a 7-key lens payload (`summary`, `implementation_plan`, `risks`, `game_theory`, `talebian`, `first_principles`, `feasibility_score`). Daily Plan generation (`plan_open_notes`) is generic AI too.

**Why this fails R2/R3 for legal questions:**

- The lens payload is an *idea-evaluation* schema (product-idea lenses for internal feature proposals). It has no place to put a legal answer, statutory citations, or a groundedness verdict — the shape itself is wrong for R3.
- `evaluate_note` is an ungrounded LLM call: no Qdrant retrieval, no citation validation, no hallucination check, no `rag_query_log` join-back. An FSO cannot distinguish "the law says" from "the model opines".
- Kill switch `NOTEPAD_AI_ENABLED` exists, but nothing gates on `RAG_ENABLED`.

**What the RAG module already provides** (`app/rag/`): `run_generation_pipeline(query, top_k, collection_name, filters)` (`app/rag/tasks.py:622`) runs retrieve → grounded generate → claim verification + citation validation → log, returning `answer`, `citations` (`chunk_id`, `section_number`, `document_title`, `document_type`, `authority`, `url` — `app/rag/retrieval/result.py:108`), `groundedness_score`, `hallucination_detected`, `llm_model`, `token_usage`, `log_id`, and (when internal decomposition ran) `sub_queries`. `ResilientRAGPipeline` (`app/rag/resilient.py:43`) wraps it with a circuit breaker + stub fallback, and the Flask endpoint `POST /api/rag/query` (`app/rag/routes.py:360`) is the reference for how another surface should call it.

**Gap for R4:** no `POST` route edits `Note.content_text`; there is no conversation model; every evaluation is independent and stateless.

## 2. Design decisions

Decisions are listed with rationale; the four that gate a phase are repeated in §12.

- **D1 — New append-only model `NoteAnswer` ("Answer"), do not overload `NoteEvaluation`.** The lens payload and a grounded-answer payload are different shapes; `tests/test_notepad.py` pins the 7 lens keys; append-only history must keep its meaning. A Note gains a **thread**: ordered `(question → answer)` turns. The existing lens evaluation and Daily Plan stay untouched (still `NOTEPAD_AI_ENABLED`-gated) and get demoted in the UI to a secondary action.
- **D2 — Notepad owns conversation state; RAG stays single-turn.** The pipeline takes one query string. Multi-turn is achieved by **deterministic query rewriting** in a pure module (`app/notepad/thread.py::build_standalone_query`): statement + last N turns + the new question, concatenated under labelled headings. No LLM condense step — it would add spend, nondeterminism, and re-couple the flow to generic AI (the exact thing R3 forbids).
- **D3 — "Fragment the query" is user-driven** (R4 says the *user* fragments). The ask-box accepts several fragments (one question per line). Each fragment becomes its own Answer turn with its own RAG call and its own citations. The pipeline's *internal* decomposition (`sub_queries`) is surfaced read-only on the answer card — it is informational, not the user's fragmentation.
- **D4 — "Update the answer" means a new append-only turn, never an overwrite.** Matches the repo's established append-only pattern (`NoteEvaluation`, `DailyPlan`, version control). Each Answer stores the exact `question` and a `note_snapshot` of the statement at ask time, so history stays interpretable after edits. The UI renders the **latest** turn as the main answer card; older turns collapse under `<details>`.
- **D5 — Synchronous execution for V1.** Matches the existing `/api/rag/query` and current `evaluate` UX. RAG calls take seconds-to-tens-of-seconds, so the submit button must disable with a visible progress hint. Async (QStash/Celery) is a non-goal (§11).
- **D6 — Fail-closed persistence.** `cfg.rag_enabled` off → `503`. `cfg.notepad_ai_enabled` off → `503`. Pipeline exception → flash danger, **no Answer row** (mirrors `evaluate`). A circuit-open *degraded* answer (pipeline falls back to stub generation) is persisted with `degraded=True` and rendered with a "retrieval unavailable" badge — never silently mixed with grounded answers.
- **D7 — Permissions unchanged.** Asking/editing is author-only; shared notes are readable by all FSOs; private notes 404 for non-authors (same guards as `evaluate`). Answers inherit visibility through the note.
- **D8 — Join-back.** Store the pipeline's `log_id` on the Answer so a human rating can join to the exact logged run (same contract as `POST /api/rag/feedback`).
- **D9 — Kill switch: reuse `NOTEPAD_AI_ENABLED`; add no new config flag.** It is already declared in `app/shared/config.py` (`notepad_ai_enabled`), mirrored in `.env.example`, and covered by the docs-parity meta-test. A second flag (`NOTEPAD_RAG_ENABLED`) would put two kill switches on one feature. The RAG path is additionally gated by `cfg.rag_enabled`.
- **D10 — Test seam mirrors the RAG blueprint.** `thread.get_pipeline()` lazily builds a `ResilientRAGPipeline` whose `pipeline_fn` resolves `run_generation_pipeline` **at call time** (late binding), exactly like `app/rag/routes.py::_get_query_breaker` (lines 59–77). Tests monkeypatch the seam — no network.

## 3. Architecture

```
detail page (/notepad/<id>)
  ├─ Statement card ── Edit statement ── POST /notepad/<id>/edit        (R4a)
  └─ Ask Legal RAG card
        textarea (one question, or several fragments — one per line)
        POST /notepad/<id>/ask                                          (R2/R3/R4bc)
          │
          ▼
 app/notepad/thread.py  (pure functions + orchestration)
   split_fragments(text)            → ["q1", "q2", ...]        (D3)
   build_standalone_query(...)      → one query string         (D2)
   ask(note, question)              → [NoteAnswer, ...]
          │  per fragment:
          ▼
 ResilientRAGPipeline (app/rag/resilient.py) ── late-bound
   run_generation_pipeline (app/rag/tasks.py:622)
          │
          ▼
 NoteAnswer row(s) appended (append-only) → redirect → detail
   renders latest turn as the answer card, older turns collapsed   (D4)
```

File seam:

```
app/models/notepad.py            + NoteAnswer model, Note.answers relationship
app/notepad/thread.py            NEW — split_fragments, build_standalone_query,
                                       ask(), get_pipeline()  (the deep module)
app/notepad/routes.py            + ask, edit routes; detail() passes the thread
app/notepad/templates/notepad/detail.html   reworked (statement edit, ask box, thread)
migrations/versions/add_note_answer_table.py  NEW
tests/test_notepad.py            + TestAsk / TestEdit / TestThread unit tests
docs/CONTEXT.md                  Note entry amended + new "Note Answer" entry
```

## 4. Data model and migration

```python
class NoteAnswer(db.Model):
    """One append-only grounded RAG answer to one question in a Note's thread."""

    __tablename__ = "note_answer"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    note_id = db.Column(db.Integer, db.ForeignKey("note.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)        # exact question asked
    answer = db.Column(db.Text, nullable=False)
    citations = db.Column(db.Text, nullable=True)        # JSON: pipeline citation dicts
    note_snapshot = db.Column(db.Text, nullable=True)    # statement at ask time (D4)
    groundedness_score = db.Column(db.Float, nullable=True)
    hallucination_detected = db.Column(db.Boolean, nullable=True)
    degraded = db.Column(db.Boolean, nullable=False, default=False)  # circuit-open stub (D6)
    provider_model = db.Column(db.String(200), nullable=True)
    token_usage = db.Column(db.Integer, nullable=True)
    rag_log_id = db.Column(db.String(64), nullable=True)  # join-back to rag_query_log (D8)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
```

`Note.answers` — relationship, `order_by="NoteAnswer.created_at"`, `cascade="all, delete-orphan"`, `lazy="selectin"` (mirrors `Note.evaluations`). Index `idx_note_answer_note_id`.

Migration `add_note_answer_table` follows the `add_notepad_daily_plan.py` template (docstring with Revision ID / Revises / Create Date, `op.create_table`, `op.create_index`, matching `downgrade`). **Before writing it, run `flask db heads`** — the versions tree currently reports multiple heads, so chain off the true single head (create a merge revision first if more than one), not blindly off `add_notepad_daily_plan`. Dev environments can also rely on `db.create_all()` (as the existing migration docstrings note).

## 5. `app/notepad/thread.py` contract

Pure, importable without Flask app context (except the lazy pipeline), so it unit-tests cheaply:

```python
_MAX_PRIOR_TURNS = 5        # deterministic cap on rewrite context (D2)
_ANSWER_PREVIEW_CHARS = 600 # per prior answer in the rewritten query
_MAX_FRAGMENTS = 10         # bound spend per ask

def split_fragments(text: str) -> list[str]:
    """One fragment per non-empty line; blank-separated groups collapse.
    Returns [] for blank input, a 1-element list for a single question."""

def build_standalone_query(note_text: str, prior_turns: list[tuple[str, str]], question: str) -> str:
    """Deterministic rewrite: labelled sections for the statement, the last
    _MAX_PRIOR_TURNS (question + truncated answer), and the new question.
    No LLM involved (D2)."""

def get_pipeline() -> ResilientRAGPipeline:
    """Lazy singleton; pipeline_fn resolves run_generation_pipeline at call
    time so tests keep monkeypatching it (D10)."""

def ask(note, question: str) -> list[NoteAnswer]:
    """Split → per fragment: build query → run pipeline → map result to a
    NoteAnswer. Raises on pipeline failure (route flashes, persists nothing —
    D6). Caller commits."""
```

Result→row mapping (all `result.get`, never `result[...]`): `answer`, `citations=json.dumps(result.get("citations") or [])`, `groundedness_score`, `hallucination_detected`, `degraded=bool((result.get("debug") or {}).get("degraded_mode"))`, `provider_model=llm_model`, `token_usage`, `rag_log_id=log_id`, plus `question` and `note_snapshot=note.content_text`.

## 6. Routes (additions to `app/notepad/routes.py`)

```
POST /notepad/<int:note_id>/ask     notepad.ask
POST /notepad/<int:note_id>/edit    notepad.edit
```

Both: `Note.query.get_or_404`, the private-note 404 guard, then author-only. `ask` additionally checks `cfg.notepad_ai_enabled` and `cfg.rag_enabled` (503 with `description=`, matching `evaluate`), strips/validates the question (empty → flash warning, redirect), calls `thread.ask(note, question)`, commits, flashes `"Answer(s) added — N fragment(s)."`. On exception: flash danger, redirect, **no commit**. `edit` validates non-empty `content_text`, assigns, commits (append-only answers are never touched). `detail()` passes `answers` (parsed payloads not needed — the row is the shape) and the stale marker (`note.updated_at > latest_answer.created_at` → "statement edited after this answer — ask again to refresh").

Note lifecycle status is **not** changed by `ask` (status tracks the idea lifecycle: `new → evaluated → implemented | dismissed`); the thread carries its own freshness.

## 7. UI (`notepad/detail.html`, Jinja only — no new JS framework)

1. **Statement card** — content + (author-only) *Edit statement* form (textarea + Save) posting to `notepad.edit`.
2. **Ask Legal RAG card** (primary button, author-only) — textarea with hint "one question, or split into fragments — one per line"; submit **Get legal answer**; `onsubmit` disables the button (existing pattern on the evaluate form). Optional helper: a small "Split fragments" button that inserts newlines (pure JS, optional).
3. **Answer card (latest turn)** — answer text; citation list (section number, document title, authority, url); verification strip: groundedness %, hallucination warning (red) when flagged, degraded badge (amber) when `degraded`; meta line: model, tokens, timestamp; stale marker when the statement was edited after the answer.
4. **History** — older turns inside `<details>`, oldest first (same convention as the evaluations list).
5. The existing *Evaluate with AI* button becomes a secondary outline button labelled **Evaluate idea (7 lenses)** — same route, same payload, unchanged behaviour (D1).

## 8. Config and docs surface

- No new flag (D9). `.env.example` unchanged. `cfg.rag_enabled` + `cfg.notepad_ai_enabled` gate the path.
- Docs: this file + a `docs/INDEX.md` entry (Improvement Plans); `CONTEXT.md` gains a **Note Answer** glossary entry and the **Note** entry is amended (thread + RAG-backed answers); no ADR needed (no architectural surprise — this wires an existing module into an existing feature; if the lens-evaluation removal of §12-D1 is taken later, record it as an ADR at that point).

## 9. Tests (`tests/test_notepad.py`, plus new `tests/test_notepad_thread.py` for the pure functions)

- `TestAsk`: flags off → 503, no row; `rag_enabled=False` → 503; success → row with all mapped fields, `rag_log_id` stored, status unchanged; two fragments → two rows in order with the exact question text; pipeline raises → graceful redirect, **zero** rows (fail-closed); degraded result → persisted with `degraded=True`.
- `TestAskPermissions`: private note, non-author → 404; shared note readable by all; edit is author-only (403 for others); edit updates `content_text` and leaves answers untouched.
- `TestThread` (no app context): `split_fragments` (single / multi-line / blank / >`_MAX_FRAGMENTS`); `build_standalone_query` includes statement + last turns + new question and truncates at `_MAX_PRIOR_TURNS`.
- Existing lens-evaluation tests must pass **unchanged** (proves D1's non-interference).

## 10. Risks and mitigations

| Risk | Mitigation |
|---|---|
| RAG latency (10s of seconds) blocks the request thread | V1 sync with disabled button + hint (D5); async is a tracked non-goal |
| Prompt growth over a long thread | `_MAX_PRIOR_TURNS` cap + per-answer truncation (deterministic, tested) |
| Degraded (circuit-open) answers mistaken for grounded | `degraded` flag + amber badge; never styled like a grounded answer (D6) |
| Spend amplification from fragments | `_MAX_FRAGMENTS` cap; reuses the existing `NOTEPAD_AI_ENABLED` kill switch |
| Two AI buttons confuse users | Lens evaluate demoted to a secondary labelled action (§7.5) |
| Stale answers after an edit | Snapshot per turn + stale marker on the latest card (§6) |

## 11. Non-goals (V1)

Async/streaming execution; LLM-based query condensation; deleting or editing Answers; migrating historical `NoteEvaluation` rows; changing the Daily Plan feature; any change inside `app/rag/` itself (the pipeline is consumed, not modified); making a Note a hash-chained legal record (it stays explicitly non-legal).

## 12. Phased delivery and gating decisions

**Phase 1 — model & seam. ✅ Done (2026-10-11).** `NoteAnswer` + relationship; migration (`add_note_answer_table`, sole head); `thread.py` with `get_pipeline`. Unit tests for the pure functions. *Done when:* migration applies on a fresh dev DB and `TestThread` passes.

**Phase 2 — routes. ✅ Done (2026-10-11).** `ask` + `edit`; `detail()` passes the thread (`answers=note.answers`); guards and fail-closed paths. Tests: `TestAskRoute` (9) + `TestEditRoute` (5) in `tests/test_notepad.py`; the old tests still pass.

**Phase 3 — UI.** Detail page per §7. *Done when:* manual pass — create note → ask → follow-up → edit statement → stale marker → re-ask; fragment ask produces N answer cards.

**Phase 4 — docs & hygiene.** `CONTEXT.md` entries, `docs/INDEX.md` entry, ruff/pytest/pre-commit clean. *Done when:* `tests/test_steering_docs.py` passes with the new INDEX link.

Decisions needing confirmation before Phase 2 (affects UI copy and scope only, not Phase 1):

1. **Lens evaluation** — keep as demoted secondary action (recommended) or remove outright?
2. **Kill switch** — reuse `NOTEPAD_AI_ENABLED` (recommended, D9) or add a separate `NOTEPAD_RAG_ENABLED`?
3. **Statement edit** — free replace of `content_text` (recommended) or keep versions?
4. **Sync vs async** — synchronous V1 (recommended) or queue from day one?
