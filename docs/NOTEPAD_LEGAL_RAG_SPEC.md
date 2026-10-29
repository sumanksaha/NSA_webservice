# Specification: Notepad Legal-RAG Q&A Thread

**Status:** specification only — no code, tests, or config changed.
**Date:** 2026-10-11.
**Implements:** [NOTEPAD_LEGAL_RAG_PLAN.md](NOTEPAD_LEGAL_RAG_PLAN.md) (evaluation, design decisions D1–D10, phases, file list).
**User requirement (verbatim intent, 2026-10-11):** *add a problem or query or note → press an AI button → get an explanation or answer → the answer is produced by the legal RAG module, not generic AI → afterwards update the statement, add further questions, fragment the query, and the RAG updates the answer.*

**How to read this document.** Sections 3–6 are **normative**: `MUST`, `MUST NOT`, `SHOULD`, `MAY` carry their usual force. Section 7 onward is explanatory. Every requirement has an ID (`FR-n`, `NFR-n`, `INV-n`, `V-n`, `SEC-n`) and appears in the traceability matrix (§12). Line citations are to the current working tree.

---

## 1. Problem statement

The Notepad (`app/notepad/`, `/notepad`) is an intake queue of free-form `Note`s. Its AI button calls `AIAssistantService.evaluate_note()` (`app/ai_assistant/service.py:153`) — an ungrounded, single-turn generic LLM call whose 7-key lens payload (`summary`, `implementation_plan`, `risks`, `game_theory`, `talebian`, `first_principles`, `feasibility_score`) has no place for a legal answer, statutory citations, or a groundedness verdict. An FSO cannot tell "the FSS Act says" from "the model opines".

The system must instead turn a Note into a **legal Q&A thread**: every question asked against the note is answered by the grounded **legal RAG module** (`app/rag/`), with citations and verification signals persisted per turn; the statement can be edited, follow-up questions can be asked, a compound question can be fragmented into focused sub-questions by the user, and each of those actions produces a **new append-only answer turn** rather than mutating history.

## 2. Scope and specified decisions

| ID | Decision | Specified as | Flip cost |
| --- | --- | --- | --- |
| **D1** | Ask is multi-turn — new append-only `NoteAnswer` model, or reuse `NoteEvaluation`? | **New model `NoteAnswer`.** The lens payload and a grounded-answer payload are different shapes; `tests/test_notepad.py` pins the 7 lens keys; `NoteEvaluation`/`DailyPlan` history must keep its meaning. `evaluate` and Daily Plan stay untouched. | None after Phase 1 |
| **D2** | Kill switch for the new path | **Reuse `notepad_ai_enabled`** (`app/shared/config.py:791`, `NOTEPAD_AI_ENABLED`, opt-out). **No new config row** — it is already declared, mirrored in `.env.example`, and covered by the docs-parity meta-test. The path is additionally gated by `cfg.rag_enabled` (`app/shared/config.py:71`). | One `Setting` row if split later |
| **D3** | Execution model | **Synchronous V1** — one request per ask, matching `POST /api/rag/query` (`app/rag/routes.py:360`) and the existing `evaluate` flow. No Celery (removed 2026-09-13; queue is QStash — `app/utils/qstash_client.py:25-37`). | Route + template rewrite if async |
| **D4** | Old lens evaluation button | **Keep, demoted** to a secondary labelled action ("Evaluate idea (7 lenses)") on the detail page; same route, same payload, unchanged behaviour. Removal is a later product decision and **MUST** be recorded as an ADR when taken. | One template block |

### 2.1 Explicitly out of scope

Async/streaming execution; LLM-based query condensation (D5 below forbids it); editing or deleting Answer turns; migrating historical `NoteEvaluation` rows; changes to the Daily Plan feature; **any change inside `app/rag/`** — the pipeline is consumed, not modified; making a Note a hash-chained legal record (it stays explicitly non-legal, `app/models/notepad.py:7`).

---

## 3. Functional requirements

### 3.1 Gating and configuration

