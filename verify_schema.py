import os
import sqlite3

db_path = os.path.join("instance", "app.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fbo_issue'")
result = cursor.fetchone()
if result:
    pass
else:
    pass

cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='fbo_issue_audit'")
result = cursor.fetchone()
if result:
    pass
else:
    pass

cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='fbo_issue'")
for _row in cursor.fetchall():
    pass

cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='fbo_issue_audit'")
for _row in cursor.fetchall():
    pass

cursor.execute("PRAGMA table_info(fbo_issue)")
for _row in cursor.fetchall():
    pass

conn.close()
