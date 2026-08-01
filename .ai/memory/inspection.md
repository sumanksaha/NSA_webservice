# Module Memory: Inspection

## Purpose
Record food-business inspections, capture geo-tagged photo evidence, calculate
compliance deadlines, and run the photo-verification pipeline (EXIF/IP-geo/
distance). Supports inspection↔adjudication linkage and dismissal.

## Responsibilities
- CRUD inspection visits with FSO assignment + FBO identity.
- Compliance-deadline calculation (15-day default via `inspection_utils`).
- Photo evidence upload to R2/B2 + verification pipeline.
- Link inspection → `Adjudication` (one-to-one via `adjudication_id` FK).
- Dismiss action (marks dismissed + records actor/date).

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/inspection/routes.py` | 38 KB | Main blueprint (largest route file) |
| `app/inspection/tasks.py` | 4 KB | Celery verification pipeline |
| `app/inspection/verification_service.py` | 3 KB | Orchestration |
| `app/inspection/inspection_utils.py` | 3 KB | deadline calc, helpers |
| `app/inspection/geo_verification.py` | 2 KB | GPS vs FBO-distance check |
| `app/inspection/ip_verification.py` | 2 KB | IP-region match |
| `app/inspection/image_processing.py` | 3 KB | EXIF / image validation |
| `app/inspection/distance_verification.py` | 3 KB | haversine geo check |
| `app/inspection/audit.py` | — | (empty stub) |

## Public Interfaces
- `inspection_bp` (prefix `/inspection`): CRUD + `lookup_ce_route`,
  `lookup_fssai_route` (both PUBLIC).
- `PhotoEvidence` model (`photo_evidence` table).

## Dependencies
Flask, SQLAlchemy, boto3 (storage), Pillow, pytesseract (optional), Celery,
`app.utils.storage`, `app.utils.pdf_utils`.

## Configuration Files
- `R2_*` env vars for storage; `REDIS_URL` for Celery.
- `instance/app.db` SQLite (local dev).

## Known Issues
- `audit.py` in the inspection module is an empty stub.
- Route file is large (38 KB) with heavy inline logic — ripe for service
  extraction.
- `inspection_utils.py` uses `random` (flagged `S311` in ruff per-file-ignores).

## Future Improvements
- Extract service-layer logic from the 38 KB route file.
- Full EXIF metadata extraction + tamper detection.

## Current TODOs
- Complete photo-evidence verification pipeline tests.
