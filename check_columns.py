import sqlite3

conn = sqlite3.connect('C:\\github\\NSA_webservice\\instance\\app.db')
cursor = conn.cursor()

cursor.execute("PRAGMA table_info(case_files)")
columns = [row[1] for row in cursor.fetchall()]
print("case_files columns:", columns)
print("Has applicable_sections:", "applicable_sections" in columns)

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print("Tables:", tables)

conn.close()
