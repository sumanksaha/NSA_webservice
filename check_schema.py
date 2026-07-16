import sqlite3
import sys

db_path = 'C:\\github\\NSA_webservice\\instance\\app.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get schema for fbo_issue
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fbo_issue'")
result = cursor.fetchone()
if result:
    print("=== SCHEMA: fbo_issue ===")
    print(result[0])
else:
    print("Table fbo_issue not found")

# Get schema for fbo_issue_audit
cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fbo_issue_audit'")
result = cursor.fetchone()
if result:
    print("\n=== SCHEMA: fbo_issue_audit ===")
    print(result[0])
else:
    print("Table fbo_issue_audit not found")

# Get triggers
cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
triggers = cursor.fetchall()
print("\n=== TRIGGERS ===")
for t in triggers:
    print(t[0])

# Get indexes for fbo_issue
cursor.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='fbo_issue'")
indexes = cursor.fetchall()
print("\n=== INDEXES: fbo_issue ===")
for idx in indexes:
    print(idx[0])

# Get indexes for fbo_issue_audit
cursor.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='fbo_issue_audit'")
indexes = cursor.fetchall()
print("\n=== INDEXES: fbo_issue_audit ===")
for idx in indexes:
    print(idx[0])

conn.close()