| ID | Requirement |
| --- | --- |
| **FR-1** | The notepad **MUST NOT** add a configuration flag. `post`/`ask` gating reads `cfg.notepad_ai_enabled` and `cfg.rag_enabled` only. Precedent for the 503 shape: `app/notepad/routes.py:237-238`. |
| **FR-2** | Neither route **MUST NOT** hand-roll a `current_app.config.get(...) or os.environ.get(...)` resolver; both **MUST** read through the `cfg` accessor (`CONTEXT.md` forbids re-implementing Pattern A). |
| **FR-3** | When `cfg.rag_enabled` is false, `ask` **MUST** return `503` with `description="Notepad Legal RAG is disabled."` — one description string, shared by both gates, chosen by the route. |
| **FR-4** | Retrieval depth **MUST** be `cfg.context_max_chunks` (`app/shared/config.py:676`), resolved at call time inside the seam — never captured at import. Precedent: `app/rag/routes.py:73-74`. |

### 3.2 Thread module (`app/notepad/thread.py`, new)

| ID | Requirement |
| --- | --- |
| **FR-5** | A new module `app/notepad/thread.py` **MUST** expose the interface in §5.2. It is the single owner of query rewriting, fragmentation, and Answer construction. Routes stay thin. |
| **FR-6** | `build_standalone_query` **MUST** be deterministic string assembly. An LLM **MUST NOT** participate in rewriting — that would re-couple the answer path to generic AI, which the requirement forbids. |
| **FR-7** | `get_pipeline()` **MUST** return a lazily-constructed `ResilientRAGPipeline` (`app/rag/resilient.py:43`) whose `pipeline_fn` resolves `run_generation_pipeline` **at call time** (late binding), mirroring `app/rag/routes.py:59-77`. Tests monkeypatch this seam; no test may reach the network. |
| **FR-8** | `ask(note, question)` **MUST** raise on pipeline failure, and the route **MUST** persist nothing on failure. Precedent: `app/notepad/routes.py:249-253`. |

### 3.3 Ask route

| ID | Requirement |
| --- | --- |
| **FR-9** | `POST /notepad/<int:note_id>/ask` **MUST** exist, render-redirect on success to `notepad.detail`, and append one `NoteAnswer` per fragment (§5.3). |
| **FR-10** | `ask` **MUST** persist the exact `question` string that produced each answer and a `note_snapshot` of `note.content_text` taken at ask time (INV-3). |
| **FR-11** | `ask` **MUST NOT** change `note.status`. Status tracks the idea lifecycle (`new → evaluated → implemented | dismissed`, `app/models/notepad.py:37`); the thread carries its own freshness (INV-5). |
| **FR-12** | When the pipeline result contains `sub_queries` (internal compound-query decomposition, `app/rag/tasks.py:766`), the detail page **MUST** surface them on the answer card as informational text, clearly distinct from the user's own fragments. |
| **FR-13** | `ask` **MUST** be author-only (`abort(403)`), tightening `evaluate`'s shared-note allowance (`app/notepad/routes.py:241-242`) to match the UI reality (`detail.html:20`) and the author-only precedent of `set_status`/`set_visibility` (`:279-280`, `:305-306`). |

### 3.4 Edit-statement route

| ID | Requirement |
| --- | --- |
| **FR-14** | `POST /notepad/<int:note_id>/edit` **MUST** exist, be author-only (`abort(403)`), accept `content_text` from the form, strip it, reject blank with a warning flash (`app/notepad/routes.py:201-203` precedent), assign, commit, redirect. |
| **FR-15** | `edit` **MUST NOT** touch any `NoteAnswer` row. History is append-only (INV-1); provenance is the snapshot (INV-3). |
| **FR-16** | `edit` **MUST NOT** expose any other column. `source_type`, `is_shared`, `status` keep their existing routes (`set_visibility`, `set_status`). |

### 3.5 Detail page

| ID | Requirement |
| --- | --- |
| **FR-17** | `detail()` **MUST** pass the note, its answers (newest-last for history rendering), the stale flag (§5.4), and the kill-switch availability (`notepad_ai_enabled and rag_enabled` — the ask card is hidden, not 503-bait, when off) to `notepad/detail.html`. Per-turn `citations`/`sub_queries` JSON is parsed in the route (precedent: the `evaluations` payload), so the template stays parse-free. |
| **FR-18** | The latest answer **MUST** render as the primary answer card; older answers **MUST** render inside a collapsed `<details>` block, oldest first — the same "(oldest first)" convention the evaluations list already documents (`detail.html:61`). |
| **FR-19** | The answer card **MUST** render, per turn: the question, the answer, the citations, the verification strip (groundedness, hallucination flag, degraded badge), and the meta line (model, tokens, timestamp). |
| **FR-20** | The primary author-only action **MUST** be the ask form, labelled **Ask Legal RAG** / submit **Get legal answer**; the lens evaluation **MUST** be a secondary outline button labelled **Evaluate idea (7 lenses)** (D4). |

