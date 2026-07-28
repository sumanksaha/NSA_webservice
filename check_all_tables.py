import os
import sqlite3

db_path = os.path.join("instance", "app.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print("ALL TABLES:", tables)

for table in tables:
    cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
    schema = cursor.fetchone()
    if schema:
        print(f"\n{table}:")
        print(schema[0])

conn.close()
