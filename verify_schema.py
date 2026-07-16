import sqlite3
import os

db_path = os.path.join('instance', 'app.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=== SCHEMA: fbo_issue ===")
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fbo_issue'")
result = cursor.fetchone()
if result:
    print(result[0])
else:
    print("TABLE NOT FOUND")

print("\n=== SCHEMA: fbo_issue_audit ===")
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fbo_issue_audit'")
result = cursor.fetchone()
if result:
    print(result[0])
else:
    print("TABLE NOT FOUND")

print("\n=== INDEXES: fbo_issue ===")
cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='fbo_issue'")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]}")

print("\n=== INDEXES: fbo_issue_audit ===")
cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='fbo_issue_audit'")
for row in cursor.fetchall():
    print(f"{row[0]}: {row[1]}")

print("\n=== CHECK CONSTRAINTS: fbo_issue ===")
cursor.execute("PRAGMA table_info(fbo_issue)")
for row in cursor.fetchall():
    print(row)

conn.close()
