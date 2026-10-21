# Research — `record_audit.record_id` overflow breaks Work Diary saves

**Date:** 2026-10-13
**Trigger:** `POST /diary/bulk?m=2026-10&fso_name=Suman+Saha` returns `500 Internal Server Error` in production.

## Symptom (from the traceback)

```
sqlalchemy.exc.DataError: (raised as a result of Query-invoked autoflush;
consider using a session.no_autoflush block if this flush is occurring prematurely)
(psycopg.errors.StringDataRightTruncation) value too long for type character varying(50)
[SQL: INSERT INTO record_audit (user_id, action, record_type, record_id, changes,
      timestamp, ip_address, user_agent) VALUES (...) RETURNING record_audit.id]
[parameters: {..., 'record_type': 'WorkDiaryEntry',
              'record_id': 'fso_name=Suman Saha|work_date=2026-10-01|duty_seq=1', ...}]
```

The user-facing effect is **not** just a missing audit row: because the audit `RecordAudit`
row is added inside the same session/transaction as the diary write (`app/audit_hooks.py`,
`after_flush`), the truncation aborts the whole `db.session.commit()` in
`app/diary.py::bulk()`. The officer's legitimate diary edits are rolled back as collateral
damage — which is why "I am failing to add entries into monthly diary."

## Root cause

Two facts collide:

1. **The column is too small.** `RecordAudit.record_id` is `VARCHAR(50)`:
   - Model: `app/models/auth.py:51` — `record_id = db.Column(db.String(50), nullable=False, index=True)`
   - Migration: `migrations/versions/f2203965fa39_add_record_audit_table.py:27` — `sa.Column("record_id", sa.String(length=50), nullable=False)`

2. **`WorkDiaryEntry` has a composite primary key**, so the audit hook builds the
   identifier by concatenating all PK parts:
   - `app/audit_hooks.py:116-135` `_record_identifier()` → `"|".join(f"{col.name}={val}")`
   - `app/models/diary.py:29-31` — PK is `(fso_name [String(100)], work_date [String(10)], duty_seq [Integer])`

   For `fso_name="Suman Saha"` that renders to
   `fso_name=Suman Saha|work_date=2026-10-01|duty_seq=1` = **51 characters** — one byte
   over the `VARCHAR(50)` limit → PostgreSQL `StringDataRightTruncation`.

The other audited models (`Adjudication`, `Bill`, `CaseFile`) carry a single integer `id`,
so their `record_id` is tiny and never overflow. **`WorkDiaryEntry` is the only model whose
audit key exceeds 50 chars**, which is why the bug surfaced only after diary auditing was wired up.

### Why CI / local dev never caught it

- The dev and test database is **SQLite** (`tests/conftest.py:20` pins `DATABASE_URL` to a
  temp SQLite file). **SQLite does not enforce `VARCHAR(n)` length**, so a 51-char value
  inserts fine locally. Production runs on **PostgreSQL** (Render), which does enforce it.
- The only test that exercises the composite-key path,
  `tests/test_schedule_planner.py::test_write_creates_an_audit_row`, uses `fso_name="Officer A"`,
  which renders to exactly **50 characters** — right at the boundary, so it passes on the
  narrow column. Any officer name longer than "Officer A" (e.g. "Suman Saha", 10 chars vs 9)
  pushes it over.

Worst-case width for a composite key: `"fso_name="`(9) + fso_name(≤100) + `"|"`(1) +
`"work_date="`(10) + date(10) + `"|"`(1) + `"duty_seq="`(9) + seq(≤2) ≈ **142 chars**. So `VARCHAR(50)` is not marginally wrong — it is roughly a third of
what is required.

## Options considered

| Option | Verdict |
| --- | --- |
| **A. Widen `record_id`** (→ `VARCHAR(255)`) | ✅ **Recommended.** Smallest, safest change. Keeps the human-readable composite key in the audit UI and the existing index. Preserves every existing row. |
| B. Truncate the identifier to 50 chars | ❌ Lossy — two different days could collapse to the same truncated `record_id`, making the audit trail ambiguous. |
| C. Hash the identifier (e.g. SHA-256 hex) | ❌ Breaks the audit UI (`app/audit/templates/audit/index.html:92` renders `entry.record_id` verbatim) and the `record_id` filter. Throws away readability for no benefit here. |
| D. Store PK parts in separate columns | ❌ Over-engineering for a single overflow. Would ripple into the audit route, template, and login-event writers (`app/auth/routes.py:30-31` also write `record_id`). |
| E. Add a length cap in `_record_identifier` | ⚠️ Acceptable only *in addition to* A as defense-in-depth; not a substitute, because it re-introduces truncation ambiguity. |

**Recommendation: Option A**, plus one robustness guard so an audit-writer bug can never
again take down a user's business transaction (see "Defense-in-depth" below).

## Recommended fix (ready to apply)

### 1. Model — `app/models/auth.py:51`

```python
    record_id = db.Column(db.String(255), nullable=False, index=True)
```

This is what fixes dev/test (tables are created from the models via the startup
`db.create_all()` fallback, not the migration).

### 2. Migration — new file, chaining off the current head `add_note_answer_table`