---

## 4. Non-functional requirements

| ID | Requirement |
| --- | --- |
| **NFR-1** | No test in the fast shard **MAY** reach the network. `get_pipeline()` is the injection seam; tests stub at it. Existing seam precedent: `_ai_service` (`app/notepad/routes.py:28-32`). |
| **NFR-2** | Application modules **MUST NOT** `print` (`CODING_STANDARDS.md`). Routes log; failures reach the user via `flash` categories `warning` / `danger` / `success` as today. |
| **NFR-3** | No root-level `*.py` file may be added (`tests/test_repo_root_layout.py`). One-off scripts belong in `scripts/`. |
| **NFR-4** | Prompt text **MUST** be an inline module constant or a call-time string; there is no prompts package. Convention: `_SUMMARIZE_PROMPT` (`app/ai_assistant/service.py:39`). |
| **NFR-5** | The RAG call is one synchronous request through the existing retry + circuit-breaker stack (`app/rag/resilient.py`, `app/ai_assistant/service.py:352-378`). This feature adds no retry of its own. |
| **NFR-6** | `thread.py` **MUST** be importable and fully testable without a Flask app context; only `get_pipeline()` touches app context, and only at call time. |
| **NFR-7** | Every Answer **MUST** be attributable to the pipeline run that produced it via `rag_log_id` (INV-4), the same join-back contract as `POST /api/rag/feedback` (`app/rag/routes.py:453`). |

---

## 5. Data contracts

### 5.1 `NoteAnswer` (new model, `app/models/notepad.py`)

```python
class NoteAnswer(db.Model):
    """One append-only grounded RAG answer to one question in a Note's thread."""

    __tablename__ = "note_answer"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    note_id = db.Column(db.Integer, db.ForeignKey("note.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)        # exact question asked (post-split fragment)
    answer = db.Column(db.Text, nullable=False)
    citations = db.Column(db.Text, nullable=True)        # JSON list of pipeline citation dicts
    sub_queries = db.Column(db.Text, nullable=True)      # JSON list; internal decomposition (FR-12)
    note_snapshot = db.Column(db.Text, nullable=True)    # statement at ask time (INV-3)
    groundedness_score = db.Column(db.Float, nullable=True)
    hallucination_detected = db.Column(db.Boolean, nullable=True)
    degraded = db.Column(db.Boolean, nullable=False, default=False)  # circuit-open stub (INV-6)
    provider_model = db.Column(db.String(200), nullable=True)
    token_usage = db.Column(db.Integer, nullable=True)
    rag_log_id = db.Column(db.String(64), nullable=True)  # rag_query_log.id (INV-4)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(UTC))
```

`Note.answers` — relationship, `order_by="NoteAnswer.created_at"`, `cascade="all, delete-orphan"`, `lazy="selectin"` (mirrors `Note.evaluations`, `app/models/notepad.py:48-53`). Index `idx_note_answer_note_id` on `(note_id)`. Migration `add_note_answer_table` follows the `add_notepad_daily_plan.py` template; **`flask db heads` MUST be run first** — the versions tree currently reports multiple heads, so the revision chains off the true head (merge revision first if more than one), and the docstring records the resolved parent.

### 5.2 `thread.py` interface

```python
_MAX_PRIOR_TURNS = 5         # deterministic cap on rewrite context (INV-7)
_ANSWER_PREVIEW_CHARS = 600  # per prior answer inside the rewritten query (INV-7)
_MAX_FRAGMENTS = 10          # spend bound per ask (V-4)

def split_fragments(text: str) -> list[str]:
    """One fragment per non-empty line; `[]` for blank input."""

def build_standalone_query(
    note_text: str, prior_turns: list[tuple[str, str]], question: str
) -> str:
    """Deterministic rewrite — labelled sections, no LLM (FR-6)."""

def get_pipeline() -> ResilientRAGPipeline:
    """Lazy singleton; late-bound pipeline_fn (FR-7, NFR-6)."""

def validate_question(question: str) -> list[str]:
    """V-1…V-4 gate; returns the fragments. The route calls this before
    the kill-switch gates so input errors and a disabled feature report
    distinctly (§8); `ask` reuses it, keeping FR-5's single ownership."""

def ask(note, question: str) -> list[NoteAnswer]:
    """Validate → per fragment build+run → map to NoteAnswer. Raises on failure (FR-8)."""
```

