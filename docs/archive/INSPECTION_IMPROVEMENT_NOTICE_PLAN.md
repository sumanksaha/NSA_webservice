# Implementation Plan — Inspection-Keyed Improvement Notice + Corrective Measures

> Output of the grilling session (2026-08). Decisions recorded in `CONTEXT.md`
> (Inspection Checklist / Improvement Notice / Corrective Measures Implemented).
> Directives-as-separate-document plan was **dropped**. Email is **deferred**.

## Goal

When an FSO records an inspection with violations, an Improvement Notice
(u/s 32, FSS Act) keyed to the _Inspection_ must be generatable and reachable
from the UI, and the inspection stays listed as an open issue until the FSO
asserts **Corrective Measures Implemented**.

## Facts this builds on (verified in code)

- Inspection UI exists: navbar link (`app/templates/base.html:215`), create form
  (`app/inspection/templates/inspection/index.html:11` → `POST /inspection/create`),
  views `list/open/pending/history/detail`.
- Improvement Notice already fully built but **sample-keyed** and **unlinked from UI**:
  template `app/food_cell/templates/food_cell/improvement_notice.html`,
  renderer `app/food_cell/renderer.py:113-171`, routes `app/food_cell/routes.py:157-191`.
- Violations deriver exists: `derive_violations()` (`app/shared/context_derivers.py:244`)
  over plain dicts; rules in `CHECKLIST_RULES` (`:39`). Used today only by adjudication
  (`app/adjudication/routes.py:209-211`) whose model carries the 12 checklist columns
  (`app/models/document.py`, Adjudication class).
- Dismissal lives in `app/inspection/routes/derived_views.py` (`dismiss_inspection`,
  line 122; past-deadline-only rule) and `is_dismissed/dismissed_by/dismissed_at`
  on `Inspection` (`app/models/inspection.py`).
- No email infrastructure exists anywhere (confirmed by grep).

---

## Step 1 — Model + migration

**Files:** `migrations/versions/*_inspection_checklist_and_corrective_measures.py` (new),
`app/models/inspection.py`.

- Add `Inspection.checklist_json` (TEXT, nullable) — `{field_name: "yes"/"no"}` for the
  12 items (same field names as Adjudication so `derive_violations()` works unchanged).
- Drop `is_dismissed`, `dismissed_by`, `dismissed_at`.
- Add `corrective_implemented` (bool, default False), `corrective_implemented_by`,
  `corrective_implemented_at`.
- Add `notice_issued_at` (DateTime, nullable) — set on first notice render/download;
  non-null ⇒ record frozen.
- One Alembic revision; SQLite-dev + Postgres-prod compatible batch mode if needed.

## Step 2 — Checklist on the create form

**Files:** `app/inspection/templates/inspection/index.html`, `app/inspection/routes/inspection_routes.py::create_inspection`.

- 12 radio/select pairs, defaults matching Adjudication conventions
  ("yes" compliant; `artificial_colour`/`Expired_item` default "no").
- `create_inspection` collects them into the JSON column.

## Step 3 — Derive violations + actions

**Files:** `app/shared/context_derivers.py`, `app/food_cell/renderer.py`.

- Reuse `derive_violations(checklist_dict)` as-is.
- New `derive_actions(violations) -> list[str]` next to it: one corrective action
  per violation, templated from `CHECKLIST_RULES` titles
  (e.g. "Unclean Premises" → "Thoroughly clean and maintain the premises…").

## Step 4 — Inspection-keyed renderer

**Files:** `app/food_cell/renderer.py`.

- Replace `build_improvement_notice_context(sample…)` /
  `render_improvement_notice_html(sample…)` / `render_improvement_notice_pdf(html, sample)`
  with inspection-keyed equivalents mapping:
  `fbo_name ← Inspection.fbo_name`, `fbo_address ← fbo_address`,
  `fbo_fssai ← fssai_license`, `fso_name ← fso_name`,
  `inspection_date ← inspection_date`, `compliance_deadline ← compliance_deadline`,
  `improvement_notice_ref ← inspection_code`, plus violations/actions/deadline params.
- Template itself unchanged (already accepts this context shape).

## Step 5 — Routes

**Files:** `app/food_cell/routes.py`, `app/inspection/routes/derived_views.py`,
`app/inspection/routes/inspection_routes.py`.

- Delete sample-keyed routes (`routes.py:157-191`) and their context builder.
- Add under food_cell blueprint:
    - `GET /food-cell/improvement-notice/inspection/<int:inspection_id>/html`
    - `GET /food-cell/improvement-notice/inspection/<int:inspection_id>/pdf`
      Both: resolve inspection, derive violations/actions from `checklist_json`,
      render; on first access set `notice_issued_at = now` (freeze).
- `update_inspection` (`inspection_routes.py:232`): return **409** when
  `notice_issued_at` is set.
- Replace `POST /<int:inspection_id>/dismiss` with
  `POST /<int:inspection_id>/implement-corrective-measures` — no deadline precondition,
  sets `corrective_implemented*` trio, audited (AuditLog hook pattern).

## Step 6 — UI wiring

**Files:** `detail.html`, `open_issues.html`, `pending_action.html`, `history.html`.

- `detail.html`: "View Notice" / "Download PDF" buttons when derived violations exist;
  status badge for corrective state.
- Swap dismiss button/modal → "Mark Corrective Measures Implemented" on
  `pending_action.html` **and** `open_issues.html` (new rule: no deadline precondition).
- `history.html`: show corrective-implemented entries.

## Step 7 — Open-issues semantics change

**Files:** `app/inspection/routes/derived_views.py`.

- `open_issues()`: replace `compliance_deadline >= today AND ~is_dismissed AND adjudication IS NULL`
  with `~corrective_implemented AND adjudication IS NULL` (**deadline no longer filters**).
- `pending_action()`: keep as the overdue slice of the same set (deadline < today).
- Assumption carried from current code: adjudication-linked inspections leave the
  open lists (handled by the adjudication flow). Revisit only if wrong.

## Step 8 — Tests

- `tests/test_inspection_checklist.py` (new): form→JSON parsing, defaults,
  `derive_violations` round-trip.
- `tests/test_improvement_notice_inspection.py` (new): context mapping, routes 200/PDF,
  freeze-on-first-render, 409-after-freeze, empty-checklist → no violation/no notice.
- `tests/test_derive_actions.py` (new): action-per-violation derivation.
- Update every existing test touching `dismiss` / `is_dismissed`
  (grep at build time; includes `test_step*_integration.py` candidates).

## Explicitly out of scope

- Email delivery of notices (deferred; revisit on request).
- FboIssue module changes (user reinvestigating separately).
- Any change to DO Intimation (Phase 21) sync/tasks.
