import os
import sqlite3

db_path = os.path.join("C:\\github\\NSA_webservice\\instance", "app.db")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check if sample table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sample'")
table_exists = cursor.fetchone()

if not table_exists:
    pass
else:
    # Get table info
    cursor.execute("PRAGMA table_info(sample)")
    columns = cursor.fetchall()

    for col in columns:
        col_name, col_type, not_null, default_val, pk = col
        flag = "NOT NULL" if not_null else "NULL"

    # Check for NOT NULL on sample_type
    sample_type_col = (
        next(c for c in columns if c[1] == "sample_type") if any(c[1] == "sample_type" for c in columns) else None
    )
    if sample_type_col and sample_type_col[2] == 1:
        pass
    else:
        pass

    # Check for CHECK constraints
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sample'")
    create_sql = cursor.fetchone()
    if create_sql:
        sql = create_sql[0]
        if "CHECK" in sql and "sample_type" in sql:
            pass
        else:
            pass

conn.close()