`prior_turns` is a list of `(question, answer)` in chronological order, capped to the last `_MAX_PRIOR_TURNS`. The rewritten query **MUST** carry three labelled sections — the statement, the prior exchanges (question + answer truncated to `_ANSWER_PREVIEW_CHARS`), and the new question — and **MUST** end with an instruction to answer the new question grounded in the retrieved law with section citations.

### 5.3 Fragmentation rules (normative)

| # | Rule |
| --- | --- |
| **F1** | `split_fragments` splits on newlines; each non-empty stripped line is one fragment. A single-line question yields exactly one fragment. |
| **F2** | Fragments are asked **in input order**; Answer rows are appended in that order (INV-2). |
| **F3** | One RAG pipeline call per fragment. Fragments are never merged into one query and never reordered. |
| **F4** | Each fragment's rewritten query includes the same statement and the same prior turns (snapshot consistency, INV-3). |
| **F5** | Fragment N **MUST NOT** see fragment N-1's answer — fragments of one ask are siblings, not a chain. Follow-ups are a separate, later ask. |

### 5.4 Pipeline result → row mapping (normative)

All reads are `result.get(...)`; the pipeline result dict shape is `app/rag/tasks.py:737-779`.

| Row field | Source | Notes |
| --- | --- | --- |
| `question` | the fragment | exact string, post-strip |
| `answer` | `answer` | empty string allowed only with `degraded=True` (INV-6) |
| `citations` | `json.dumps(citations)` | `Citation` asdict fields: `chunk_id`, `section_number`, `document_title`, `document_type`, `authority`, `url`, `snippet`, `confidence` (`app/rag/retrieval/result.py:107-123`) |
| `sub_queries` | `json.dumps(sub_queries or [])` | JSON list of the pipeline's internal decomposition; rendered read-only on the card as informational text (FR-12). Always a list (empty when the pipeline did not decompose). |
| `note_snapshot` | `note.content_text` | taken before the first call of the ask |
| `groundedness_score` | `groundedness_score` | render as a percentage |
| `hallucination_detected` | `hallucination_detected` | bool; true ⇒ red warning on the card |
| `degraded` | `bool((debug or {}).get("degraded_mode"))` | circuit-open stub fallback (`app/rag/resilient.py:182`) |
| `provider_model` | `llm_model` | |
| `token_usage` | `token_usage` | int |
| `rag_log_id` | `log_id` | `str` or `None` (`app/rag/tasks.py:369`) |

**Stale flag (rendering rule):** the detail view **MUST** compute `stale = latest_answer.created_at < note.updated_at` and render "statement edited after this answer — ask again to refresh" on the latest card. `Note.updated_at` carries `onupdate` (`app/models/notepad.py:40-44`); no extra column.

---

## 6. Invariants

Normative. Each **MUST** hold after any `ask` / `edit` completes.

| ID | Invariant |
| --- | --- |
| **INV-1** | An existing `NoteAnswer` row is never updated or deleted by `ask` or `edit`. Threads are append-only, matching `NoteEvaluation` (`app/models/notepad.py:9`) and `DailyPlan`. |
| **INV-2** | Answers of one ask are ordered by fragment input order; `created_at` ordering (and `id` ordering) agree with it. |
| **INV-3** | Every Answer carries a `note_snapshot` equal to the statement at ask time, so history stays interpretable after `edit` (FR-15). |
| **INV-4** | Every non-degraded Answer carries `rag_log_id` when the pipeline logged the run; a human rating can join back to the exact logged answer (NFR-7). |
| **INV-5** | `ask` never mutates `note.status`; freshness lives on the thread, not the lifecycle enum (FR-11). |
| **INV-6** | A `degraded` answer is persisted only with the degraded badge rendered; a grounded answer is never rendered with the badge. The two are visually distinct at all times. |
| **INV-7** | `build_standalone_query` context is bounded: ≤ `_MAX_PRIOR_TURNS` prior turns, each answer truncated to `_ANSWER_PREVIEW_CHARS`. No unbounded prompt growth. |
| **INV-8** | A failed ask writes zero rows — either all fragments' answers persist, or none does. |

