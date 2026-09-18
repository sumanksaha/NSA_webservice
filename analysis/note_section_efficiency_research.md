# Background Agent — Note Section Efficiency Research

Status: spawned; working in parallel.
Question: How to improve the notepad/note-evaluation section (`app/notepad/` + `Note`/`NoteEvaluation` models) for efficiency?

Primary sources being checked (not secondary):

- Source: `app/notepad/routes.py` (note CRUD, AI evaluation, plan generation, status transitions, visibility toggle)
- Source: `app/models/` (Note, NoteEvaluation, DailyPlan definitions — not fully read; to be verified)
- Source: `app/ai_assistant/service.py` (AIAssistantService — evaluation payload keys `summary`, `implementation_plan`, etc.)
- Source: `analysis/data_flow.md` (sync pipeline context — not directly note-related)

Convention: saved in `analysis/` (repo's existing research-note location, per `research_case_file_adjudication.md`). Single Markdown file, claims cited to file/line.

Efficiencies being evaluated (lazy ladder, per AGENTS.md / ponytail):

1. Does note evaluation need AI for every note? (YAGNI — skip if batch or only for open notes)
2. Synchronous AI calls block (`evaluate`, `generate_plan`) — could be queued / async.
3. `Note.query.filter(...)` sorts full open-note set in-memory for AI payload; could index/filter earlier.
4. `evaluations` loaded in `detail()` via loop over `note.evaluations`; could be eager-loaded (`joinedload`).
5. Plan generation builds `offered` list from all open notes then validates; could paginate / limit.

Not included (skipped, with reason):

- Full DB schema audit (not needed for efficiency focus).
- Secondary blog/AI-efficiency articles (skill requires primary sources only).

Next: agent will read `models/`, `ai_assistant/service.py`, and report which of 1-5 actually holds.
