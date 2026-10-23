## Agent skills

### Issue tracker

GitHub issue tracker. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles mapped to this tracker's label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` at repo root with `docs/adr/` for architectural decisions. See `docs/agents/domain.md`.

### Docs index

Every other doc is listed in `docs/INDEX.md`. Check it before writing a new one.

### Evaluation artifacts

Schemas for the training/eval cache files: `evaluation/README.md`. Everything under `evaluation/out/` is gitignored and the file tools refuse it — read those paths via the terminal.

### Photo violation scoring (inspection UI)

- **Tool**: `PhotoEvidenceScorer` (`app/inspection/photo_scoring.py`) backed by `inception/mercury-decide:free` — a decision/noul model, **not JEV**. JEV (`typesafe/jev-1.13`) is a text retrieval decision model and cannot analyze photos.
- **What it does**: scores a premises photo against the 12-field inspection checklist, returning an independent `p_violation` (0–1) per field with no cap on selections. All 12, none, or any subset can be flagged.
- **Gate**: a premises-interior question suppresses scoring on non-premises images. `Pest_report`/`Water_report` are excluded (require documents, not photos).
- **Flow**: FSO clicks a photo → upload → scorer runs between storage and Evidence insert → `EvidenceAssessment` row persisted → shown on inspection detail page → "Apply to checklist" pre-fills checklist (advisory only; FSO confirms).
- **Endpoint**: `POST /inspection/<id>/checklist/apply-suggestion` accepts `{"fields": [...]}` with correct form polarity.
- **Config**: `PHOTO_SCORING_ENABLED` (default false), `PHOTO_SCORING_THRESHOLD` (default 0.70), `PHOTO_SCORING_MODEL`, `PHOTO_SCORING_ENDPOINT`, `PHOTO_SCORING_TIMEOUT`, `PHOTO_SCORING_MAX_BYTES`.
- **Tests**: `tests/test_photo_scoring.py` (44 tests).