---

## 7. Validation rules

Applied to request input before any RAG call; modelled on the light-validation style of `_validate_plan_payload` (`app/notepad/routes.py:40-52`). On failure: no RAG call, flash, redirect.

| ID | Rule | Failure behaviour |
| --- | --- | --- |
| **V-1** | `question` present, is a string, non-blank after strip | `warning` flash, redirect, no call |
| **V-2** | `split_fragments(question)` yields ≥ 1 fragment | `warning` flash, redirect, no call |
| **V-3** | Each fragment ≤ 4,000 characters | `warning` flash "…fragment N is too long…", redirect, no call |
| **V-4** | `len(fragments) ≤ _MAX_FRAGMENTS` | `warning` flash naming the cap, redirect, no call |
| **V-5** | `edit`: `content_text` non-blank after strip | `warning` flash, redirect, no write (FR-14) |
| **V-6** | The note exists and the caller may act on it (404 private / 403 non-author) | abort per §9 |

---

## 8. Route contracts and state machine

| Route | Method | Writes? | Behaviour |
| --- | --- | --- | --- |
| `/notepad/<int:note_id>` | GET | no | Detail + thread; unchanged signature, extended template context (FR-17) |
| `/notepad/<int:note_id>/ask` | POST | **yes** | §7 validation → gates (FR-3) → `thread.ask` per fragment → commit → flash count → redirect |
| `/notepad/<int:note_id>/edit` | POST | **yes** | §7 V-5 → assign `content_text` → commit → redirect |
| `/notepad/<int:note_id>/evaluate` | POST | **yes** | Unchanged (lens path, D4) |

Both new POSTs reuse the blueprint's global CSRF + login gate (`WTF_CSRF_ENABLED`; neither may be `@csrf.exempt`). Flag-off ⇒ `503` on both new POSTs, matching `app/notepad/routes.py:237-238`.

```
ask (author-only)
   │
   ▼
 V-1…V-4 pass? ──no──► warning flash, redirect (no RAG call, no row)
   │ yes
   ▼
 gates: notepad_ai_enabled and rag_enabled ──off──► 503
   │ on
   ▼
 note visible to caller? ──private & not author──► 404
   │
   ▼
 snapshot = note.content_text                     (INV-3)
   │
   ▼
 fragments = split_fragments(question)
   │
   ▼
 per fragment, in order (F2/F3):
   build_standalone_query(snapshot, prior_turns, fragment)
   → get_pipeline().run(query, top_k=cfg.context_max_chunks)
   → map to NoteAnswer (§5.4)
   │
   ├── any fragment raises ──► flash danger, rollback, ZERO rows (INV-8)
   ▼
 commit N rows → flash "N answer(s) added." → redirect detail
   │
   ▼
 detail renders latest card (+ stale flag) + collapsed history (FR-18)
```

---

## 9. Security requirements

| ID | Requirement |
| --- | --- |
| **SEC-1** | `ask` and `edit` **MUST** be author-only: authenticated non-author ⇒ `403` (precedent `app/notepad/routes.py:279-280`). A private note read by a non-author stays `404` and never reveals existence (`:217-220`). |
| **SEC-2** | `author_id` / note identity **MUST** come from the URL + `current_user`, never from form data. No client-supplied ids are trusted for ownership. |
| **SEC-3** | Both new POSTs **MUST** be CSRF-protected via the global gate; neither **MAY** be `@csrf.exempt`. |
| **SEC-4** | The RAG call **MUST** receive only: the rewritten query (statement + prior turns + question). No credentials, no other users' notes, no session data. |
| **SEC-5** | Pipeline errors **MUST NOT** leak raw driver/provider text to the user: log server-side, flash a generic message (precedent `app/notepad/routes.py:251-252`). |
| **SEC-6** | Answer rows inherit the note's visibility; no separate sharing flag **MUST** be introduced (D1 scope). |

---

## 10. Error taxonomy

