# Module Memory: Adjudication

## Purpose
Manage non-sample adjudication cases: section selection (55/56/58/63/64),
checklist violations, KMC licence lookup, and legal document generation.

## Responsibilities
- Record non-sample case details (complaint, first/follow-up inspection dates,
  checklist items, selected sections).
- Derive `applicable_sections`, `sections_display`, `case_track`,
  `violations` via `app/shared/context_derivers.py`.
- KMC (Kolkata Municipal Corporation) licence lookup endpoints (PUBLIC).
- Link to `Inspection` (via `from_inspection`) and `CaseFile`.
- `version_id` OCC on the Adjudication model.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/adjudication/routes.py` | 28 KB | Main blueprint (large) |
| `app/adjudication/__init__.py` | — | `adjudication_bp` |

## Public Interfaces
- `adjudication_bp` (prefix `/adjudication`): CRUD + `lookup_ce_route`,
  `lookup_fssai_route`, `lookup_fbo_issues` (all PUBLIC).

## Dependencies
Flask, SQLAlchemy, `app.models.Adjudication`, `app.shared.context_derivers`,
`app.metadata_extractor` (optional NER), `app.utils.suggester` (section aid).

## Configuration Files
- `fss_sections.md` — FSS Act section text (consumed by suggester).
- `instance/app.db` (SQLite dev).

## Known Issues
- `inspection_date` field is semantically the follow-up date (misnamed; see
  `case_keys.py` date disambiguation rules).
- `app.adjudication.routes` is in mypy override `ignore_errors` list.

## Future Improvements
- AI-powered section suggestion (Phase 4).
- Replace suggester heuristics with RAG/vector search.

## Current TODOs
- Refactor the 28 KB route file into a service layer.
- Wire `legal_paragraph_detection_engine` for citation-aware drafting.
