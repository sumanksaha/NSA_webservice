import glob
import os
import sqlite3

migration_files = sorted(glob.glob("C:\\github\\NSA_webservice\\migrations\\versions\\*.py"))
for _f in migration_files:
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
