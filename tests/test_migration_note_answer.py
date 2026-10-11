"""Tests for the ``add_note_answer_table`` migration.

The migration is the Phase 1 schema change of
``docs/NOTEPAD_LEGAL_RAG_SPEC.md``: the append-only ``note_answer`` table
backing the legal-RAG Q&A thread. Follows the scratch-DB Operations-context
pattern of ``tests/test_migration_rcm_nullable.py`` — no running server, no
production database touched.
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

_COLUMNS = {
    "id",
    "note_id",
    "question",
    "answer",
    "citations",
    "note_snapshot",
    "groundedness_score",
    "hallucination_detected",
    "degraded",
    "provider_model",
    "token_usage",
    "rag_log_id",
    "created_at",
}


def _run(path, fn_name: str):
    from migrations.versions import add_note_answer_table as mig

    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(mig, fn_name)()


def _columns(path):
    con = sqlite3.connect(path)
    try:
        return {row[1]: row for row in con.execute("PRAGMA table_info(note_answer)")}
    finally:
        con.close()


class TestNoteAnswerMigration:
    def test_upgrade_creates_table_with_spec_columns(self, tmp_path):
        path = tmp_path / "scratch.db"
        _run(path, "upgrade")
        cols = _columns(path)
        assert set(cols) == _COLUMNS

    def test_not_null_matches_the_model(self, tmp_path):
        path = tmp_path / "scratch.db"
        _run(path, "upgrade")
        cols = _columns(path)
        for required in ("note_id", "question", "answer", "degraded"):
            assert cols[required][3] == 1, f"{required} must be NOT NULL"
        for optional in ("citations", "note_snapshot", "groundedness_score", "rag_log_id"):
            assert cols[optional][3] == 0, f"{optional} must be nullable"

    def test_note_id_fk_cascades_on_delete(self, tmp_path):
        path = tmp_path / "scratch.db"
        _run(path, "upgrade")
        con = sqlite3.connect(path)
        try:
            fks = list(con.execute("PRAGMA foreign_key_list(note_answer)"))
        finally:
            con.close()
        assert any(fk[2] == "note" and fk[3] == "note_id" and fk[4] == "id" and fk[6] == "CASCADE" for fk in fks)

    def test_upgrade_creates_note_index(self, tmp_path):
        path = tmp_path / "scratch.db"
        _run(path, "upgrade")
        con = sqlite3.connect(path)
        try:
            indexes = {row[1] for row in con.execute("PRAGMA index_list(note_answer)")}
        finally:
            con.close()
        assert "idx_note_answer_note_id" in indexes

    def test_downgrade_drops_index_and_table(self, tmp_path):
        path = tmp_path / "scratch.db"
        _run(path, "upgrade")
        _run(path, "downgrade")
        con = sqlite3.connect(path)
        try:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        assert "note_answer" not in tables

    def test_migration_chains_off_the_previous_head(self):
        from migrations.versions import add_note_answer_table as mig

        assert mig.revision == "add_note_answer_table"
        assert mig.down_revision == "add_rag_answer_feedback"

    def test_migration_is_the_sole_head(self):
        """``flask db upgrade`` on deploy only applies a migration when it is
        the single head; a second head would silently skip this one."""
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        cfg = Config()
        cfg.set_main_option("script_location", str(repo_root / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == ["add_note_answer_table"]
