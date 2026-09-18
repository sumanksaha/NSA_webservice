"""Database bootstrap for application startup.

Extracted from ``app/__init__.py::create_app`` (2026-09-12 review: the
factory was a 500-line function doing blueprint wiring, config, and DB
bootstrap in one body). This module owns everything needed to make a
possibly-fresh or partially-migrated database usable at boot:

1. Fallback ``db.create_all()`` when core tables are missing.
2. Alembic ``stamp head`` on a truly fresh database.
3. Schema self-heal for mid-chain migrations Alembic can never replay.
4. FTS5 search virtual table creation (SQLite no-op on PostgreSQL).
5. Default admin account seed on first boot.

Behaviour is intentionally identical to the previous inline block — this is
a move, not a rewrite.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

from app.extensions import db


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
