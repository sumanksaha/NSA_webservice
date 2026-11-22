# ADR-0018: Remove the document viewer and version control

- **Date:** 2026-11-21
- **Status:** Accepted
- **Scope:** `app/document_viewer/`, `app/version_control/`,
  `app/services/version_control.py`, `app/services/document_lifecycle.py`,
  `app/utils/document_storage.py`, the `Version` model, the editor/xref/TOC/
  renumber routes, editor-only frontend assets, and the `versions` table.

## Context

The app shipped a fully implemented Quill-based document viewer/editor:

- `app/document_viewer/routes.py` — autosave, save-to-PDF, image upload/serve,
  Markdown export, and session restore;
- `app/document_viewer/templates/document_viewer/editor.html` — split
  editor/live-preview UI with TOC, xref, history, and AI buttons;
- `app/document_viewer/renderer.py` — shared case/adjudication HTML rendering;
- `app/version_control/` and `app/services/version_control.py` — version
  snapshots, compare, restore, and branching, backed by the `versions` table.

The feature was only reachable from the timeline, search, annexure, and
evidence "edit" links. Version history was only reachable from the editor.
Rich-text editing of generated documents was not used by the day-to-day
workflow, which edits structured case data and regenerates documents from
templates. Keeping the editor, its save/restore/version machinery, the
editor-only AI-assistant frontend, and the xref/TOC report UI added a large
maintenance surface for no operational benefit.

## Decision

Remove the document viewer/editor and version control entirely:

- delete `app/document_viewer/`, `app/version_control/`,
  `app/services/version_control.py`, `app/services/document_lifecycle.py`,
  and `app/utils/document_storage.py`;
- delete the editor, `/xref_report`, `/toc_report`, and `/renumber_annexures`
  routes and their templates, plus `app/shared/document_reports.py`;
- delete the editor-only frontend (`document_viewer/editor.ts/js`,
  `version_control/version_control.ts/js`, `ai_assistant.ts/js`) and the
  corresponding types in `app/static/ts/types/api.ts`;
- remove the `Version` model and its export/import handling in
  `app/case_file_generator/services.py` (old exports with a `versions` key
  still import; the key is ignored);
- drop the `versions` table with an Alembic migration
  (`drop_versions_table`);
- repoint the timeline/search/annexure/evidence editor links to the existing
  prefilled data form (`edit_form`).

Keep:

- `app/shared/document_rendering.py` (relocated from
  `app/document_viewer/renderer.py`) because validation rendering and any
  future report callers need it;
- `app/cross_reference/`, `app/toc_generator/`, and `app/pdf_assembly/`
  because the PDF generation pipeline still uses them;
- `app/static/vendor/quill/` because Work Diary and Inspection use Quill;
- the `app/ai_assistant/` backend because API v2, auditor, notepad, RAG, and
  plugins use the service (only the editor frontend is removed).

## Consequences

- **Positive:** removes the editor, autosave/save/restore, image upload,
  Markdown export, xref/TOC reports, version history/compare/restore/branch,
  and their tests — a large simplification with no remaining in-repo caller.
- **Positive:** the case data form is the single edit surface; generated
  DOCX/PDF/`.adoc` documents remain downloadable from the case index.
- **Boundary:** Work Diary is unaffected — it has its own Quill editor and
  uses the shared vendor assets, not the document viewer or version service.
- **Risk:** version history is permanently deleted on deploy. The migration's
  downgrade recreates the schema only, not the rows.
- **Risk:** any external consumer of `/api/version-control/*` would now 404.
  No in-repo caller remains.
- **Risk:** case JSON exports no longer contain `versions`. Old exports still
  import because the unknown key is ignored.

## Alternatives considered

- **Keep the editor and only hide its UI:** rejected — the save/restore/
  version machinery and tests would remain, defeating the simplification.
- **Keep version history but remove the editor:** rejected — the history UI was
  only reachable from the editor, and the API had no in-repo consumer.
- **Remove the shared renderer too:** rejected — validation rendering depends
  on it; it was relocated instead.