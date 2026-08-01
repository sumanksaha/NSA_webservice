# Module Memory: Audit Trail

## Purpose
Tamper-evident audit logging at two layers: (1) hash-chained `AuditLog` for
photo-evidence / inspection records, (2) session-level `RecordAudit`
capturing all INSERT/UPDATE/DELETE on the three core business models.

## Responsibilities
- `AuditLog` model (`audit_log` table): `prev_hash` → `curr_hash` SHA-256
  chain — retroactive modification detectable.
- `RecordAudit` model (`record_audit` table): JSON `changes` diff of changed
  columns; tracks login_success/login_failed.
- `app/audit_hooks.py`: SQLAlchemy `after_flush` listener on `db.session` that
  records create/update/delete for `Adjudication`, `Bill`, `CaseFile`.
- `app/audit/` blueprint (`/admin/audit-log`): paginated, filterable read-only
  viewer.
- `fso_issue_audit` table: state-transition log for the FBO issue state machine.

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/audit_hooks.py` | 6.6 KB | after_flush → RecordAudit |
| `app/audit/routes.py` | 1.7 KB | Read-only viewer |
| `app/audit/__init__.py` | — | `audit_bp` |
| `app/models.py` | — | `AuditLog`, `RecordAudit`, `FboIssueAudit` |

## Public Interfaces
- `register_audit_hooks()` — idempotent listener registration.
- `RecordAudit` / `AuditLog` models.
- `/admin/audit-log` route.

## Dependencies
Flask, SQLAlchemy, hashlib, json.

## Configuration Files
- None special (uses `db.session.info["audit_user_id"]`).

## Known Issues
- `_EXCLUDED_COLUMNS` in audit_hooks drops `synced_at`, `pdf_task_id`,
  `pdf_generated_at` from change diffs — intended, but means Celery-driven
  PDF events are not audited.
- `AuditLog` hash-chain write path only referenced in skeleton doc; primary
  runtime audit is `RecordAudit` via hooks.

## Future Improvements
- Expose `AuditLog` (hash-chain) writes in the verification pipeline
  (photo evidence).
- Exportable audit trails (CSV/PDF).

## Current TODOs
- Wire hash-chained `AuditLog` into inspection photo verification flow.
