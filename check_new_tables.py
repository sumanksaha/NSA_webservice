import sqlite3

conn = sqlite3.connect('C:\\github\\NSA_webservice\\instance\\app.db')
cursor = conn.cursor()

for table in ['fbo_issue', 'fbo_issue_audit']:
    cursor.execute(f"PRAGMA table_info({table})")
    columns = cursor.fetchall()
    print(f"\n{table} columns:")
    for col in columns:
        print(f"  {col}")
    
    cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
    schema = cursor.fetchone()
    if schema:
        print(f"\n{table} schema:")
        print(f"  {schema[0]}")
    
    cursor.execute(f"PRAGMA index_list({table})")
    indexes = cursor.fetchall()
    print(f"\n{table} indexes:")
    for idx in indexes:
        print(f"  {idx}")

conn.close()
