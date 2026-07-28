import glob
import os
import sqlite3

print("=== ALEMBIC MIGRATION FILES (in order) ===")
migration_files = sorted(glob.glob("C:\\github\\NSA_webservice\\migrations\\versions\\*.py"))
for f in migration_files:
    print(f"  {os.path.basename(f)}")

print("\n=== ACTUAL TABLES IN DATABASE ===")
db_path = os.path.join("C:\\github\\NSA_webservice\\instance", "app.db")
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
if "case_files" in tables:
    print("  case_files EXISTS in database")
else:
    print("  case_files MISSING from database")
