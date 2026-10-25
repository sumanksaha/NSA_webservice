"""Tests for the ``widen_record_audit_record_id`` migration.

Production context (2026-10-13): saving a Monthly Diary failed with a 500 on
``POST /diary/bulk`` because ``record_audit.record_id`` is ``VARCHAR(50)``
while ``WorkDiaryEntry``'s composite-key audit identifier
(``fso_name=...|work_date=...|duty_seq=...``) routinely exceeds 50 chars
(e.g. ``fso_name=Suman Saha|work_date=2026-10-01|duty_seq=1`` is 51; the
worst case is ~142). The audit row shares the diary save's transaction, so
the ``StringDataRightTruncation`` rolled back the officer's legitimate edits.

This migration widens the column to ``VARCHAR(255)``. Follows the scratch-DB
Operations-context pattern of ``tests/test_migration_rcm_nullable.py`` — no
running server, no production database touched.
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


@pytest.fixture
def old_shape_db(tmp_path):
    """Scratch DB mimicking production: ``record_id`` is ``VARCHAR(50)``."""
    path = tmp_path / "prodlike.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE record_audit (id INTEGER NOT NULL PRIMARY KEY, record_id VARCHAR(50) NOT NULL)",
    )
    con.commit()
    con.close()
    return path


def _record_id_type(path) -> str:
    con = sqlite3.connect(path)
    try:
        for row in con.execute("PRAGMA table_info(record_audit)"):
            if row[1] == "record_id":
                return row[2]
    finally:
        con.close()
    raise AssertionError("column record_id missing")


def _run(path, fn_name: str):
    from migrations.versions import widen_record_audit_record_id as mig

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mig, fn_name)()


class TestWidenRecordAuditRecordIdMigration:
    def test_upgrade_widens_record_id(self, old_shape_db):
        assert _record_id_type(old_shape_db) == "VARCHAR(50)"
        _run(old_shape_db, "upgrade")
        assert _record_id_type(old_shape_db) == "VARCHAR(255)"

    def test_downgrade_restores_record_id(self, old_shape_db):
        """Upgrade then downgrade returns the column to ``VARCHAR(50)``.

        Verifies the downgrade's happy path on a scratch DB. It does NOT
        prove the docstring's "fails loudly if a row now exceeds 50 chars"
        property: SQLite does not enforce ``VARCHAR(n)``, so the narrowing
        would not raise here. On Postgres it would — the docstring documents
        that contract, this test only pins the schema round-trip.
        """
        _run(old_shape_db, "upgrade")
        _run(old_shape_db, "downgrade")
        assert _record_id_type(old_shape_db) == "VARCHAR(50)"

    def test_migration_chains_off_the_previous_head(self):
        from migrations.versions import widen_record_audit_record_id as mig

        assert mig.revision == "widen_record_audit_record_id"
        assert mig.down_revision == "add_note_answer_table"

    def test_migration_is_the_sole_head(self):
        """``flask db upgrade`` on deploy only applies a migration when it is
        the single head; a second head would silently skip this one."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        cfg = Config()
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["add_evidence_assessment"]
