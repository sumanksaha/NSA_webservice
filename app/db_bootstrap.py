"""Database bootstrap for application startup.

Extracted from ``app/__init__.py::create_app`` (2026-09-12 review: the
factory was a 500-line function doing blueprint wiring, config, and DB
bootstrap in one body). This module owns everything needed to make a
possibly-fresh or partially-migrated database usable at boot:

1. Fallback ``db.create_all()`` when core tables are missing.
2. Alembic ``stamp head`` on a truly fresh database.
3. Schema self-heal for mid-chain migrations Alembic can never replay.
4. Fail-loud required-columns check (tables exist but a migration's
   columns never applied — ``flask db upgrade`` is a no-op at head).
5. FTS5 search virtual table creation (SQLite no-op on PostgreSQL).
6. Default admin account seed on first boot.
7. Mutual exclusion + read-your-writes: a cross-process file lock around
   the whole bootstrap and a fresh inspector per read, so concurrent boots
   (web + worker, overlapping harnesses) and stale reflection caches can no
   longer produce the 2026-10-12 flake family (``no such table`` vs
   ``already exists`` on healthy databases).

Behaviour is intentionally identical to the previous inline block — this is
a move, not a rewrite.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from flask import Flask

from app.extensions import db

logger = logging.getLogger(__name__)

#: How long a boot waits for a concurrent bootstrap to finish before
#: proceeding unlocked (and logging loudly). Boots are rare; correctness of
#: concurrent DDL beats boot latency. Override with DB_BOOTSTRAP_LOCK_TIMEOUT.
_LOCK_TIMEOUT_S = float(os.environ.get("DB_BOOTSTRAP_LOCK_TIMEOUT", "120"))


@contextlib.contextmanager
def _bootstrap_lock(db_uri: str):
    """Cross-process exclusive lock for the bootstrap, best-effort.

    Yields True when the lock is held. SQLite DDL (CREATE TABLE) from two
    processes against one file is exactly the 2026-10-12 flake family
    (``table X already exists`` vs ``no such table: X`` on healthy
    databases), so the whole bootstrap runs inside this. Non-file databases
    (:memory:, Postgres) and un-lockable paths yield False with a warning and
    keep today's behaviour — never block boot on the lock itself.
    """
    locked = False
    fh = None
    try:
        path = db_uri.split("sqlite:///", 1)[1] if "sqlite:///" in db_uri else ""
        path = path.split("?", 1)[0]
        if not path or path == ":memory:":
            locked = False
        else:
            lock_path = Path(path).with_suffix(Path(path).suffix + ".bootstrap.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(lock_path, "a+b")  # noqa: PTH123 — lockfile, not data
            deadline = time.monotonic() + _LOCK_TIMEOUT_S
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.2)
    except Exception as exc:  # pragma: no cover — lock infra failure
        logger.warning("Bootstrap lock unavailable (%s) — proceeding unlocked.", exc)
        locked = False
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
    # NOTE: no `except` around this yield — an exception from the bootstrap
    # body must propagate (fail-loud schema checks depend on it). Catching it
    # here and yielding again raises "generator didn't stop after throw()"
    # and swallows the original error (caught 2026-10-14: fail-loud tests
    # went green-on-broken).
    try:
        yield locked
    finally:
        try:
            if locked:
                try:
                    if os.name == "nt":
                        import msvcrt

                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            if fh is not None:
                fh.close()
        except Exception:
            pass


def _table_names(engine) -> set[str]:
    """Table names from a FRESH inspector, every call.

    SQLAlchemy inspectors cache reflection per object: reusing one inspector
    across ``create_all()`` reads back the pre-DDL snapshot, so a table
    created moments ago reports as missing (the ``no such table`` half of the
    2026-10-12 flake family). A new inspector per read costs one sqlite_master
    query and is always current.
    """
    from sqlalchemy import inspect as sa_inspect

    return set(sa_inspect(engine).get_table_names())


#: Tables whose mapped columns must exist on disk, not just on the model.
#: Expected columns are derived from model metadata (never hardcoded), so
#: both failure modes fail loud at boot instead of 500ing every read with
#: ``ProgrammingError`` f405 (``UndefinedColumn``):
#: (a) a migration never applied to a DB stamped at head (``flask db
#: upgrade`` is a silent no-op there), and (b) a model change shipped
#: without any migration (e.g. ``retailer_cum_manufacturer``, which broke
#: ``GET /case_file_generator/`` on every pre-existing database while
#: fresh ``create_all`` databases worked). Genuinely missing *tables* are
#: owned by ``create_all``/self-heal above and are skipped here.
def _required_table_models() -> dict[str, type]:
    from app import models

    return {
        "case_files": models.CaseFile,
        "adjudications": models.Adjudication,
        "inspection": models.Inspection,
        "work_diary": models.WorkDiaryEntry,
    }


#: Manual remediation (idempotent PostgreSQL) for the drift seen in the
#: wild: archive columns (``add_archive_columns_to_cases``), auditor
#: columns (``add_auditor_plan_to_inspection``), and the migration-less
#: ``retailer_cum_manufacturer`` (``add_retailer_cum_manufacturer`` covers
#: it going forward). Prefer ``flask db upgrade``; use this when the
#: version table is already at/above the revision that should have applied.
_MANUAL_REPAIR_SQL_PG = """\
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE;
CREATE INDEX IF NOT EXISTS idx_case_files_is_archived ON case_files (is_archived);
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS retailer_cum_manufacturer BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS is_unsafe BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE;
CREATE INDEX IF NOT EXISTS idx_adjudications_is_archived ON adjudications (is_archived);
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS case_submission_date TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS hearing_date TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE case_files ADD COLUMN IF NOT EXISTS penalty VARCHAR(200);
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS case_submission_date TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS hearing_date TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS penalty VARCHAR(200);
ALTER TABLE inspection ADD COLUMN IF NOT EXISTS auditor_plan_json TEXT;
ALTER TABLE inspection ADD COLUMN IF NOT EXISTS dossier_verified BOOLEAN NOT NULL DEFAULT FALSE;"""

#: Same repair for local SQLite (no ``IF NOT EXISTS`` support on
#: ``ADD COLUMN`` there — remove any line for a column already present).
_MANUAL_REPAIR_SQL_SQLITE = """\
ALTER TABLE case_files ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE case_files ADD COLUMN archived_at DATETIME;
ALTER TABLE case_files ADD COLUMN retailer_cum_manufacturer BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE case_files ADD COLUMN is_unsafe BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE adjudications ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE adjudications ADD COLUMN archived_at DATETIME;
ALTER TABLE case_files ADD COLUMN case_submission_date DATETIME;
ALTER TABLE case_files ADD COLUMN hearing_date DATETIME;
ALTER TABLE case_files ADD COLUMN penalty VARCHAR(200);
ALTER TABLE adjudications ADD COLUMN case_submission_date DATETIME;
ALTER TABLE adjudications ADD COLUMN hearing_date DATETIME;
ALTER TABLE adjudications ADD COLUMN penalty VARCHAR(200);
ALTER TABLE inspection ADD COLUMN auditor_plan_json TEXT;
ALTER TABLE inspection ADD COLUMN dossier_verified BOOLEAN NOT NULL DEFAULT 0;"""


def _verify_required_columns(app: Flask, engine) -> None:
    """Refuse to boot when a present table lacks model-mapped columns.

    Raises ``RuntimeError`` naming the table/columns and how to repair.
    Skip with ``SKIP_SCHEMA_CHECK=1`` (mirrors ``SKIP_DB_MIGRATION``).
    """
    from sqlalchemy import inspect as sa_inspect

    if os.environ.get("SKIP_SCHEMA_CHECK") == "1":
        app.logger.warning("SKIP_SCHEMA_CHECK=1 — skipping required-columns schema check")
        return
    inspector = sa_inspect(engine)
    tables = set(inspector.get_table_names())
    missing: dict[str, list[str]] = {}
    for table, model in _required_table_models().items():
        if table not in tables:
            continue
        expected = [col.name for col in model.__table__.columns]
        present = {col["name"] for col in inspector.get_columns(table)}
        absent = [col for col in expected if col not in present]
        if absent:
            missing[table] = absent
    if not missing:
        return
    detail = "; ".join(f"{table} missing {', '.join(cols)}" for table, cols in sorted(missing.items()))
    app.logger.error(
        "Schema check failed: %s. A migration never applied to this "
        "database, or the model changed without one (and `flask db upgrade` "
        "is a no-op once stamped at/above the revision).",
        detail,
    )
    manual_sql = _MANUAL_REPAIR_SQL_PG if engine.dialect.name != "sqlite" else _MANUAL_REPAIR_SQL_SQLITE
    raise RuntimeError(
        f"Database schema is missing required columns: {detail}. "
        "Repair with `SKIP_SCHEMA_CHECK=1 flask db upgrade` (the env var lets "
        "the pre-upgrade app boot far enough to run the migration), or apply "
        f"the columns manually: {manual_sql}",
    )


def bootstrap_database(app: Flask) -> None:
    """Bring the database up to a bootable state inside ``app``'s context."""
    # Import models so they're registered with SQLAlchemy metadata.
    from app import models  # noqa: F401

    with app.app_context():
        from sqlalchemy import create_engine

        from app.guard_rail import install_guard

        uri = app.config["SQLALCHEMY_DATABASE_URI"]
        app.logger.info("Bootstrap start (pid=%s, db=%s).", os.getpid(), _display_uri(uri))
        engine = create_engine(uri)
        install_guard(engine)
        with _bootstrap_lock(uri) as locked:
            if not locked:
                app.logger.warning(
                    "Bootstrap proceeding WITHOUT the mutual-exclusion lock — "
                    "concurrent boots may collide (see 2026-10-12 flake family)."
                )
            _bootstrap_locked(app, engine)
        app.logger.info("Bootstrap complete (pid=%s).", os.getpid())


