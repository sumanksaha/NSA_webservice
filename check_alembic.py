import os
import sys
os.chdir('C:\\github\\NSA_webservice')
sys.path.insert(0, 'C:\\github\\NSA_webservice')

from app import create_app
from flask_migrate import Migrate
import sqlite3

app = create_app()
migrate = Migrate(app, app.extensions['db'])

# Get alembic version info
from alembic.config import Config
from alembic import command
from io import StringIO
import contextlib

alembic_cfg = Config('migrations/alembic.ini')
alembic_cfg.set_main_option('script_location', 'migrations')

print("=== ALEMBIC MIGRATION FILES (in order) ===")
import glob
migration_files = sorted(glob.glob('migrations/versions/*.py'))
for f in migration_files:
    print(f"  {os.path.basename(f)}")

print("\n=== ACTUAL TABLES IN DATABASE ===")
db_path = os.path.join('C:\\github\\NSA_webservice\\instance', 'app.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in cursor.fetchall()]
for t in tables:
    print(f"  {t}")

print("\n=== ALEMBIC VERSION TABLE ===")
cursor.execute("SELECT * FROM alembic_version")
versions = cursor.fetchall()
if versions:
    for v in versions:
        print(f"  {v}")
else:
    print("  (empty - no migrations applied)")

conn.close()

print("\n=== CHECKING FOR case_files TABLE ===")
if 'case_files' in tables:
    print("  case_files EXISTS in database")
else:
    print("  case_files MISSING from database")

# Try to get current and heads
print("\n=== ALEMBIC CURRENT ===")
try:
    with contextlib.redirect_stdout(StringIO()) as f:
        command.current(alembic_cfg)
    output = f.getvalue()
    print(output if output else "  (no output or error)")
except Exception as e:
    print(f"  Error: {e}")

print("\n=== ALEMBIC HEADS ===")
try:
    with contextlib.redirect_stdout(StringIO()) as f:
        command.heads(alembic_cfg, verbose=True)
    output = f.getvalue()
    print(output if output else "  (no output or error)")
except Exception as e:
    print(f"  Error: {e}")
