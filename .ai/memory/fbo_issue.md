# Module Memory: FBO Issue Tracking

## Purpose
Unified state machine for Food Business Operator (FBO) issues arising from
inspections or samples, with a tamper-evident audit trail of every state
transition.

## Responsibilities
- Issue model: `fbo_id`, `manufacturer_fbo_id`, `fbo_name`, `source_type`
  (inspection|sample), `state`, `fso_name`, `detail_json`, geo coords.
- State machine: `open → permission_pending → permission_granted → closed`,
  plus `dismissed`. Enforced via DB check constraints.
- DB constraints prevent: sample+dismissed; non-sample with null mfr FBO id.
- `FboIssueAudit` captures every transition (issue_id, from_state→to_state,
  asserted_by, note).

## Main Source Files
| File | Size | Notes |
|------|------|-------|
| `app/fbo_issue/routes.py` | 12 KB | Main blueprint |
| `app/fbo_issue/__init__.py` | — | `fbo_issue_bp` |
| `app/models.py` | — | `FboIssue`, `FboIssueAudit` models |

## Public Interfaces
- `fbo_issue_bp` (prefix `/fbo-issue`): create, transition, view, audit.

## Dependencies
Flask, SQLAlchemy, `app.models.FboIssue`/`FboIssueAudit`.

## Configuration Files
- `render.yaml` (env), `instance/app.db` (local).

## Known Issues
- Geo fields (`reg_lat`/`reg_lng`) populated lazily on geocode.
- State transitions only partially exposed via UI (constraints enforce validity).

## Future Improvements
- Notification/webhook on state transitions.

## Current TODOs
- End-to-end state-machine test coverage (`test_fbo_issue.py` exists — 38 KB).
