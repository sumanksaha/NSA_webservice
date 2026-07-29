import os
import sqlite3

db_path = os.path.join("C:\\github\\NSA_webservice\\instance", "app.db")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get the CREATE TABLE statement
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sample'")
create_sql = cursor.fetchone()
if create_sql:
    if "CHECK" in create_sql[0] and "sample_type" in create_sql[0]:
        pass
    else:
        pass

# Also check the constraints
cursor.execute("PRAGMA table_info(sample)")
columns = cursor.fetchall()
for _col in columns:
    pass

conn.close()
