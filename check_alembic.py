import os
import sys

os.chdir("C:\\github\\NSA_webservice")
sys.path.insert(0, "C:\\github\\NSA_webservice")

import sqlite3

from flask_migrate import Migrate

from app import create_app

app = create_app()
migrate = Migrate(app, app.extensions["db"])

# Get alembic version info
import contextlib
from io import StringIO

from alembic import command
from alembic.config import Config

alembic_cfg = Config("migrations/alembic.ini")
alembic_cfg.set_main_option("script_location", "migrations")

import glob

migration_files = sorted(glob.glob("migrations/versions/*.py"))
for f in migration_files:
    pass

db_path = os.path.join("C:\\github\\NSA_webservice\\instance", "app.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in cursor.fetchall()]
for _t in tables:
    pass

cursor.execute("SELECT * FROM alembic_version")
versions = cursor.fetchall()
if versions:
    for _v in versions:
        pass
else:
    pass

conn.close()

if "case_files" in tables:
    pass
else:
    pass

# Try to get current and heads
try:
    with contextlib.redirect_stdout(StringIO()) as f:
        command.current(alembic_cfg)
    output = f.getvalue()
except Exception:
    pass

try:
    with contextlib.redirect_stdout(StringIO()) as f:
        command.heads(alembic_cfg, verbose=True)
    output = f.getvalue()
except Exception:
    pass
