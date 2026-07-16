import sqlite3

db_path = 'C:\\github\\NSA_webservice\\instance\\app.db'
backup_path = 'C:\\github\\NSA_webservice\\instance\\app.db.backup_20260716_184718'

print("=== TABLES IN MAIN DB ===")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
for t in tables:
    print(t[0])

print("\n=== ROW COUNTS IN MAIN DB ===")
for t in tables:
    table_name = t[0]
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f"{table_name}: {count}")

conn.close()

print("\n=== TABLES IN BACKUP DB ===")
conn_backup = sqlite3.connect(backup_path)
cursor_backup = conn_backup.cursor()
cursor_backup.execute("SELECT name FROM sqlite_master WHERE type='table'")
backup_tables = cursor_backup.fetchall()
for t in backup_tables:
    print(t[0])

print("\n=== ROW COUNTS IN BACKUP DB ===")
for t in backup_tables:
    table_name = t[0]
    cursor_backup.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor_backup.fetchone()[0]
    print(f"{table_name}: {count}")

conn_backup.close()
