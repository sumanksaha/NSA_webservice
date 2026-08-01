# Module Memory: Shared Contracts

## Purpose
Canonical field-name contract (`case_keys.py`) and pure context-deriver
helpers (`context_derivers.py`) that unify the four UIs (Inspection, Sample,
Case-File, Adjudication) to prevent field-name drift.

## Responsibilities
- `case_keys.py`: canonical constant names for every field across the 4 UIs;
  OLD→NEW key mappings per module (with `TypedDict` shapes); date
  disambiguation rules; section helpers (`sections_display`,
  `resolve_case_track`).
- `context_derivers.py`: pure functions deriving `applicable_sections`,
  `sections_display`, `case_track`, `violations`, `same_entity` from form data;
  full `derive_case_file_context` / `derive_adjudication_context`.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/shared/case_keys.py` | 16 KB | Canonical key contract + mappings |
| `app/shared/context_derivers.py` | 15 KB | Pure derivation helpers |
| `app/shared/__init__.py` | — | (empty) |

## Public Interfaces
- Constants: `SHARED_*`, `DATE_*`, `PARTY_*`, `SAMPLE_*`, `LAB_*`, `SECTION_*`,
  `DERIVED_*`.
- Mappings: `INSPECTION_OLD_TO_NEW`, `SAMPLE_OLD_TO_NEW`,
  `ADJUDICATION_OLD_TO_NEW`, `CASE_FILE_OLD_TO_NEW` (and reverse).
- Functions: `sections_display()`, `resolve_case_track()`,
  `get_hygienic_sections()`, `get_nonsample_licence_sections()`,
  `get_sample_sections()`, `derive_case_file_context()`,
  `derive_adjudication_context()`, `derive_violations()`.

## Dependencies
stdlib typing (`TypedDict`, `Any`) + internal imports only.

## Configuration Files
- `fss_sections.md` (consumed indirectly via sections_data).

## Known Issues
- `context_derivers.py` has `# type: ignore` at top (mypy non-strict bypass).
- Date disambiguation relies on correct usage; historical columns (e.g.
  adjudication `inspection_date`) are semantically follow-up dates.

## Future Improvements
- Enforce canonical keys in all templates (migration WIP).
- Add TypedDict for full form-data shape.

## Current TODOs
- STEP 4 of uniform-keys migration — complete template migration to canonical
  keys (in progress per `context_derivers` docstring).
