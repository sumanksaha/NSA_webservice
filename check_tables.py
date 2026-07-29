import sqlite3

db_path = "C:\\github\\NSA_webservice\\instance\\app.db"
backup_path = "C:\\github\\NSA_webservice\\instance\\app.db.backup_20260716_184718"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
for t in tables:
    pass

for t in tables:
    table_name = t[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]

conn.close()

conn_backup = sqlite3.connect(backup_path)
cursor_backup = conn_backup.cursor()
cursor_backup.execute("SELECT name FROM sqlite_master WHERE type='table'")
backup_tables = cursor_backup.fetchall()
for t in backup_tables:
    pass

for t in backup_tables:
    table_name = t[0]
    cursor_backup.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor_backup.fetchone()[0]

conn_backup.close()