def _display_uri(uri: str) -> str:
    """DB URI with any credentials redacted (logs must never carry secrets)."""
    try:
        from sqlalchemy.engine.url import make_url

        url = make_url(uri)
        if url.password:
            return str(url.set(password="***"))  # noqa: S106 — intentional credential redaction for logging, not a hardcoded password
    except Exception:
        pass
    return uri.split("?", 1)[0]


def _bootstrap_locked(app: Flask, engine) -> None:
    """The bootstrap steps; runs under the file lock (see bootstrap_database).

    Called with the app context already pushed. Uses a fresh inspector per
    read (see _table_names) — never the pre-DDL snapshot.
    """
    with app.app_context():
        if "fso" not in _table_names(engine):
            db.create_all()
            app.logger.info("Created missing tables via db.create_all() fallback")
            # Only stamp when there is NO migration history at all — never
            # clobber a partially-migrated database. Fresh read: create_all()
            # above just wrote, so a reused inspector would still report the
            # pre-DDL snapshot here.
            if "alembic_version" not in _table_names(engine):
                try:
                    from flask_migrate import stamp as alembic_stamp

                    alembic_stamp(revision="head")
                    app.logger.info("Stamped fresh database at migration head")
                except (Exception, SystemExit) as exc:
                    app.logger.warning(
                        "Could not stamp fresh database at migration head (%s) — "
                        "`flask db upgrade` may replay the full chain next deploy.",
                        exc,
                    )
            # Existing database — self-heal tables that `flask db upgrade`
            # can NEVER create: a migration inserted mid-chain (e.g. the
            # Phase 18 `a1b2c3d4e5f6` role/user_roles/comment migration) is an
            # ancestor of the DB's current version, so Alembic never replays it
            # and its tables stay missing (login crashed with
            # `relation "user_roles" does not exist`). create_all() is only
            # safe here when the DB is stamped at head — then no migration is
            # pending that could later collide with the created tables.
            try:
                from alembic.config import Config as AlembicConfig
                from alembic.script import ScriptDirectory
                from sqlalchemy import text

                migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
                alembic_cfg = AlembicConfig(str(migrations_dir / "alembic.ini"))
                alembic_cfg.set_main_option("script_location", str(migrations_dir))
                if "alembic_version" in _table_names(engine):
                    with engine.connect() as conn:
                        db_version = conn.execute(
                            text("SELECT version_num FROM alembic_version"),
                        ).scalar()
                    if db_version and db_version == ScriptDirectory.from_config(alembic_cfg).get_current_head():
                        before = _table_names(engine)
                        # The file lock above serialises concurrent boots (web +
                        # Celery worker); create_all only adds genuinely missing
                        # tables. Belt-and-braces: a duplicate-CREATE from a
                        # lock-less contender is caught by the except below
                        # (non-fatal warning, never a boot failure).
                        db.create_all()
                        created = sorted(_table_names(engine) - before)
                        if created:
                            app.logger.warning(
                                "Schema self-heal: created missing model tables %s (DB stamped at "
                                "migration head — `flask db upgrade` cannot replay mid-chain "
                                "insertions).",
                                created,
                            )
            except Exception as exc:
                app.logger.warning("Schema self-heal skipped: %s", exc)

        # Fail loud on tables that exist but lack migration-owned columns
        # (upgrade-at-head can never repair them — see module docstring).
        _verify_required_columns(app, engine)

        # Create FTS5 search virtual table on SQLite (no-op on PostgreSQL).
        # This runs unconditionally so the table exists even on a pre-existing
        # database that predates the search feature.
        from app.search.indexer import ensure_search_table

        ensure_search_table()

        # ------------------------------------------------------------------
        # Seed default admin account on first boot (empty user table).
        # Credentials: username=admin  password=admin123
        # The admin can change the password after first login via the
        # "Change password" button in the top-right corner.
        # ------------------------------------------------------------------
        from app.models import User

        try:
            _user_count = User.query.count()
        except Exception:  # pragma: no cover - pre-migration schema (flask db upgrade)
            # DB predates the current models; skip seeding so `flask db upgrade`
            # can boot and bring the schema up. Seeding happens on next boot.
            app.logger.warning("User table not queryable yet — skipping admin seed.")
            _user_count = 1

        if _user_count == 0 and not os.environ.get("SKIP_ADMIN_SEED"):
            from werkzeug.security import generate_password_hash

            from sqlalchemy.exc import IntegrityError, OperationalError

            default_admin = User(
                username="admin",
                password_hash=generate_password_hash("admin123"),
                is_admin=True,
            )
            db.session.add(default_admin)
            try:
                db.session.commit()
            except (IntegrityError, OperationalError) as exc:
                # Lost a race with a concurrent boot (duplicate admin) or the
                # table vanished under us (concurrent DDL where the file lock
                # was unavailable). Roll back and continue booting: the admin
                # either exists now (re-check below is unnecessary — login
                # reads it live) or the schema check above already failed loud
                # for a genuinely broken database.
                db.session.rollback()
                app.logger.warning(
                    "Admin seed collided (%s) — continuing boot; the account exists if a concurrent boot committed it.",
                    type(exc).__name__,
                )
            else:
                app.logger.info(
                    "Default admin account created (username=admin). Change the password after first login."
                )
