# ADR-0005: Collapse the photo pipeline fork

- **Status:** Accepted
- **Date:** 2026-09-20
- **Context:** Inspection photo evidence (`app/inspection/`, `tests/inspection/`)
- **Supersedes (file layout only):** ADR-0004 §§5–7

## Context

ADR-0004 introduced `PhotoProcessor`, `EvidenceStore`, and `OCRDispatcher`
under `app/inspection/services/` as the deep modules behind the photo
routes. The route seam (`app/inspection/routes/photo_routes.py`) was never
migrated: it instantiated all three dead and routed every handler through
the pre-existing `InspectionPhotoService` (`app/inspection/photo_service.py`),
while its own docstring claimed the opposite. Two implementations and two
test suites were being paid for; the trio additionally lacked the
adjudication R2/B2 upload and the geo-verify-and-stamp stages, and
`EvidenceStore.save_evidence` had drifted from its own documented
interface (`save(processed_photo)`).

## Decision

`InspectionPhotoService` is the surviving deep module at the photo seam.
The `app/inspection/services/` trio and its two test files
(`tests/inspection/test_photo_processor.py`,
`tests/inspection/test_evidence_store.py`) are deleted. The dead
instantiations and the false docstring in `photo_routes.py` were removed
first, as their own step.

ADR-0004's intent — one deep module at the photo seam — stands; only its
file layout is superseded.

## Consequences

- **Positive:** one implementation, one test suite
  (`tests/test_inspection_photo_service.py`,
  `tests/test_upload_evidence_characterization.py` — 20 passed at collapse
  time); photo bugs concentrate in one module.
- **Positive:** `CONTEXT.md` names `InspectionPhotoService` as the photo
  evidence seam; the trio terms are retired.
- **Risk:** none observed — the trio had zero production callers, so the
  deletion test passed cleanly (delete, suite stays green).
