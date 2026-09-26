"""Tests for the ``allow_null_rcm_fields`` migration.

Production context (2026-09-26): saving an RCM (loose-food) case file
failed with ``NotNullViolation: null value in column "mfg_date"``. The
model declares the RCM-exempt columns nullable and the app legitimately
stores NULL there, but the production schema (built from the
``add_missing_base_tables`` baseline) still enforced NOT NULL and no
migration ever relaxed it. This migration aligns the schema with the
model.
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from alembic.config import Config


@pytest.fixture()
def old_shape_db(tmp_path):
    """Scratch DB mimicking production: RCM-exempt columns NOT NULL."""
    path = tmp_path / "prodlike.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE case_files ("
        "id INTEGER PRIMARY KEY, "
        "batch_no VARCHAR(100) NOT NULL, "
        "mfg_date DATETIME NOT NULL, "
        "expiry_date DATETIME NOT NULL, "
        "manufacturer_report_receive_date DATETIME NOT NULL)"
    )
    con.commit()
    con.close()
    return path


def _notnull(path, column) -> int:
    con = sqlite3.connect(path)
    try:
        for row in con.execute("PRAGMA table_info(case_files)"):
            if row[1] == column:
                return row[3]
    finally:
        con.close()
    raise AssertionError(f"column {column} missing")


def _run(path, fn_name: str):
    from migrations.versions import allow_null_rcm_fields as mig

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mig, fn_name)()


class TestRcmNullableMigration:
    COLUMNS = ("mfg_date", "expiry_date", "manufacturer_report_receive_date", "batch_no")

    def test_upgrade_relaxes_not_null(self, old_shape_db):
        for col in self.COLUMNS:
            assert _notnull(old_shape_db, col) == 1
        _run(old_shape_db, "upgrade")
        for col in self.COLUMNS:
            assert _notnull(old_shape_db, col) == 0

    def test_upgraded_schema_accepts_rcm_nulls(self, old_shape_db):
        """The user scenario: an RCM row with NULL dates must insert."""
        _run(old_shape_db, "upgrade")
        con = sqlite3.connect(old_shape_db)
        try:
            con.execute(
                "INSERT INTO case_files (batch_no, mfg_date, expiry_date, "
                "manufacturer_report_receive_date) VALUES ('', NULL, NULL, NULL)"
            )
            con.commit()
            assert con.execute("SELECT COUNT(*) FROM case_files").fetchone()[0] == 1
        finally:
            con.close()

    def test_downgrade_restores_not_null(self, old_shape_db):
        _run(old_shape_db, "upgrade")
        _run(old_shape_db, "downgrade")
        for col in self.COLUMNS:
            assert _notnull(old_shape_db, col) == 1

    def test_single_head_is_rcm_nullable(self):
        """The new migration must remain the sole alembic head so
        ``flask db upgrade`` on deploy actually applies it."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        cfg = Config()
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["allow_null_rcm_fields"]
