"""Tests for the ``add_witness_name_case_file`` migration.

Context (2026-10-30): the petition named the local witness with a hardcoded
string in the template, so per-case witness names could not be recorded. The
column is additive and nullable so pre-existing cases keep all their data and
simply carry NULL (the petition then prints a fill-in line).
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.fixture
def old_shape_db(tmp_path):
    """Scratch DB shaped like a pre-witness production database."""
    path = tmp_path / "prodlike.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE case_files ("
        "id INTEGER PRIMARY KEY, "
        "case_number VARCHAR(100) NOT NULL, "
        "sample_code VARCHAR(100) NOT NULL)",
    )
    con.execute(
        "INSERT INTO case_files (id, case_number, sample_code) VALUES (1, '2026/FSS/1', 'SL/WB/1')",
    )
    con.commit()
    con.close()
    return path


def _columns(path) -> dict[str, tuple]:
    con = sqlite3.connect(path)
    try:
        return {row[1]: row for row in con.execute("PRAGMA table_info(case_files)")}
    finally:
        con.close()


def _run(path, fn_name: str):
    from migrations.versions import add_witness_name_case_file as mig

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mig, fn_name)()


class TestWitnessNameMigration:
    def test_upgrade_adds_optional_column(self, old_shape_db):
        assert "witness_name" not in _columns(old_shape_db)
        _run(old_shape_db, "upgrade")
        # PRAGMA row: (cid, name, type, notnull, dflt_value, pk)
        assert _columns(old_shape_db)["witness_name"][3] == 0

    def test_existing_cases_survive_with_null_witness(self, old_shape_db):
        """The user scenario: old rows must stay readable, witness NULL."""
        _run(old_shape_db, "upgrade")
        con = sqlite3.connect(old_shape_db)
        try:
            row = con.execute(
                "SELECT case_number, sample_code, witness_name FROM case_files",
            ).fetchone()
        finally:
            con.close()
        assert row == ("2026/FSS/1", "SL/WB/1", None)

    def test_upgrade_is_idempotent(self, old_shape_db):
        _run(old_shape_db, "upgrade")
        _run(old_shape_db, "upgrade")  # fresh create_all DBs already have it
        assert "witness_name" in _columns(old_shape_db)

    def test_downgrade_drops_column(self, old_shape_db):
        _run(old_shape_db, "upgrade")
        _run(old_shape_db, "downgrade")
        assert "witness_name" not in _columns(old_shape_db)

    def test_merge_revision_leaves_a_single_head(self):
        """``flask db upgrade`` refuses to run with two open heads, so the
        chain must resolve to one — and this migration must be on it."""
        from pathlib import Path

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        repo_root = Path(__file__).resolve().parent.parent
        cfg = Config()
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        assert len(heads) == 1, f"expected a single alembic head, got {heads}"
        revisions = {rev.revision for rev in script.iterate_revisions(heads[0], "base")}
        assert "add_witness_name_case_file" in revisions