| Code | HTTP | Meaning | User-facing behaviour |
| --- | --- | --- | --- |
| `blank_question` | 302 | V-1/V-2 failure | "Enter a question first." — no RAG call |
| `fragment_too_long` | 302 | V-3 failure | "Fragment N is too long (max 4000 characters)." |
| `too_many_fragments` | 302 | V-4 failure | "At most 10 fragments per ask." |
| `flag_disabled` | **503** | `notepad_ai_enabled` or `rag_enabled` off | `abort(503, description=…)`, precedent `app/notepad/routes.py:237-238` |
| `note_not_found` | **404** | private note, non-author | Same 404 as `detail` |
| `not_authorized` | **403** | authenticated non-author on ask/edit | Precedent `set_status` |
| `rag_failed` | 302 | pipeline raised (FR-8) | "Legal RAG failed: …" danger flash, **zero rows** (INV-8) |
| `degraded_answer` | 302 | circuit open, stub fallback persisted | Success flash + amber "retrieval unavailable" badge on the card (INV-6) |

---

## 11. Acceptance criteria

Testable assertions; each maps to a test in §12.

- **AC-1** Given `notepad_ai_enabled` off, `POST /ask` returns 503 and zero `NoteAnswer` rows exist.
- **AC-2** Given `rag_enabled` false, `POST /ask` returns 503 and zero rows exist.
- **AC-3** Given a stubbed pipeline returning a full result, `POST /ask` with a one-line question appends exactly one row with `question`, `answer`, `citations` JSON, `groundedness_score`, `hallucination_detected`, `provider_model`, `token_usage`, `rag_log_id`, and `note_snapshot` equal to the pre-ask statement; `note.status` is unchanged.
- **AC-4** Given a three-line question, `POST /ask` appends exactly three rows in input order, each carrying its own fragment as `question` (F2/F3).
- **AC-5** Given a pipeline stub that raises on the second fragment, `POST /ask` persists **zero** rows (INV-8).
- **AC-6** Given a stubbed circuit-open (degraded) result, the row persists with `degraded=True` and the rendered page shows the degraded badge; a grounded result renders without it (INV-6).
- **AC-7** Given two sequential asks (a follow-up), the second answer's rewritten query contained the statement, the first question, and the new question — asserted on the stub's captured argument (FR-6, INV-7).
- **AC-8** Given a note with 7 prior turns, the rewritten query contains only the last 5 (INV-7).
- **AC-9** Given an author edits the statement, all prior rows are byte-identical afterwards (INV-1/INV-3) and the latest card shows the stale marker (§5.4).
- **AC-10** Given a non-author POSTs to `ask` or `edit` on another user's note, the response is 403 and no row or content changes.
- **AC-11** Given a private note, a non-author GET of the detail page and POST to `ask` both 404.
- **AC-12** Given a blank or whitespace question, the response redirects with a warning and the stubbed pipeline was never called (V-1/V-2).
- **AC-13** Given an 11-line question, the response is `too_many_fragments` and the pipeline was never called (V-4).
- **AC-14** All existing tests in `tests/test_notepad.py` pass **unmodified** — proves the lens path is untouched (D1/D4).
- **AC-15** `split_fragments` / `build_standalone_query` unit tests pass with no Flask app context pushed (NFR-6).
- **AC-16** `pytest -m "not slow" --tb=short -q legal_paragraph_detection_engine/tests/ tests/` passes (fast shard, NFR-1).

---

## 12. Traceability

| Requirement | Test file | Plan phase |
| --- | --- | --- |
| FR-5…FR-8, §5.2/§5.3 pure functions, INV-7, AC-7/AC-8/AC-15 | `tests/test_notepad_thread.py` *(new)* | 1 |
| Model + relationship + migration, INV-1…INV-4 | `tests/test_notepad.py` *(extend, existing cases unmodified)* | 1–2 |
| FR-9…FR-14, V-1…V-6, §8, §9, §10, AC-1…AC-6, AC-9…AC-13 | `tests/test_notepad.py` *(extend)* | 2 |
| FR-17…FR-20, stale marker, degraded badge rendering | `tests/test_notepad.py` *(extend, template assertions on rendered body)* | 3 |
| FR-3/FR-4 flag behaviour | `tests/test_notepad.py` (monkeypatch `cfg`) | 2 |
| Docs surface (plan→spec→INDEX→CONTEXT.md) | `tests/test_steering_docs.py` (existing) | 4 |
| SEC-1…SEC-6 | `tests/test_notepad.py` *(extend)* | 2 |
| NFR-3 root-layout freeze | `tests/test_repo_root_layout.py` (existing) | all |

