"""Tests for the boot-time required-columns schema check (db_bootstrap).

Regression coverage for the production ``GET /case_file_generator/`` 500
(``sqlalchemy.exc.ProgrammingError``, error code f405): the index query
filters on ``case_files.is_archived``, which only exists once migration
``add_archive_columns_to_cases`` has applied. A database stamped at
migration head but missing those columns boots fine and then 500s on
every case-list page, because:

- ``apply_archive_filter`` guards on ``hasattr(model, ...)`` (the model
  class always has the attribute — never the actual table), and
- ``bootstrap_database`` self-heals missing *tables* but never missing
  *columns*, while ``flask db upgrade`` is a no-op at head.

The fail-loud check in ``bootstrap_database`` must refuse to boot such a
database with a remediation message instead of serving 500s.
"""

from __future__ import annotations

import sqlite3

import pytest


def _write_legacy_db(path: str, *, with_archive_columns: bool) -> None:
    """Create a minimal prod-like database file.

    ``fso`` exists so the ``create_all`` fallback is skipped (exactly like
    production, where the tables exist but predate the archive migration);
    ``case_files`` / ``adjudications`` are created with or without the
    archive columns.
    """
    archive_cols = (
        ", is_archived BOOLEAN NOT NULL DEFAULT 0, archived_at DATETIME"
        if with_archive_columns
        else ""
    )
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE fso (id INTEGER PRIMARY KEY, name VARCHAR(100))")
        conn.execute(f"CREATE TABLE case_files (id INTEGER PRIMARY KEY{archive_cols})")
        conn.execute(f"CREATE TABLE adjudications (id INTEGER PRIMARY KEY{archive_cols})")
        # Present but drifted: the inspection half of the check is exercised
        # only when the table exists (missing tables are owned by create_all).
        conn.execute("CREATE TABLE inspection (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def boot_env(monkeypatch):
    """Deterministic boot: skip seeds/syncs unrelated to the schema check."""
    monkeypatch.setenv("SKIP_ADMIN_SEED", "1")
    monkeypatch.setenv("SKIP_FSO_STARTUP_SYNC", "1")
    monkeypatch.setenv("AUTO_RESTORE_ON_EMPTY_DB", "false")


def test_bootstrap_refuses_db_missing_archive_columns(tmp_path, boot_env):
    """Legacy schema (no is_archived/archived_at) must fail loud at boot."""
    from app import create_app

    db_path = str(tmp_path / "legacy.db")
    _write_legacy_db(db_path, with_archive_columns=False)

    with pytest.raises(RuntimeError, match="is_archived"):
        create_app(db_uri=f"sqlite:///{db_path}")


def test_bootstrap_error_names_table_and_remediation(tmp_path, boot_env, caplog):
    """The error must tell the operator which table/column and how to fix it."""
    import logging

    from app import create_app

    db_path = str(tmp_path / "legacy.db")
    _write_legacy_db(db_path, with_archive_columns=False)

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError) as exc_info:
        create_app(db_uri=f"sqlite:///{db_path}")
    message = str(exc_info.value)
    assert "case_files" in message
    assert "flask db upgrade" in message


def test_bootstrap_names_migration_less_column(tmp_path, boot_env):
    """Pins the production ``GET /case_file_generator/`` 500: a full schema
    minus the migration-less ``retailer_cum_manufacturer`` column must fail
    loud naming that column (not serve per-page 500s).
    """
    import sqlite3

    from app import create_app

    db_path = str(tmp_path / "norcm.db")
    create_app(db_uri=f"sqlite:///{db_path}")  # create_all: full current schema
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE case_files DROP COLUMN retailer_cum_manufacturer")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="retailer_cum_manufacturer"):
        create_app(db_uri=f"sqlite:///{db_path}")


def test_bootstrap_bypass_repairs_drifted_db(tmp_path, boot_env, monkeypatch):
    """The whole repair path depends on the bypass: a legacy DB must boot
    with ``SKIP_SCHEMA_CHECK=1`` so `flask db upgrade` can run against it.
    """
    from app import create_app

    db_path = str(tmp_path / "legacy.db")
    _write_legacy_db(db_path, with_archive_columns=False)
    monkeypatch.setenv("SKIP_SCHEMA_CHECK", "1")

    app = create_app(db_uri=f"sqlite:///{db_path}")
    assert app is not None


def test_bootstrap_boots_when_columns_present(tmp_path, boot_env):
    """A database whose tables already exist with full columns boots
    normally — the check must not false-positive on the tables-exist path.
    """
    from app import create_app

    db_path = str(tmp_path / "migrated.db")
    create_app(db_uri=f"sqlite:///{db_path}")  # create_all: full current schema

    app = create_app(db_uri=f"sqlite:///{db_path}")
    assert app is not None


def test_bootstrap_boots_fresh_database(tmp_path, boot_env):
    """Empty database (create_all path) boots normally."""
    from app import create_app

    db_path = str(tmp_path / "fresh.db")

    app = create_app(db_uri=f"sqlite:///{db_path}")
    assert app is not None


def _admin_count(db_path: str) -> int:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    finally:
        conn.close()


def test_bootstrap_sequential_boots_idempotent(tmp_path, monkeypatch):
    """N sequential boots: all succeed, exactly one admin row.

    Guards the 2026-10-12 flake family from the single-process side — a
    second boot must observe the first boot's schema, never a stale
    snapshot (``no such table``) nor re-create (``already exists``).
    Seeding stays ON here (unlike boot_env) because exactly-once admin is
    part of the assertion.
    """
    monkeypatch.delenv("SKIP_ADMIN_SEED", raising=False)

    from app import create_app

    db_path = str(tmp_path / "seq.db")
    for _ in range(3):
        assert create_app(db_uri=f"sqlite:///{db_path}") is not None
    assert _admin_count(db_path) == 1


def test_bootstrap_concurrent_boots_single_admin(tmp_path, monkeypatch):
    """Concurrent boots (web + worker shape): all succeed, exactly one admin.

    Threads share the process but take separate connections — the SQLite
    equivalent of two gunicorn workers racing create_all/seed. The file lock
    serialises them; without it this flakes with duplicate-CREATE or
    missing-table errors (the 2026-10-12 P1).
    """
    import concurrent.futures

    monkeypatch.delenv("SKIP_ADMIN_SEED", raising=False)

    from app import create_app

    db_path = str(tmp_path / "race.db")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        apps = list(ex.map(lambda _: create_app(db_uri=f"sqlite:///{db_path}"), range(4)))
    assert all(a is not None for a in apps)
    assert _admin_count(db_path) == 1


def test_lazy_import_skips_module_boot():
    """APP_LAZY_IMPORT=true: importing app must not boot the real database.

    Runs in a subprocess so the module-level guard is evaluated fresh (the
    parent process has app imported already). No DB is touched — the assertion
    is only that no `app` instance is created at import time.
    """
    import subprocess
    import sys

    code = (
        "import os; os.environ['APP_LAZY_IMPORT']='true';"
        "import app;"
        "print('LAZY_OK' if not hasattr(app, 'app') else 'LAZY_BOOT')"
    )
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=240)
    assert "LAZY_OK" in p.stdout, p.stdout + p.stderr
