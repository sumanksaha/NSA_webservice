"""Pytest session bootstrap.

The ``app`` package runs ``create_app()`` at module-import time, which binds
the SQLAlchemy engine to the default dev DB (``instance/app.db``) and creates
tables via the startup fallback. Test fixtures that later override
``SQLALCHEMY_DATABASE_URI`` to ``sqlite:///:memory:`` do NOT take effect,
so tests silently read/write the developer's DB — and fail with schema drift
when the dev DB predates model changes (e.g. ``user.is_admin``).

Setting ``DATABASE_URL`` here, before any test module imports ``app``, pins
the whole session to an isolated temp SQLite DB instead. ``load_dotenv`` does
not override an already-set env var, so this cannot be clobbered.
"""

import os
import tempfile
from pathlib import Path

_TEST_DB_DIR = tempfile.mkdtemp(prefix="nsa_test_db_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_TEST_DB_DIR) / 'test.db'}"
os.environ["SKIP_FSO_STARTUP_SYNC"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"  # noqa: S105