`migrations/versions/widen_record_audit_record_id.py`:

```python
"""widen record_audit.record_id to fit composite-key audit identifiers

Production context (2026-10-13): saving the Monthly Diary via POST /diary/bulk
failed with psycopg.errors.StringDataRightTruncation "value too long for type
character varying(50)". WorkDiaryEntry has a composite PK (fso_name, work_date,
duty_seq); the after_flush audit hook (app/audit_hooks.py::_record_identifier)
renders it as "fso_name=...|work_date=...|duty_seq=..." which is up to ~142
chars, overflowing the VARCHAR(50) column. PostgreSQL enforces the length
(SQLite, used in dev/test, does not — which is why CI never caught it).

Revision ID: widen_record_audit_record_id
Revises: add_note_answer_table
Create Date: 2026-10-13
"""

from alembic import op
import sqlalchemy as sa

revision = "widen_record_audit_record_id"
down_revision = "add_note_answer_table"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("record_audit", schema=None) as batch_op:
        batch_op.alter_column(
            "record_id",
            existing_type=sa.String(length=50),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("record_audit", schema=None) as batch_op:
        batch_op.alter_column(
            "record_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=50),
            existing_nullable=False,
        )
```

`batch_alter_table` handles the SQLite table-rebuild automatically; on PostgreSQL it
emits a native `ALTER COLUMN ... TYPE VARCHAR(255)`. The downgrade is best-effort — it
will fail if any row now exceeds 50 chars, which is the correct behavior (you cannot
safely narrow a column that has grown).

### 3. Defense-in-depth — make the audit hook non-fatal to the business write

The truncation was invisible until it took down a diary save. The login-event writers in
`app/auth/routes.py:24-59` already wrap their audit insert in `try/except` + rollback with
the comment "Audit logging must never break the login flow." The `after_flush` hook
(`app/audit_hooks.py`) has **no** such guard. However, because `after_flush` runs inside the
flush and the `RecordAudit` is committed with the user's transaction, you cannot cleanly
swallow mid-flush. The durable protection is:

- Cap the identifier length in `_record_audit` so it can never exceed the column — but do it
  **safely** (truncate only as a last resort), and
- Rely on the widened column (255) as the primary fix so the cap is essentially never hit.

A minimal cap in `_record_audit` (after `record_id=str(record_id)`):

```python
    rid = str(record_id)
    if len(rid) > 255:
        # Should not happen with a 255-wide column; guard anyway so an audit
        # identifier can never truncate-fail the caller's transaction.
        rid = rid[:255]
```

(Leave the widening as the real fix; the cap is a seatbelt, not the brake.)

### 4. Tests to update / add

- **Update the sole-head pin.** `tests/test_migration_note_answer.py::test_migration_is_the_sole_head`
  asserts `script.get_heads() == ["add_note_answer_table"]`. After adding the migration it must
  become `== ["widen_record_audit_record_id"]` (or, better, follow the durable pattern used in
  `tests/test_migration_rcm_nullable.py::test_single_alembic_head`, which only asserts
  `len(get_heads()) == 1`).
- **Add `tests/test_migration_record_audit_record_id.py`** mirroring
  `test_migration_note_answer.py`: run `upgrade` on a scratch SQLite DB with a `record_audit`
  table pre-created at `VARCHAR(50)`, assert the column type widens, and assert `downgrade`
  restores `VARCHAR(50)`.
- **Add a regression test for the actual overflow string.** In `tests/test_schedule_planner.py`,
  extend the audit-row test (or add a new one) to write a diary row for a **long** FSO name
  (e.g. `"Suman Kumar Saha"` / 16 chars → `record_id` ≈ 57 chars) and assert the audit row is
  created. Because SQLite won't enforce the width, assert on the string explicitly:

  ```python
  assert len(audit.record_id) <= 255
  assert "fso_name=Suman Kumar Saha" in audit.record_id
  ```

  Note in a comment that this test is a *proxy* for the Postgres-only truncation error and
  cannot reproduce the exact 500 on SQLite.

## Primary sources read

- `app/audit_hooks.py` — `_record_identifier()` (116-135), `_record_audit()` (69-82), `_after_flush` (172-202)
- `app/models/auth.py:36-61` — `RecordAudit` model (`record_id = db.String(50)`)
- `app/models/diary.py:24-41` — `WorkDiaryEntry` composite PK `(fso_name, work_date, duty_seq)`
- `app/diary.py:356-452` — `bulk()` route; `db.session.commit()` that the audit error aborts
- `app/audit/templates/audit/index.html:90-104` — audit UI rendering `record_id` (why Options C/D hurt)
- `app/auth/routes.py:24-59` — existing "audit must never break the flow" try/except pattern
- `migrations/versions/f2203965fa39_add_record_audit_table.py` — original `VARCHAR(50)` schema
- `migrations/versions/add_note_answer_table.py`, `tests/test_migration_note_answer.py` — current head + sole-head test pattern
- `migrations/versions/add_diary_duty_seq.py`, `tests/test_migration_rcm_nullable.py` — `batch_alter_table` / single-head idioms
- `tests/conftest.py:20` — SQLite test DB (root reason the bug escaped CI)

