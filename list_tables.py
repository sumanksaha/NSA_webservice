import os
import sqlite3

db_path = os.path.join("C:\\github\\NSA_webservice\\instance", "app.db")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# List all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = cursor.fetchall()
for _t in tables:
    pass

conn.close()
