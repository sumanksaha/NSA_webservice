import sqlite3
import os

db_path = os.path.join(os.getcwd(), 'instance', 'app.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print("Existing tables:", tables)

# Get schema for each table
for table in tables:
    cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
    schema = cursor.fetchone()
    if schema:
        print(f"\n{table}: {schema[0]}")

conn.close()