**Fast-shard command** (what CI runs, `.github/workflows/validation.yml`):

```bash
pytest -m "not slow" --tb=short -q legal_paragraph_detection_engine/tests/ tests/
```

---

## 13. Question status

| # | Question | Specified default | Flip cost | Owner |
| --- | --- | --- | --- | --- |
| **D1** | Reuse `NoteEvaluation` or new model? | **New `NoteAnswer`** (payload shapes differ; existing tests pin the 7 lens keys) | — (closed by this spec) | spec |
| **D2** | Kill switch: reuse or new flag? | **Reuse `NOTEPAD_AI_ENABLED`**; no new `Setting` row | One row + `.env.example` line if split | operator |
| **D3** | Sync or async? | **Synchronous V1**; async is a non-goal | Route + template rewrite | operator |
| **D4** | Lens evaluation keep or remove? | **Keep, demoted** to a secondary labelled button | One template block to remove; removal warrants an ADR | operator |
| **Q1** | Multi-head alembic tree — which revision parents `add_note_answer_table`? | Unresolved; `flask db heads` decides at implementation | None if a single head; one merge revision otherwise | implementer (Phase 1) |
| **Q2** | Cap per-ask spend with `_MAX_FRAGMENTS = 10` — is the bound right? | Specified at 10 (V-4) | One constant | operator |

---

## 14. Change log

| Date | Change |
| --- | --- |
| 2026-10-11 | Initial specification. Normative sections 3–6 fixed against the implementation plan's recommended defaults; D1–D4 specified with flip costs; 16 acceptance criteria and the traceability matrix added. |
| 2026-10-11 | **Phase 1 implemented.** `NoteAnswer` model + `Note.answers` relationship (`app/models/notepad.py`); `app/notepad/thread.py` (`split_fragments`/`build_standalone_query`/`get_pipeline`/`ask` — F1–F5, INV-7, FR-4/FR-6–FR-8, V-1…V-4); migration `add_note_answer_table` chained off `add_rag_answer_feedback` (Q1 resolved: sole head). Tests added: `tests/test_notepad_thread.py` (11), `TestNoteAnswerModel` + `TestAsk` in `tests/test_notepad.py` (11), `tests/test_migration_note_answer.py` (7) — covering AC-1…AC-8, AC-15 at module level (routes land in Phase 2). Pre-existing stale head assertion in `tests/test_migration_rcm_nullable.py` fixed to the durable single-head invariant (it was red at HEAD before this work). |
| 2026-10-11 | **Phase 2 implemented.** `POST /notepad/<id>/ask` and `POST /notepad/<id>/edit` (`app/notepad/routes.py`): §8 order pinned (visibility 404 → author-only 403 → §7 validation warning-redirect → shared 503 gate description → `thread.ask` → commit), graceful `rag_failed` danger-flash path (INV-8), edit author-only touching `content_text` only (FR-14…FR-16). §5.2 amended: `validate_question` added as the thread's public validation entry point so the route can honour §8 ordering without duplicating the fragment caps (FR-5 single ownership preserved — `ask` calls it). Tests: `TestAskRoute` (9) + `TestEditRoute` (5) in `tests/test_notepad.py`. |
| 2026-10-12 | **Phase 3 implemented.** `detail()` passes parsed thread + stale flag + `rag_available` (FR-17 amended); `notepad/detail.html` reworked per plan §7 — statement card with author-only *Edit statement* form, primary **Ask Legal RAG** card (hidden behind the gates), latest answer as primary card with citations/verification strip/meta and the §5.4 stale marker, older turns in a collapsed `<details>` oldest-first, lens button demoted to **Evaluate idea (7 lenses)** (FR-18…FR-20, D4). **§5.1/§5.4 amended**: `sub_queries` column added to `NoteAnswer` + `add_note_answer_table` (the un-shipped Phase 1 migration was amended in place rather than chained — FR-12 could not surface the pipeline's decomposition without persisting it; `_to_answer` always stores a JSON list). Tests: `TestDetailThreadUI` (12, rendered-body assertions per the traceability matrix). |
