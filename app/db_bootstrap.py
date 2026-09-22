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

Behaviour is intentionally identical to the previous inline block — this is
a move, not a rewrite.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

from app.extensions import db


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
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE adjudications ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITHOUT TIME ZONE;
CREATE INDEX IF NOT EXISTS idx_adjudications_is_archived ON adjudications (is_archived);
ALTER TABLE inspection ADD COLUMN IF NOT EXISTS auditor_plan_json TEXT;
ALTER TABLE inspection ADD COLUMN IF NOT EXISTS dossier_verified BOOLEAN NOT NULL DEFAULT FALSE;"""

#: Same repair for local SQLite (no ``IF NOT EXISTS`` support on
#: ``ADD COLUMN`` there — remove any line for a column already present).
_MANUAL_REPAIR_SQL_SQLITE = """\
ALTER TABLE case_files ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE case_files ADD COLUMN archived_at DATETIME;
ALTER TABLE case_files ADD COLUMN retailer_cum_manufacturer BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE adjudications ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE adjudications ADD COLUMN archived_at DATETIME;
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
        f"the columns manually: {manual_sql}"
    )


def bootstrap_database(app: Flask) -> None:
    """Bring the database up to a bootable state inside ``app``'s context."""
    # Import models so they're registered with SQLAlchemy metadata.
    from app import models  # noqa: F401

    with app.app_context():
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect

        engine = create_engine(app.config["SQLALCHEMY_DATABASE_URI"])
        from app.guard_rail import install_guard

        install_guard(engine)
        inspector = sa_inspect(engine)
        if "fso" not in inspector.get_table_names():
            db.create_all()
            app.logger.info("Created missing tables via db.create_all() fallback")
            # Only stamp when there is NO migration history at all — never
            # clobber a partially-migrated database.
            if "alembic_version" not in inspector.get_table_names():
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
                if "alembic_version" in inspector.get_table_names():
                    with engine.connect() as conn:
                        db_version = conn.execute(
                            text("SELECT version_num FROM alembic_version"),
                        ).scalar()
                    if db_version and db_version == ScriptDirectory.from_config(alembic_cfg).get_current_head():
                        before = set(inspector.get_table_names())
                        # Concurrent boots (web + Celery worker) may both reach
                        # this; create_all only adds genuinely missing tables and
                        # a duplicate-CREATE race is caught below (non-fatal).
                        db.create_all()
                        created = sorted(set(sa_inspect(engine).get_table_names()) - before)
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

            default_admin = User(
                username="admin",
                password_hash=generate_password_hash("admin123"),
                is_admin=True,
            )
            db.session.add(default_admin)
            db.session.commit()
            app.logger.info("Default admin account created (username=admin). Change the password after first login.")
